"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { formatHospitalTime } from "@/lib/clinical-format";
import {
  buildSavePayload,
  draftFromConsultation,
  hasUnsavedChanges,
  isEmptySave,
} from "@/lib/consultation-format";
import { CONSULTATION_SECTIONS } from "@/lib/diagnosis-format";
import type { ConsultationSection } from "@/lib/diagnosis-format";
import {
  bloodPressureText,
  compactGender,
  displayText,
  doctorLabel,
  vitalText,
} from "@/lib/doctor-format";
import type { ApiEnvelope, DoctorVisitDetail, DoctorVitals } from "@/types/doctor";
import type {
  ConsultationDraft,
  ConsultationNarrativeField,
  ConsultationSaveStatus,
  DoctorConsultation,
  DoctorConsultationResponse,
} from "@/types/doctor-consultation";
import { CONSULTATION_CONFLICT_CODE } from "@/types/doctor-consultation";

import { PriorityBadge, StageBadge } from "../doctor-badges";
import DoctorVitalsGrid from "../doctor-vitals-grid";
import DiagnosisWorkspace from "./diagnosis-workspace";
import ConsultationNoteEditor from "./note-editor";

/**
 * The active consultation workspace.
 *
 * MODE IS DERIVED, NOT DECLARED. This component renders only when the
 * AUTHORITATIVE visit state is `in_consultation`; the parent decides that from
 * the server payload. There is no client-side "consultation started" flag and
 * nothing that could disagree with Odoo about whether a consultation is open.
 *
 * THE DRAFT IS NEVER THE RECORD. `baseline` is the last thing the server
 * confirmed and `draft` is what the doctor has typed. Every successful save
 * replaces BOTH from the response, so the version token, the stored text and
 * the dirty markers all come from Odoo rather than from what this component
 * hoped it had written.
 *
 * THE HEIGHT MODEL, which the previous layout got wrong.
 * The grid cell owns the height, so this is `h-full min-h-0` and its chrome --
 * identity, vitals, section bar, triage context and the command bar -- is
 * `shrink-0`. Exactly ONE region scrolls: the note body. Previously the whole
 * panel scrolled, which pushed vitals and the complaint off screen while
 * writing and buried the save bar below the fold.
 */

/** One reading in the dense strip. Value leads, unit and label recede. */
function Stat({ label, value }: { label: string; value: string }) {
  const [reading, ...unit] = value.split(" ");
  return (
    <div className="flex min-w-0 items-baseline gap-1.5">
      <span className="shrink-0 text-[9px] font-bold uppercase tracking-[0.07em] text-slate-400">
        {label}
      </span>
      <span className="truncate text-[12.5px] font-bold leading-none tabular-nums text-slate-800">
        {reading}
        {unit.length ? (
          <span className="ml-0.5 text-[9px] font-semibold text-slate-400">
            {unit.join(" ")}
          </span>
        ) : null}
      </span>
    </div>
  );
}

/**
 * The six readings a doctor scans before writing, on one line.
 *
 * This replaces a two-tier card grid that occupied roughly a third of the
 * panel above the fold. The full grid is still one click away in the expanded
 * triage context; nothing was removed, it was demoted.
 */
function VitalsStrip({ vitals }: { vitals: DoctorVitals }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      <Stat label="BP" value={bloodPressureText(vitals)} />
      <Stat label="Pulse" value={vitalText(vitals.heart_rate, "bpm", 0)} />
      <Stat label="Temp" value={vitalText(vitals.temperature, "°C")} />
      <Stat label="Resp" value={vitalText(vitals.respiratory_rate, "/min", 0)} />
      <Stat label="SpO₂" value={vitalText(vitals.spo2, "%", 0)} />
      <Stat label="BMI" value={vitalText(vitals.bmi)} />
    </div>
  );
}

export default function ConsultationWorkspace({
  detail,
  loading,
  section,
  onSectionChange,
}: {
  detail: DoctorVisitDetail;
  loading: boolean;
  /*
    WHICH SECTION IS ON SCREEN. Presentation only, and OWNED BY THE PARENT so
    the Clinical Actions rail can focus a section too.

    This is NOT a workflow state machine and mirrors nothing on the server: the
    visit state and the consultation state still come from Odoo and still decide
    what may be written. Switching sections does not unmount the note draft, so
    a doctor can check the diagnosis list mid-sentence and come back to unsaved
    text exactly as they left it -- which is also why the command bar stays
    visible in every section.
  */
  section: ConsultationSection;
  onSectionChange: (section: ConsultationSection) => void;
}) {
  const appointmentId = detail.visit.appointment_id;

  const [consultation, setConsultation] = useState<DoctorConsultation | null>(null);
  const [draft, setDraft] = useState<ConsultationDraft>(() =>
    draftFromConsultation(null),
  );
  const [baseline, setBaseline] = useState<ConsultationDraft>(() =>
    draftFromConsultation(null),
  );

  const [noteLoading, setNoteLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [status, setStatus] = useState<ConsultationSaveStatus>("idle");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  /*
    Collapsed by default now that the vitals strip and the triage complaint are
    always visible in the chrome above. The expanded panel is the FULL nursing
    record -- every vital plus triage notes -- which a doctor consults
    deliberately rather than scrolls past on the way to the history.
  */
  const [contextOpen, setContextOpen] = useState(false);
  /** Client-side, presentation only: when this tab last saw a save succeed. */
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [savedPulse, setSavedPulse] = useState(false);

  /*
    NOTE ON CROSS-PATIENT SAFETY.

    The parent mounts this component with key={appointmentId}, so switching
    patients UNMOUNTS this instance and mounts a fresh one. That is what
    guarantees one patient's note can never appear in another's editor: there
    is no shared state to leak, and a late fetch resolving after unmount lands
    on a dead component and is discarded by React.
  */

  const applyServerRecord = useCallback((record: DoctorConsultation | null) => {
    const next = draftFromConsultation(record);
    setConsultation(record);
    setDraft(next);
    setBaseline(next);
  }, []);

  /* ---------------- load ---------------- */
  useEffect(() => {
    const controller = new AbortController();
    const target = appointmentId;

    async function loadNote() {
      setNoteLoading(true);
      setLoadError(null);
      setStatus("idle");
      setStatusMessage(null);
      try {
        const response = await fetch(
          `/api/doctor/visits/${target}/consultation`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorConsultationResponse>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setLoadError(
            messageFromPayload(payload, "Unable to load the consultation note."),
          );
          return;
        }
        applyServerRecord(payload.data.consultation);
        if (!payload.data.available) {
          setLoadError(payload.data.reason);
        }
      } catch {
        if (!controller.signal.aborted) {
          setLoadError("Unable to reach the consultation service.");
        }
      } finally {
        if (!controller.signal.aborted) {
          setNoteLoading(false);
        }
      }
    }

    void loadNote();
    return () => controller.abort();
  }, [appointmentId, applyServerRecord]);

  /*
    The per-field ✓ fades after a few seconds. Leaving every field permanently
    green would make "saved" the resting state of the whole form and therefore
    meaningless -- the indicator has to be an event, not a decoration.
  */
  useEffect(() => {
    if (!savedPulse) return;
    const timer = setTimeout(() => setSavedPulse(false), 2600);
    return () => clearTimeout(timer);
  }, [savedPulse]);

  const dirty = useMemo(
    () => hasUnsavedChanges(draft, baseline),
    [draft, baseline],
  );
  const editable = Boolean(consultation?.editable);

  const onFieldChange = useCallback(
    (field: ConsultationNarrativeField, value: string) => {
      setDraft((current) => ({ ...current, [field]: value }));
      // A previous outcome must not sit next to text that has since changed:
      // "Saved" above an edited paragraph is a false statement.
      setStatus((current) => (current === "saved" ? "idle" : current));
      setSavedPulse(false);
    },
    [],
  );

  /* ---------------- save ---------------- */
  const save = useCallback(async () => {
    if (!consultation || !editable) return;

    const payload = buildSavePayload(consultation.version, draft, baseline);
    if (isEmptySave(payload)) {
      setStatus("saved");
      setStatusMessage("No changes to save.");
      return;
    }

    setStatus("saving");
    setStatusMessage(null);
    const target = appointmentId;

    try {
      const response = await fetch(
        `/api/doctor/visits/${target}/consultation/save`,
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        },
      );
      const body = (await response.json()) as ApiEnvelope<DoctorConsultationResponse>;

      if (!response.ok || !body.success) {
        const code = body.success === false ? body.error.code : null;
        /*
          A CONFLICT IS NOT A RETRYABLE ERROR, and the UI must not let it look
          like one. Someone else's write landed after this tab read the note.
          Re-sending with a refreshed token would overwrite their paragraph,
          and merging free-text clinical narrative would fabricate a sentence
          neither clinician wrote. The only safe recovery is to reload and let
          a human reconcile, so the button below reloads rather than retries.
        */
        setStatus(code === CONSULTATION_CONFLICT_CODE ? "conflict" : "error");
        setStatusMessage(
          messageFromPayload(body, "Unable to save the consultation note."),
        );
        return;
      }

      /*
        The SERVER's record, not the draft that was posted. It carries the new
        version token, so the next save is chained correctly without a reload,
        and it is what makes the dirty markers honest.

        No queue or visit-detail refetch is triggered: a note changes no
        appointment state, no queue stage and nothing on the visit payload, so
        there is no divergence for a refresh to repair.
      */
      applyServerRecord(body.data.consultation);
      setStatus("saved");
      setStatusMessage(null);
      setSavedAt(new Date().toISOString());
      setSavedPulse(true);
    } catch {
      setStatus("error");
      setStatusMessage("Unable to reach the consultation service.");
    }
  }, [appointmentId, applyServerRecord, baseline, consultation, draft, editable]);

  const reload = useCallback(async () => {
    const target = appointmentId;
    setNoteLoading(true);
    try {
      const response = await fetch(`/api/doctor/visits/${target}/consultation`, {
        cache: "no-store",
      });
      const payload =
        (await response.json()) as ApiEnvelope<DoctorConsultationResponse>;
      if (response.ok && payload.success) {
        applyServerRecord(payload.data.consultation);
        setStatus("idle");
        setStatusMessage(null);
      }
    } catch {
      /* the banner already shown stays; nothing worse has happened */
    } finally {
      setNoteLoading(false);
    }
  }, [appointmentId, applyServerRecord]);

  const { patient, triage, medical_alerts: alerts, visit, encounter } = detail;
  const problem = status === "conflict" || status === "error";

  return (
    <section className="flex h-full min-h-[560px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      {/* ---- Identity: one dense line, not a card ---- */}
      <header className="shrink-0 border-b border-slate-200 bg-white px-3 py-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <span
              aria-hidden
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-[12px] font-bold text-emerald-800"
            >
              {patient.name.trim().charAt(0).toUpperCase() || "?"}
            </span>
            <h2 className="shrink-0 truncate text-[15px] font-bold leading-tight tracking-tight text-slate-950">
              {patient.name}
            </h2>
            {/* Codes on the SAME line as the name. They are reference detail a
                doctor checks, not a second heading. */}
            <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-[11px] leading-tight text-slate-500">
              <span aria-hidden className="text-slate-300">|</span>
              <span className="font-mono font-semibold text-slate-700">
                {patient.mrn ?? "No chart no."}
              </span>
              <span aria-hidden className="text-slate-300">|</span>
              <span className="font-semibold text-slate-700">
                {displayText(patient.age)} / {compactGender(patient.gender)}
              </span>
              {encounter?.name ? (
                <>
                  <span aria-hidden className="text-slate-300">|</span>
                  <span className="font-mono">{encounter.name}</span>
                </>
              ) : null}
              {consultation ? (
                <>
                  <span aria-hidden className="text-slate-300">|</span>
                  <span className="font-mono">{consultation.name}</span>
                </>
              ) : null}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1.5">
            {loading ? (
              <span className="text-[10px] text-slate-500">Updating…</span>
            ) : null}
            <StageBadge stage={visit.queue_stage} />
            <PriorityBadge priority={triage.priority} />
          </div>
        </div>
      </header>

      {/* ---- Vitals strip + triage complaint: always visible ---- */}
      <div className="shrink-0 border-b border-slate-200 bg-slate-50/80 px-3 py-1.5">
        <VitalsStrip vitals={triage.vitals} />
      </div>

      {alerts.length > 0 ? (
        <div className="shrink-0 border-b border-red-200 bg-red-50/70 px-3 py-1.5">
          <ul className="flex flex-wrap items-center gap-1.5">
            <li className="text-[9px] font-bold uppercase tracking-[0.07em] text-red-700">
              Alerts
            </li>
            {alerts.map((alert) => (
              <li
                key={alert.id}
                className="inline-flex items-center gap-1 rounded border border-red-300 bg-white px-1.5 py-0.5 text-[10.5px] font-semibold text-red-900"
              >
                {alert.name}
                {alert.severity ? (
                  <span className="text-[9px] font-bold uppercase tracking-wide text-red-500">
                    {doctorLabel(alert.severity)}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* ---- Section bar ---- */}
      <div className="flex shrink-0 items-center gap-1 border-b border-slate-200 bg-white px-3">
        {CONSULTATION_SECTIONS.map((entry) =>
          entry.live ? (
            <button
              key={entry.key}
              type="button"
              aria-current={section === entry.key ? "page" : undefined}
              onClick={() => onSectionChange(entry.key)}
              className={`-mb-px border-b-2 px-1.5 py-1.5 text-[10.5px] font-bold uppercase tracking-[0.07em] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 ${
                section === entry.key
                  ? "border-emerald-600 text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              {entry.label}
            </button>
          ) : (
            // Still text, not a button: no handler, no tab stop, no hover
            // affordance. A control that looks pressable and does nothing is
            // worse than an honest label in a clinical tool.
            <span
              key={entry.key}
              title="Arrives in a later clinical slice"
              className="cursor-default border-b-2 border-transparent px-1.5 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.07em] text-slate-400"
            >
              {entry.label}
            </span>
          ),
        )}
        <span aria-hidden className="flex-1" />
      </div>

      {/* ---- Triage context: collapsed by default, bounded when open ---- */}
      <div className="shrink-0 border-b border-slate-200 bg-white">
        <div className="flex items-start gap-2 px-3 py-1.5">
          {/* The NURSE's record of the complaint, labelled as such. The
              doctor's own presenting complaint is a separate editable field
              below: it is seeded from this once and then diverges, so showing
              both is what makes the copy visible rather than mysterious. */}
          <p className="min-w-0 flex-1 truncate text-[11.5px] leading-snug text-slate-700">
            <span className="font-bold uppercase tracking-[0.06em] text-slate-400">
              Triage ·{" "}
            </span>
            {triage.chief_complaint ?? visit.reason ?? (
              <span className="italic text-slate-400">Not recorded at triage.</span>
            )}
          </p>
          <button
            type="button"
            onClick={() => setContextOpen((open) => !open)}
            aria-expanded={contextOpen}
            className="shrink-0 rounded border border-slate-200 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-500 outline-none transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 focus-visible:ring-2 focus-visible:ring-emerald-600"
          >
            {contextOpen ? "Hide nursing detail" : "Nursing detail"}
          </button>
        </div>

        {contextOpen ? (
          // Bounded and independently scrollable, so opening the full nursing
          // record can never squeeze the note out of the viewport.
          <div className="max-h-[34vh] overflow-y-auto border-t border-slate-200 bg-slate-50/70 px-3 py-2">
            <div className="flex flex-col gap-2">
              <DoctorVitalsGrid
                vitals={triage.vitals}
                previous={detail.previous_vitals}
              />
              {triage.notes ? (
                <p className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 text-[11px] leading-relaxed text-slate-600">
                  <span className="font-bold uppercase tracking-[0.06em] text-slate-400">
                    Triage notes ·{" "}
                  </span>
                  {triage.notes}
                </p>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      {/* ---- Section body: THE only scrolling region ---- */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50/40 p-3">
        {section === "diagnosis" ? (
          /*
            Keyed on the visit so a patient change cannot carry one patient's
            diagnosis list into another's screen, exactly as the parent keys
            this whole workspace.
          */
          <DiagnosisWorkspace key={appointmentId} appointmentId={appointmentId} />
        ) : noteLoading && !consultation ? (
          <p className="py-8 text-center text-xs text-slate-500">
            Loading consultation note…
          </p>
        ) : loadError && !consultation ? (
          <p
            role="alert"
            className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] leading-snug text-amber-900"
          >
            {loadError}
          </p>
        ) : (
          <>
            {!editable && consultation ? (
              <p className="mb-2.5 rounded-md border border-slate-300 bg-white px-3 py-2 text-[11px] leading-snug text-slate-700">
                This consultation is completed and its clinical content is locked.
              </p>
            ) : null}
            <ConsultationNoteEditor
              draft={draft}
              baseline={baseline}
              disabled={!editable || status === "saving"}
              savedPulse={savedPulse}
              onChange={onFieldChange}
            />
          </>
        )}
      </div>

      {/* ---- Command bar ---- */}
      <footer
        className={`shrink-0 border-t px-3 py-2 ${
          problem
            ? "border-red-200 bg-red-50/70"
            : dirty
              ? "border-amber-200 bg-amber-50/60"
              : "border-slate-200 bg-white"
        }`}
      >
        {statusMessage && problem ? (
          <div
            role="alert"
            className="mb-2 rounded-md border border-red-300 bg-white px-2.5 py-1.5 text-[11px] leading-snug text-red-900"
          >
            <p className="font-semibold">
              {status === "conflict" ? "Save refused — the note changed" : "Save failed"}
            </p>
            <p className="mt-0.5 text-red-800">{statusMessage}</p>
            {status === "conflict" ? (
              <button
                type="button"
                onClick={reload}
                className="mt-1.5 rounded border border-red-400 bg-white px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-red-800 outline-none transition-colors hover:bg-red-50 focus-visible:ring-2 focus-visible:ring-red-600"
              >
                Reload the note
              </button>
            ) : null}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="flex min-w-0 items-center gap-1.5 text-[11px] leading-snug">
            {status === "saving" ? (
              <>
                <Spinner className="h-3 w-3 text-slate-500" />
                <span className="font-semibold text-slate-700">
                  Saving clinical note…
                </span>
              </>
            ) : dirty ? (
              <>
                <span aria-hidden className="h-2 w-2 rounded-full bg-amber-500" />
                <span className="font-semibold text-amber-900">
                  Unsaved changes
                </span>
                <span className="hidden text-slate-500 sm:inline">
                  · nothing is stored until you save
                </span>
              </>
            ) : status === "saved" || savedAt ? (
              <>
                <span aria-hidden className="font-bold text-emerald-700">✓</span>
                <span className="font-semibold text-emerald-800">
                  {/* The message belongs to the SAVE that produced it. Once the
                      doctor has typed again, status leaves "saved" and only the
                      persistent ✓ remains -- carrying "No changes to save."
                      forward would attach it to edits it never described. */}
                  {status === "saved" ? (statusMessage ?? "Saved") : "Saved"}
                </span>
                {savedAt ? (
                  <span className="text-slate-500">
                    {formatHospitalTime(savedAt)}
                  </span>
                ) : null}
              </>
            ) : editable ? (
              <span className="text-slate-500">
                {section === "diagnosis"
                  ? "Diagnoses save as you record them"
                  : "Note open for editing"}
              </span>
            ) : (
              <span className="text-slate-500">Read-only</span>
            )}
          </p>

          <button
            type="button"
            onClick={save}
            disabled={!editable || !dirty || status === "saving" || noteLoading}
            className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-emerald-700 px-4 text-[11.5px] font-bold uppercase tracking-[0.06em] text-white shadow-sm outline-none transition-colors hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400 disabled:shadow-none"
          >
            {status === "saving" ? (
              <>
                <Spinner className="h-3.5 w-3.5" />
                Saving…
              </>
            ) : (
              "Save Note"
            )}
          </button>
        </div>
      </footer>
    </section>
  );
}

function Spinner({ className }: { className: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      className={`animate-spin motion-reduce:animate-none ${className}`}
    >
      <path d="M14 8a6 6 0 1 1-1.76-4.24" />
    </svg>
  );
}

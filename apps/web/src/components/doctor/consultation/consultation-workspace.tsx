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
import { compactGender, displayText, doctorLabel } from "@/lib/doctor-format";
import type { ApiEnvelope, DoctorVisitDetail } from "@/types/doctor";
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
import ConsultationNoteEditor from "./note-editor";

/**
 * The active consultation workspace.
 *
 * MODE IS DERIVED, NOT DECLARED. This component renders only when the
 * AUTHORITATIVE visit state is `in_consultation`; the parent decides that from
 * the server payload. There is no client-side "consultation started" flag, no
 * parallel state machine and nothing that could disagree with Odoo about
 * whether a consultation is open.
 *
 * CONTEXT STAYS ON SCREEN. The doctor does not navigate away to write: the
 * complaint recorded at triage, the vitals, the alerts and the patient's
 * identity remain above the note. The context strip is collapsible because on a
 * short screen the note is what needs the height, but it is expanded by default
 * so nothing clinical is hidden unless the doctor hides it.
 *
 * THE DRAFT IS NEVER THE RECORD. `baseline` is the last thing the server
 * confirmed and `draft` is what the doctor has typed. Every successful save
 * replaces BOTH from the response, so the version token, the stored text and
 * the dirty markers all come from Odoo rather than from what this component
 * hoped it had written.
 */
export default function ConsultationWorkspace({
  detail,
  loading,
}: {
  detail: DoctorVisitDetail;
  loading: boolean;
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
  const [contextOpen, setContextOpen] = useState(true);

  /*
    NOTE ON CROSS-PATIENT SAFETY.

    The parent mounts this component with key={appointmentId}, so switching
    patients UNMOUNTS this instance and mounts a fresh one. That is what
    guarantees one patient's note can never appear in another's editor: there
    is no shared state to leak, and a late fetch resolving after unmount lands
    on a dead component and is discarded by React.

    A ref-based "is this still the active visit" guard was the obvious
    alternative and is worse in two ways: it has to be written during render to
    stay current (which react-hooks/refs correctly forbids), and it leaves the
    previous patient's draft, version token and status message sitting in state
    where a later code path could still read them.
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
        there is no divergence for a refresh to repair. Firing one would refetch
        the whole worklist on every save of a paragraph.
      */
      applyServerRecord(body.data.consultation);
      setStatus("saved");
      setStatusMessage(null);
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

  return (
    <section className="flex min-h-[320px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      {/* ---- Identity ---- */}
      <header className="shrink-0 border-b border-slate-200 bg-gradient-to-b from-white to-slate-50 px-3.5 py-2.5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span
              aria-hidden
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-[13px] font-bold text-emerald-800"
            >
              {patient.name.trim().charAt(0).toUpperCase() || "?"}
            </span>
            <div className="min-w-0">
              <h2 className="truncate text-[17px] font-bold leading-tight tracking-tight text-slate-950">
                {patient.name}
              </h2>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] leading-tight text-slate-500">
                <span className="font-mono font-bold text-slate-700">
                  {patient.mrn ?? "No chart no."}
                </span>
                <span aria-hidden className="text-slate-300">·</span>
                <span className="font-semibold text-slate-700">
                  {displayText(patient.age)} / {compactGender(patient.gender)}
                </span>
                {consultation ? (
                  <>
                    <span aria-hidden className="text-slate-300">·</span>
                    <span className="truncate font-mono">{consultation.name}</span>
                  </>
                ) : null}
              </div>
            </div>
          </div>
          {loading ? (
            <span className="shrink-0 text-[10px] text-slate-500">Updating…</span>
          ) : null}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <StageBadge stage={visit.queue_stage} />
          <PriorityBadge priority={triage.priority} />
          {consultation?.started_at ? (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-slate-500">
              Opened {formatHospitalTime(consultation.started_at)}
            </span>
          ) : null}
          {encounter?.name ? (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[9px] font-semibold text-slate-500">
              {encounter.name}
            </span>
          ) : null}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* ---- Context strip: triage, vitals, alerts ---- */}
        <div className="border-b border-slate-200 bg-slate-50/70">
          <button
            type="button"
            onClick={() => setContextOpen((open) => !open)}
            aria-expanded={contextOpen}
            className="flex w-full items-center gap-2 px-3.5 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-inset"
          >
            <span className="text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">
              Triage context
            </span>
            <span aria-hidden className="h-px flex-1 bg-slate-200" />
            <span className="text-[10px] font-semibold text-slate-500">
              {contextOpen ? "Hide" : "Show"}
            </span>
          </button>

          {contextOpen ? (
            <div className="flex flex-col gap-2 px-3.5 pb-3">
              {alerts.length > 0 ? (
                <ul className="flex flex-wrap gap-1.5">
                  {alerts.map((alert) => (
                    <li
                      key={alert.id}
                      className="inline-flex items-center gap-1.5 rounded-md border border-red-300 bg-red-50 px-2 py-1 text-[11px] font-semibold text-red-900"
                    >
                      {alert.name}
                      {alert.severity ? (
                        <span className="text-[9px] font-bold uppercase tracking-wide opacity-75">
                          {doctorLabel(alert.severity)}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}

              {/*
                The NURSE's record of the complaint, labelled as such and
                rendered read-only. The doctor's own presenting complaint is a
                separate, editable field below: it is seeded from this once and
                then diverges, so showing both is what makes the copy visible
                rather than mysterious.
              */}
              <p className="rounded-md border border-slate-200 border-l-[3px] border-l-emerald-600 bg-white px-3 py-2 text-[12px] font-medium leading-relaxed text-slate-900">
                <span className="font-bold uppercase tracking-[0.06em] text-slate-400">
                  Triage complaint ·{" "}
                </span>
                {triage.chief_complaint ?? visit.reason ?? (
                  <span className="font-normal italic text-slate-400">
                    Not recorded at triage.
                  </span>
                )}
              </p>

              <DoctorVitalsGrid
                vitals={triage.vitals}
                previous={detail.previous_vitals}
              />
            </div>
          ) : null}
        </div>

        {/* ---- The note ---- */}
        <div className="p-3.5">
          {noteLoading && !consultation ? (
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
                <p className="mb-2.5 rounded-md border border-slate-300 bg-slate-50 px-3 py-2 text-[11px] leading-snug text-slate-700">
                  This consultation is completed and its clinical content is
                  locked.
                </p>
              ) : null}
              <ConsultationNoteEditor
                draft={draft}
                baseline={baseline}
                disabled={!editable || status === "saving"}
                onChange={onFieldChange}
              />
            </>
          )}
        </div>
      </div>

      {/* ---- Save bar ---- */}
      <footer
        className={`shrink-0 border-t px-3.5 py-2.5 ${
          status === "conflict" || status === "error"
            ? "border-red-200 bg-red-50/70"
            : dirty
              ? "border-amber-200 bg-amber-50/70"
              : "border-slate-200 bg-slate-50"
        }`}
      >
        {statusMessage && (status === "conflict" || status === "error") ? (
          <div
            role="alert"
            className="mb-2 rounded-md border border-red-300 bg-red-50 px-2.5 py-1.5 text-[11px] leading-snug text-red-900"
          >
            <p>{statusMessage}</p>
            {status === "conflict" ? (
              <button
                type="button"
                onClick={reload}
                className="mt-1.5 rounded border border-red-400 bg-white px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-red-800 outline-none hover:bg-red-100 focus-visible:ring-2 focus-visible:ring-red-600"
              >
                Reload the note
              </button>
            ) : null}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="min-w-0 text-[11px] leading-snug text-slate-600">
            {status === "saving" ? (
              <span className="font-semibold text-slate-700">Saving…</span>
            ) : dirty ? (
              <span className="font-semibold text-amber-900">
                Unsaved changes. Nothing is stored until you save.
              </span>
            ) : status === "saved" ? (
              <span className="font-semibold text-emerald-800">
                {statusMessage ?? "Saved."}
              </span>
            ) : editable ? (
              "Consultation note is open for editing."
            ) : (
              "This note is read-only."
            )}
          </p>

          <button
            type="button"
            onClick={save}
            disabled={!editable || !dirty || status === "saving" || noteLoading}
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md bg-emerald-700 px-4 text-[12px] font-bold uppercase tracking-[0.06em] text-white shadow-sm outline-none transition-colors hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 focus-visible:ring-offset-transparent disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500 disabled:shadow-none"
          >
            {status === "saving" ? (
              <>
                <svg
                  aria-hidden
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none"
                >
                  <path d="M14 8a6 6 0 1 1-1.76-4.24" />
                </svg>
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

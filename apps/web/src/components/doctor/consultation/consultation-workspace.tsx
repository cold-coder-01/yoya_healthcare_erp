"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { formatHospitalTime } from "@/lib/clinical-format";
import {
  NOTE_FIELDS,
  buildSavePayload,
  completeDisabledReason,
  draftFromConsultation,
  hasUnsavedChanges,
  isEmptySave,
  mayCompleteConsultation,
} from "@/lib/consultation-format";
import { CONSULTATION_SECTIONS } from "@/lib/diagnosis-format";
import type { ConsultationSection } from "@/lib/diagnosis-format";
import {
  activeNoteField,
  consultationIdleText,
  isStaleNoteSelection,
} from "@/lib/note-editor-format";
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
  ConsultationBlocker,
  ConsultationCompleteResponse,
  ConsultationCompleteStatus,
  ConsultationSaveStatus,
  ConsultationWarning,
  DoctorConsultation,
  DoctorConsultationResponse,
} from "@/types/doctor-consultation";
import { CONSULTATION_CONFLICT_CODE } from "@/types/doctor-consultation";

import { PriorityBadge, StageBadge } from "../doctor-badges";
import DoctorVitalsGrid from "../doctor-vitals-grid";
import DiagnosisWorkspace from "./diagnosis-workspace";
import OrdersWorkspace from "./orders-workspace";
import ResultsWorkspace from "./results-workspace";
import ConsultationNoteEditor from "./note-editor";
import NoteEditorModal from "./note-editor-modal";
import { ConsultationOrderDraftProvider } from "./order-draft-context";

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
      <span className="shrink-0 cl-micro font-bold uppercase tracking-[0.07em] text-slate-400">
        {label}
      </span>
      <span className="truncate cl-body font-bold leading-none tabular-nums text-slate-800">
        {reading}
        {unit.length ? (
          <span className="ml-0.5 cl-micro font-semibold text-slate-400">
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
  onCompleted,
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
  /*
    Fired after a SUCCESSFUL completion, with the visit detail the server
    re-read inside the same transaction. The parent uses it to re-bucket the
    queue row and to swap the workspace to read-only.

    The payload is passed UP rather than refetched, because the server already
    produced the post-completion state atomically; a follow-up GET would be a
    second read of a state this component has already been handed, and could
    race a concurrent write.
  */
  onCompleted?: (detail: DoctorVisitDetail) => void;
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
  /*
    WHICH note section is open in the focused editor, or null for none.

    Held HERE rather than inside the note surface because the modal writes
    through the same draft, the same version token and the same save path as the
    command bar. A second copy of that state inside the editor is exactly how a
    second persistence mechanism gets built by accident.
  */
  const [openSection, setOpenSection] =
    useState<ConsultationNarrativeField | null>(null);
  /*
    The card that opened the modal. Focus goes back to it on close, so a
    keyboard user resumes where they were instead of at the top of the document.
  */
  const noteOriginRef = useRef<HTMLButtonElement | null>(null);
  /** Client-side, presentation only: when this tab last saw a save succeed. */
  const [savedAt, setSavedAt] = useState<string | null>(null);

  /*
    COMPLETION IS TRACKED SEPARATELY FROM SAVING, and deliberately so.

    They are different acts with different consequences: a failed save leaves
    text the doctor can retype, a failed completion leaves a question about
    whether the record is signed. Sharing one status enum would make "error"
    ambiguous at exactly the moment the doctor most needs to know which of the
    two failed.
  */
  const [completeStatus, setCompleteStatus] =
    useState<ConsultationCompleteStatus>("idle");
  const [completeError, setCompleteError] = useState<string | null>(null);
  /*
    SERVER VERDICTS, held exactly as received and never recomputed here.
    can_complete and completion_blockers come from
    hospital.consultation.completion_blockers(), which action_complete()
    enforces a moment later. A local re-derivation would eventually enable a
    button the server refuses.
  */
  const [canComplete, setCanComplete] = useState(false);
  const [blockers, setBlockers] = useState<ConsultationBlocker[]>([]);
  const [warnings, setWarnings] = useState<ConsultationWarning[]>([]);

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

  /*
    The completion verdict travels with EVERY consultation payload -- the load,
    each save and the completion itself -- so the button re-evaluates the moment
    a doctor writes an assessment or marks a primary diagnosis, without this
    component knowing what either of those rules is.
  */
  const applyCompletionVerdict = useCallback(
    (payload: DoctorConsultationResponse) => {
      setCanComplete(Boolean(payload.can_complete));
      setBlockers(payload.completion_blockers ?? []);
      setWarnings(payload.completion_warnings ?? []);
    },
    [],
  );

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
        applyCompletionVerdict(payload.data);
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
  }, [appointmentId, applyServerRecord, applyCompletionVerdict]);

  const dirty = useMemo(
    () => hasUnsavedChanges(draft, baseline),
    [draft, baseline],
  );
  const editable = Boolean(consultation?.editable);

  /* ---------------- focused note editor ---------------- */
  /*
    THE OPEN EDITOR, RESOLVED ONCE.

    This single value is what the modal's render gate, the command bar's
    sentence and the staleness check all read. "An editor is open" and "the
    modal is mounted" are therefore the SAME expression rather than two
    derivations that can disagree -- and their disagreement had exactly one
    visible shape: a command bar announcing an open editor with nothing on
    screen to close.

    Resolved from NOTE_FIELDS -- the same list the save payload is built from --
    so a section can never be opened for a field the save path does not know
    about.
  */
  const openField = activeNoteField(openSection, NOTE_FIELDS);
  const editorOpen = openField !== null;

  /*
    NORMALISED AT THE SOURCE, which is why no effect is needed downstream.

    The selection is refused unless it resolves to a field the render gate will
    actually mount, so `openSection` can never hold a value that makes the
    workspace think an editor is open while none is. An effect that cleared the
    bad value afterwards would be a repair for a state this simply cannot enter
    -- and would call setState from an effect body to do it.
  */
  const openNoteSection = useCallback(
    (field: ConsultationNarrativeField, origin: HTMLButtonElement) => {
      if (isStaleNoteSelection(field, activeNoteField(field, NOTE_FIELDS))) {
        return;
      }
      noteOriginRef.current = origin;
      setOpenSection(field);
    },
    [],
  );

  const closeNoteSection = useCallback(() => {
    setOpenSection(null);
    /*
      Focus returns on the NEXT frame: the card is still behind the modal at
      this point in the commit, and focusing an element that is about to be
      re-rendered loses the ring. requestAnimationFrame is enough -- the card
      is never unmounted, only re-rendered with a new preview.
    */
    const origin = noteOriginRef.current;
    noteOriginRef.current = null;
    if (origin) {
      requestAnimationFrame(() => origin.focus());
    }
  }, []);

  /*
    WHY THERE IS NO NORMALISING EFFECT HERE, and why the invariant still holds.

    "No modal visible" and "nothing open" are not two states kept in step; they
    are one derivation, `openField`, read by the render gate and by the command
    bar alike. The selection can only be SET to a resolvable field (above) and
    can only be CLEARED through `closeNoteSection` (below), which every exit --
    Save & close, Cancel, the X, Escape, the backdrop and the read-only
    viewer's Close -- routes through. There is no third writer and no second
    exit, so there is nothing for an effect to repair, and no setState in an
    effect body to cascade a render off.
  */

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
  /*
    RETURNS WHETHER THE WRITE LANDED. The command-bar button ignores the value
    and reads the status banner exactly as it always did; the note modal needs
    it, because it must close ONLY on success. A modal that closed on a refused
    save -- a version conflict, most of all -- would tell the doctor their
    paragraph was stored when the server had rejected it.

    A save with nothing to send counts as success: there is no unsaved text
    left, which is the question the caller is really asking.
  */
  const save = useCallback(async (): Promise<boolean> => {
    if (!consultation || !editable) return false;

    const payload = buildSavePayload(consultation.version, draft, baseline);
    if (isEmptySave(payload)) {
      setStatus("saved");
      setStatusMessage("No changes to save.");
      return true;
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
        return false;
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
      // The saved assessment may have just satisfied the completion rule, so
      // the verdict is re-read from the same response rather than left stale
      // until the next page load.
      applyCompletionVerdict(body.data);
      setStatus("saved");
      setStatusMessage(null);
      setSavedAt(new Date().toISOString());
      return true;
    } catch {
      setStatus("error");
      setStatusMessage("Unable to reach the consultation service.");
      return false;
    }
  }, [
    appointmentId,
    applyServerRecord,
    applyCompletionVerdict,
    baseline,
    consultation,
    draft,
    editable,
  ]);

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
        applyCompletionVerdict(payload.data);
        setStatus("idle");
        setStatusMessage(null);
      }
    } catch {
      /* the banner already shown stays; nothing worse has happened */
    } finally {
      setNoteLoading(false);
    }
  }, [appointmentId, applyServerRecord, applyCompletionVerdict]);

  /* ---------------- complete ---------------- */
  /*
    THE GATE ON THE BUTTON, in one place.

    `dirty` is the condition that matters most and is the reason completion does
    NOT auto-save: completing writes no clinical content, so an unsaved
    paragraph would be frozen out of existence -- the note would lock with text
    the doctor could still see on screen but which was never sent. The server
    enforces the same separation by rejecting narrative fields on the completion
    endpoint; this refuses to offer the action in the first place.
  */
  const completeBlockedByDraft = dirty || status === "saving";
  /*
    The gate itself now lives in consultation-format, where a test enumerates
    its whole input surface. `editorOpen` is deliberately NOT among those
    inputs: which section the doctor has open on screen is presentation, and
    the question that actually matters -- whether their words reached Odoo --
    is `dirty`. Adding an editor flag here is how a piece of view state becomes
    able to make a documented consultation uncompletable.
  */
  const completeState = {
    editable,
    canComplete,
    dirty,
    saving: status === "saving",
    loading: noteLoading,
  };
  const mayComplete = mayCompleteConsultation(completeState);
  const completeReason = completeDisabledReason({ ...completeState, blockers });

  const complete = useCallback(async () => {
    if (!consultation || !editable || completeBlockedByDraft) return;

    setCompleteStatus("completing");
    setCompleteError(null);
    const target = appointmentId;

    try {
      const response = await fetch(
        `/api/doctor/visits/${target}/consultation/complete`,
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          // THE VERSION AND NOTHING ELSE. Completion is not an edit, and the
          // server rejects narrative fields here by name.
          body: JSON.stringify({ version: consultation.version }),
        },
      );
      const body =
        (await response.json()) as ApiEnvelope<ConsultationCompleteResponse>;

      if (!response.ok || !body.success) {
        const code = body.success === false ? body.error.code : null;
        /*
          A conflict here means the note moved after this tab read it -- another
          tab saved, or another tab completed. Reloading is the only safe
          recovery for the same reason it is on save: the doctor must see what
          they are actually signing.
        */
        setCompleteStatus(code === CONSULTATION_CONFLICT_CODE ? "error" : "error");
        setCompleteError(
          messageFromPayload(body, "Unable to complete the consultation."),
        );
        return;
      }

      /*
        The server re-read all of this INSIDE the completing transaction, so it
        describes the committed state rather than a state this component
        predicted. The consultation now reports editable=false, which is what
        flips the whole workspace read-only -- no local flag is set for it.
      */
      applyServerRecord(body.data.consultation);
      applyCompletionVerdict(body.data);
      setCompleteStatus("idle");
      setCompleteError(null);
      setStatus("idle");
      setStatusMessage(null);
      onCompleted?.(body.data.visit_detail);
    } catch {
      setCompleteStatus("error");
      setCompleteError("Unable to reach the consultation service.");
    }
  }, [
    appointmentId,
    applyServerRecord,
    applyCompletionVerdict,
    completeBlockedByDraft,
    consultation,
    editable,
    onCompleted,
  ]);

  const { patient, triage, medical_alerts: alerts, visit, encounter } = detail;
  const problem = status === "conflict" || status === "error";

  return (
    /*
      ---- Unsent order drafts ----

      MOUNTED OUTSIDE EVERY SECTION CONDITIONAL, deliberately. The section body
      below renders exactly one of ORDERS, DIAGNOSIS or the note, so anything
      held inside those subtrees is destroyed by a section click -- and the
      ORDERS tabs do the same again one level down. A half-written laboratory
      request, imaging request or prescription belongs to this CONSULTATION, so
      it is held here, where no navigation within the consultation can reach it.

      Scoped by appointment twice over: this whole workspace is already mounted
      with key={appointment_id} by the workstation, and the provider wipes its
      own state if the appointment ever changes underneath it.
    */
    <ConsultationOrderDraftProvider appointmentId={appointmentId}>
    <section className="flex h-full min-h-[560px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      {/* ---- Identity: one dense line, not a card ---- */}
      <header className="shrink-0 border-b border-slate-200 bg-white px-3 py-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <span
              aria-hidden
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-100 cl-body font-bold text-emerald-800"
            >
              {patient.name.trim().charAt(0).toUpperCase() || "?"}
            </span>
            <h2 className="shrink-0 truncate cl-head font-bold leading-tight tracking-tight text-slate-950">
              {patient.name}
            </h2>
            {/* Codes on the SAME line as the name. They are reference detail a
                doctor checks, not a second heading. */}
            <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 cl-secondary leading-tight text-slate-500">
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
              <span className="cl-meta text-slate-500">Updating…</span>
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
            <li className="cl-micro font-bold uppercase tracking-[0.07em] text-red-700">
              Alerts
            </li>
            {alerts.map((alert) => (
              <li
                key={alert.id}
                className="inline-flex items-center gap-1 rounded border border-red-300 bg-white px-1.5 py-0.5 cl-meta font-semibold text-red-900"
              >
                {alert.name}
                {alert.severity ? (
                  <span className="cl-micro font-bold uppercase tracking-wide text-red-500">
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
              className={`-mb-px border-b-2 px-1.5 py-1.5 cl-meta font-bold uppercase tracking-[0.07em] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 ${
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
              className="cursor-default border-b-2 border-transparent px-1.5 py-1.5 cl-meta font-semibold uppercase tracking-[0.07em] text-slate-400"
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
          <p className="min-w-0 flex-1 truncate cl-secondary leading-snug text-slate-700">
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
            className="shrink-0 rounded border border-slate-200 px-1.5 py-0.5 cl-micro font-bold uppercase tracking-wide text-slate-500 outline-none transition-colors hover:border-slate-300 hover:bg-slate-50 hover:text-slate-700 focus-visible:ring-2 focus-visible:ring-emerald-600"
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
                <p className="rounded-md border border-slate-200 bg-white px-2.5 py-1.5 cl-secondary leading-relaxed text-slate-600">
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
        {section === "orders" ? (
          /* Keyed on the visit for the same reason the diagnosis list is: a
             patient change must not carry one patient's orders onto another's
             screen. */
          <OrdersWorkspace key={appointmentId} appointmentId={appointmentId} />
        ) : section === "results" ? (
          /*
            Keyed on the visit, like every other section, and mounted only
            while RESULTS is open -- which is what makes opening the tab
            refetch. That is the honest behaviour for a queue another
            department is working: a result the doctor left ten minutes ago may
            well have landed, and a stale card is worse than a brief spinner.

            READ-ONLY, and unconditionally so: it is rendered the same way for
            a completed consultation as for an open one, because chasing a
            result is precisely what a doctor does after the visit finishes.
          */
          <ResultsWorkspace key={appointmentId} appointmentId={appointmentId} />
        ) : section === "diagnosis" ? (
          /*
            Keyed on the visit so a patient change cannot carry one patient's
            diagnosis list into another's screen, exactly as the parent keys
            this whole workspace.
          */
          <DiagnosisWorkspace key={appointmentId} appointmentId={appointmentId} />
        ) : noteLoading && !consultation ? (
          <p className="py-8 text-center cl-body text-slate-500">
            Loading consultation note…
          </p>
        ) : loadError && !consultation ? (
          <p
            role="alert"
            className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 cl-secondary leading-snug text-amber-900"
          >
            {loadError}
          </p>
        ) : (
          <>
            {!editable && consultation ? (
              <div className="mb-2.5 rounded-md border border-slate-300 bg-slate-50 px-3 py-2">
                <p className="cl-secondary font-bold uppercase tracking-wide text-slate-700">
                  Consultation completed
                </p>
                <p className="mt-0.5 cl-secondary leading-snug text-slate-600">
                  The clinical note and its diagnoses are locked. Orders already
                  placed continue under their own workflow.
                  {consultation.completed_at
                    ? ` Signed ${formatHospitalTime(consultation.completed_at)}.`
                    : ""}
                </p>
              </div>
            ) : null}
            <ConsultationNoteEditor
              draft={draft}
              baseline={baseline}
              readOnly={!editable}
              onOpenSection={openNoteSection}
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
        {completeStatus === "confirming" ? (
          <CompletionConfirm
            warnings={warnings}
            busy={false}
            onCancel={() => setCompleteStatus("idle")}
            onConfirm={complete}
          />
        ) : null}
        {completeStatus === "completing" ? (
          <CompletionConfirm
            warnings={warnings}
            busy
            onCancel={() => undefined}
            onConfirm={() => undefined}
          />
        ) : null}

        {completeError ? (
          <div
            role="alert"
            className="mb-2 rounded-md border border-red-300 bg-white px-2.5 py-1.5 cl-secondary leading-snug text-red-900"
          >
            <p className="font-semibold">Consultation not completed</p>
            <p className="mt-0.5 text-red-800">{completeError}</p>
            <button
              type="button"
              onClick={reload}
              className="mt-1.5 rounded border border-red-400 bg-white px-2 py-0.5 cl-meta font-bold uppercase tracking-wide text-red-800 outline-none transition-colors hover:bg-red-50 focus-visible:ring-2 focus-visible:ring-red-600"
            >
              Reload the note
            </button>
          </div>
        ) : null}

        {/*
          WHY THE BLOCKERS ARE LISTED RATHER THAN JUST DISABLING THE BUTTON.
          A greyed-out control with no explanation is the single most common way
          a clinician loses time on a form. Each sentence is the SERVER's, bound
          to its own stable code, so the desk never guesses at a rule it does
          not own.
        */}
        {editable && !canComplete && !completeBlockedByDraft && !noteLoading ? (
          <ul className="mb-2 space-y-0.5 rounded-md border border-slate-300 bg-slate-50 px-2.5 py-1.5">
            {(blockers.length
              ? blockers.map((blocker) => ({
                  key: blocker.code as string,
                  message: blocker.message,
                }))
              : /*
                  THE HOLE THIS BRANCH CLOSES. A verdict of can_complete:false
                  carrying an EMPTY blocker list used to render nothing at all:
                  the list needed `blockers.length`, and the button's tooltip
                  needed it too. The doctor got a grey button and no sentence
                  anywhere. The wording is this desk's, and says so -- it is the
                  only sentence here the server did not write.
                */
                [{ key: "not_cleared", message: completeReason ?? "" }]
            ).map((item) => (
              <li
                key={item.key}
                className="flex items-start gap-1.5 cl-secondary leading-snug text-slate-700"
              >
                <span aria-hidden className="mt-px text-slate-400">
                  •
                </span>
                {item.message}
              </li>
            ))}
          </ul>
        ) : null}

        {statusMessage && problem ? (
          <div
            role="alert"
            className="mb-2 rounded-md border border-red-300 bg-white px-2.5 py-1.5 cl-secondary leading-snug text-red-900"
          >
            <p className="font-semibold">
              {status === "conflict" ? "Save refused — the note changed" : "Save failed"}
            </p>
            <p className="mt-0.5 text-red-800">{statusMessage}</p>
            {status === "conflict" ? (
              <button
                type="button"
                onClick={reload}
                className="mt-1.5 rounded border border-red-400 bg-white px-2 py-0.5 cl-meta font-bold uppercase tracking-wide text-red-800 outline-none transition-colors hover:bg-red-50 focus-visible:ring-2 focus-visible:ring-red-600"
              >
                Reload the note
              </button>
            ) : null}
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="flex min-w-0 items-center gap-1.5 cl-secondary leading-snug">
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
                  · save your note before completing the consultation
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
              /*
                THE SENTENCE IS BOUND TO THE MODAL'S OWN RENDER GATE.

                The open-editor sentence used to be the NOTE tab's STATIC idle
                text, so the desk announced an editor whenever the note simply
                sat there clean -- indistinguishable, to a doctor reading the
                footer, from an editor stuck open behind nothing. It is now said
                only when `editorOpen` is true, and `editorOpen` is the same
                value that mounts the modal below. The wording itself lives in
                note-editor-format, so it cannot be restated here by hand.
              */
              <span className="text-slate-500">
                {consultationIdleText({ section, editorOpen })}
              </span>
            ) : (
              <span className="text-slate-500">Read-only</span>
            )}
          </p>

          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={save}
              disabled={!editable || !dirty || status === "saving" || noteLoading}
              className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border border-emerald-700 bg-white px-3.5 cl-secondary font-bold uppercase tracking-[0.06em] text-emerald-800 shadow-sm outline-none transition-colors hover:bg-emerald-50 focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400 disabled:shadow-none"
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

            {/*
              COMPLETION IS THE TERMINAL ACTION, so it carries the solid accent
              and Save steps back to an outline. It is rendered only while the
              consultation is still open: after completion there is nothing to
              complete, and a disabled button would keep offering an act that no
              longer exists.

              `title` names the reason whenever the control is disabled, so the
              two blocking conditions the blocker list does NOT cover -- unsaved
              text and an in-flight save -- are still explained.
            */}
            {editable ? (
              <button
                type="button"
                onClick={() => {
                  setCompleteError(null);
                  setCompleteStatus("confirming");
                }}
                disabled={!mayComplete || completeStatus !== "idle"}
                /*
                  ALWAYS NAMES A REASON WHEN IT IS GREY. The previous expression
                  fell through to `undefined` when the server said
                  can_complete:false with an EMPTY blocker list -- a dead button
                  with no list, no tooltip and no sentence anywhere on screen.
                */
                title={completeReason ?? undefined}
                className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md bg-emerald-700 px-4 cl-secondary font-bold uppercase tracking-[0.06em] text-white shadow-sm outline-none transition-colors hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400 disabled:shadow-none"
              >
                {completeStatus === "completing" ? (
                  <>
                    <Spinner className="h-3.5 w-3.5" />
                    Completing…
                  </>
                ) : (
                  "Complete Consultation"
                )}
              </button>
            ) : null}
          </div>
        </div>
      </footer>

      {/*
        ---- Focused note editor ----

        RENDERED LAST, INSIDE THE WORKSPACE, AND POSITIONED `fixed`. It escapes
        this section's `overflow-hidden` because a fixed element is laid out
        against the viewport, so no portal is needed and the modal still sits
        inside the React tree that owns the draft it edits.

        Its z-index deliberately clears the Doctor Desk chrome: while a section
        is open it IS the task, and a half-covered navigation bar invites a
        click that would abandon a paragraph.
      */}
      {openField ? (
        <NoteEditorModal
          /* Keyed on the section so switching fields remounts the editor --
             which re-seeds its "opened with" baseline and re-runs autofocus. */
          key={openField.key}
          label={openField.label}
          hint={openField.placeholder}
          value={draft[openField.key]}
          context={`${patient.name}${visit.appointment_code ? ` · ${visit.appointment_code}` : ""}`}
          readOnly={!editable}
          busy={status === "saving"}
          errorMessage={problem ? statusMessage : null}
          onChange={(next) => onFieldChange(openField.key, next)}
          onSave={save}
          onClose={closeNoteSection}
        />
      ) : null}
    </section>
    </ConsultationOrderDraftProvider>
  );
}

/**
 * The completion confirmation. An INLINE PANEL, never window.confirm().
 *
 * A native confirm cannot show the warnings, cannot be styled to match the
 * desk, blocks the whole browser, and -- the reason that actually matters --
 * gives the doctor no way to read the outstanding balance and the pending-order
 * count before deciding. Those two sentences are the entire point of asking.
 *
 * The warnings are INFORMATIONAL. They never disable Complete: a patient owing
 * money is a cashier problem, not a reason to refuse a doctor the right to
 * finish documenting the care they have already given.
 */
function CompletionConfirm({
  warnings,
  busy,
  onCancel,
  onConfirm,
}: {
  warnings: ConsultationWarning[];
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      role="group"
      aria-label="Confirm consultation completion"
      className="mb-2 rounded-md border border-emerald-300 bg-white px-3 py-2.5 shadow-sm"
    >
      <p className="cl-secondary font-bold uppercase tracking-wide text-emerald-900">
        Complete this consultation?
      </p>

      <ul className="mt-1.5 space-y-0.5 cl-secondary leading-snug text-slate-700">
        <li>The clinical note is locked and can no longer be edited.</li>
        <li>Diagnoses become read-only.</li>
        <li>No further orders can be placed from this consultation.</li>
        <li>Orders already placed continue under their own workflow.</li>
      </ul>

      {warnings.length ? (
        <ul className="mt-2 space-y-1">
          {warnings.map((warning) => (
            <li
              key={warning.code}
              className="rounded border border-amber-300 bg-amber-50 px-2 py-1 cl-secondary leading-snug text-amber-900"
            >
              {warning.message}
            </li>
          ))}
        </ul>
      ) : null}

      <div className="mt-2.5 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="inline-flex h-7 items-center rounded-md border border-slate-300 bg-white px-3 cl-secondary font-bold uppercase tracking-wide text-slate-700 outline-none transition-colors hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-slate-500 disabled:cursor-not-allowed disabled:text-slate-400"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy}
          className="inline-flex h-7 items-center gap-1.5 rounded-md bg-emerald-700 px-3.5 cl-secondary font-bold uppercase tracking-wide text-white outline-none transition-colors hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {busy ? (
            <>
              <Spinner className="h-3 w-3" />
              Completing…
            </>
          ) : (
            "Complete Consultation"
          )}
        </button>
      </div>
    </div>
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

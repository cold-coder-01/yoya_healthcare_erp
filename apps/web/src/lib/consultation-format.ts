/**
 * Consultation editor logic: mode, draft shape and what a save actually sends.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * decides authorization -- the model refuses a write to a completed or
 * out-of-scope consultation whatever this file says.
 */
// TYPE-ONLY, and it has to stay that way. TypeScript erases these statements
// entirely, which is what lets node:test run consultation-format.test.ts
// directly with no resolver and no transform -- the `@/` alias is a tsconfig
// path Node knows nothing about. A runtime import from `@/types/...` would
// make this module untestable under `npm test`. The field list is therefore
// restated below rather than imported, and a test pins the two together.
import type {
  ConsultationDraft,
  ConsultationNarrativeField,
  ConsultationSaveRequest,
  DoctorConsultation,
} from "@/types/doctor-consultation";

/**
 * THE appointment state in which the consultation workspace is the right
 * screen. Compared against, never recomputed from triage or clearance.
 *
 * Mirrors CONSULTATION_APPOINTMENT_STATE in
 * yoya_clinical_bridge/models/consultation.py, which is the authority and which
 * refuses to open a note for any other state.
 */
export const CONSULTATION_STATE = "in_consultation";

/**
 * Runtime copy of the narrative field list.
 *
 * Deliberately duplicated from types/doctor-consultation.ts rather than
 * imported at runtime -- see the note on the import above. `fieldListsAgree()`
 * exists so the duplication is checked rather than trusted.
 */
export const NOTE_FIELDS: ReadonlyArray<{
  key: ConsultationNarrativeField;
  label: string;
  placeholder: string;
  rows: number;
}> = [
  {
    key: "presenting_complaint",
    label: "Presenting Complaint",
    placeholder: "What the patient has come with, in clinical terms…",
    rows: 2,
  },
  {
    key: "history_of_presenting_illness",
    label: "History of Presenting Illness",
    placeholder: "Onset, duration, character, aggravating and relieving factors…",
    rows: 4,
  },
  {
    key: "review_of_systems",
    label: "Review of Systems",
    placeholder: "Systemic enquiry, positives and relevant negatives…",
    rows: 3,
  },
  {
    key: "examination_findings",
    label: "Examination Findings",
    placeholder: "General and system examination…",
    rows: 4,
  },
  {
    key: "assessment",
    label: "Assessment",
    placeholder: "Clinical impression…",
    rows: 3,
  },
  {
    key: "plan",
    label: "Plan",
    placeholder: "Investigations, treatment, follow-up, advice given…",
    rows: 3,
  },
];

/** Whether the active-consultation workspace is the right screen for a visit. */
export function isConsultationMode(visitState: string | null | undefined) {
  return visitState === CONSULTATION_STATE;
}

/**
 * The server record, flattened into an editable draft.
 *
 * Odoo returns null for an unset Text field; a textarea needs "". Doing the
 * conversion in ONE place is what stops a `null` reaching a controlled input
 * and flipping it to uncontrolled mid-edit.
 */
export function draftFromConsultation(
  consultation: DoctorConsultation | null,
): ConsultationDraft {
  const draft = {} as ConsultationDraft;
  for (const field of NOTE_FIELDS) {
    draft[field.key] = consultation?.[field.key] ?? "";
  }
  return draft;
}

/** True when the draft differs from the last thing the server confirmed. */
export function hasUnsavedChanges(
  draft: ConsultationDraft,
  baseline: ConsultationDraft,
) {
  return NOTE_FIELDS.some((field) => draft[field.key] !== baseline[field.key]);
}

/**
 * The save body: the version, plus ONLY the fields that actually changed.
 *
 * SENDING ONLY THE DIFF IS A CORRECTNESS CHOICE, NOT AN OPTIMISATION. The
 * server writes exactly what it is given, so posting all six fields every time
 * would rewrite paragraphs the doctor never touched -- and would do it with the
 * values this tab happened to load, which is the same overwrite the version
 * token exists to prevent, arriving through the front door.
 *
 * An empty string IS sent when it differs from the baseline: clearing a
 * paragraph written in error is a legitimate edit, and is distinct from not
 * touching the field at all.
 */
export function buildSavePayload(
  version: string,
  draft: ConsultationDraft,
  baseline: ConsultationDraft,
): ConsultationSaveRequest {
  const payload: ConsultationSaveRequest = { version };
  for (const field of NOTE_FIELDS) {
    if (draft[field.key] !== baseline[field.key]) {
      payload[field.key] = draft[field.key];
    }
  }
  return payload;
}

/** True when a save would send nothing but the version. */
export function isEmptySave(payload: ConsultationSaveRequest) {
  return Object.keys(payload).length === 1;
}

/**
 * Guard for the runtime/type duplication above.
 *
 * Exported so a test can assert NOTE_FIELDS matches
 * CONSULTATION_NARRATIVE_FIELDS exactly, in order. A field added to the model
 * and the types but forgotten here would otherwise be silently uneditable and
 * silently unsaved.
 */
export function fieldListsAgree(canonical: readonly string[]) {
  const ours = NOTE_FIELDS.map((field) => field.key);
  return (
    ours.length === canonical.length &&
    ours.every((key, index) => key === canonical[index])
  );
}

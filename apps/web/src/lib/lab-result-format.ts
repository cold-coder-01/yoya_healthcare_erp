/**
 * Laboratory Desk result entry: the pure decisions behind the result modal.
 *
 * Nothing here fetches, writes or decides anything clinical. What counts as a
 * COMPLETE result is hospital.laboratory.result.action_mark_entered()'s
 * decision and is never restated here -- the browser does not trim, does not
 * require a unit, and does not refuse a blank value before the server has had
 * the chance to say so in its own words.
 *
 * WHAT THE DESK ITSELF DECIDES is narrower and operational: which request
 * offers "Enter results" (in_progress only, one operational result), and how
 * unsent typing is protected. Those are the rules pinned by
 * lab-result-format.test.ts.
 */
// TYPE-ONLY, and it has to stay that way: node:test runs this module's tests
// with no resolver and no transform, so a VALUE import through "@/..." would
// not resolve.
import type {
  LabResult,
  LabResultLine,
  LabResultWritePayload,
} from "@/types/lab-desk";

/* ------------------------------------------------------------------ *
 * Affordances
 * ------------------------------------------------------------------ */

type ResultEntryContext = {
  status: string | null | undefined;
  result: { state: string } | null | undefined;
  result_conflict?: boolean | null;
};

/**
 * May the panel offer "Enter results" for this request?
 *
 * LABORATORY DESK POLICY, mirrored from the Lab API rather than invented: the
 * request is `in_progress`, there is at most one result, and that result (if
 * any) is still a draft to resume. The model itself would accept a result on a
 * sample_collected request; the bench deliberately does not.
 *
 * AN AFFORDANCE, NEVER A PERMISSION. /requests/<id>/result re-applies every
 * one of these conditions under a row lock, so a button drawn from stale data
 * still gets a clean refusal.
 */
export function canEnterResults(detail: ResultEntryContext | null | undefined) {
  if (!detail || detail.status !== "in_progress") return false;
  if (detail.result_conflict) return false;
  return !detail.result || detail.result.state === "draft";
}

/**
 * May the panel offer a read-only "View result"?
 *
 * For a result that exists and is past draft: entered, validated or released.
 * A cancelled result is stated, not opened, and a draft is resumed through
 * Enter results rather than viewed.
 */
export function canViewResult(detail: ResultEntryContext | null | undefined) {
  const state = detail?.result?.state;
  return state === "entered" || state === "validated" || state === "released";
}

/* ------------------------------------------------------------------ *
 * Routes -- the BFF only, never Odoo
 * ------------------------------------------------------------------ */

export function openResultPath(requestId: number) {
  return `/api/laboratory/requests/${requestId}/result`;
}

export function saveResultPath(resultId: number) {
  return `/api/laboratory/results/${resultId}/save`;
}

export function enterResultPath(resultId: number) {
  return `/api/laboratory/results/${resultId}/enter`;
}

export function validateResultPath(resultId: number) {
  return `/api/laboratory/results/${resultId}/validate`;
}

/* ------------------------------------------------------------------ *
 * Validation (Slice 3B)
 * ------------------------------------------------------------------ */

/**
 * May the panel offer "Validate result"?
 *
 * THE LAB API'S VALIDATION POLICY, mirrored rather than invented: the request
 * is `in_progress`, the request has one operational result (no conflict), and
 * that result is `entered`. Every condition is re-checked by the Lab API under
 * lock, so a button drawn from stale data still gets a clean refusal.
 *
 * `sample_collected` is refused on purpose even though the model would accept
 * it: validating there delivers the test for a result that can then never be
 * released.
 */
export function canValidateResult(detail: ResultEntryContext | null | undefined) {
  if (!detail || detail.status !== "in_progress") return false;
  if (detail.result_conflict) return false;
  return detail.result?.state === "entered";
}

/** The review step's instruction, shown above Cancel / Validate result…. */
export const VALIDATION_REVIEW_TEXT =
  "Check each value, unit and reference range against the analyser output. " +
  "Validation confirms the result and records the test as performed. " +
  "Values cannot be changed afterwards.";

/** Stated beside the final confirmation, every time. */
export const VALIDATION_IRREVERSIBLE_TEXT =
  "This cannot be undone from the Laboratory Desk.";

/**
 * The final confirmation's question, naming exactly what is being validated:
 * "Validate LABRES0096 for Selam Tesfaye (HMS11834) — CBC?"
 *
 * The tests are the RESULT's own lines -- code when the catalogue has one,
 * name otherwise -- so the question describes the record that will change,
 * not the order it came from.
 */
export function validationConfirmText(
  request: { patient: { name: string; mrn: string | null } | null },
  result: { name: string; lines: { test: { name: string; code: string | null } }[] },
) {
  const patient = request.patient;
  const who = patient
    ? patient.mrn
      ? `${patient.name} (${patient.mrn})`
      : patient.name
    : "this patient";
  const tests = resultTestsLabel(result);
  return `Validate ${result.name} for ${who}${tests ? ` — ${tests}` : ""}?`;
}

/** The result's own tests, code when the catalogue has one: "CBC, Urinalysis". */
export function resultTestsLabel(result: {
  lines: { test: { name: string; code: string | null } }[];
}) {
  return result.lines
    .map((line) => line.test.code || line.test.name)
    .filter(Boolean)
    .join(", ");
}

/* ------------------------------------------------------------------ *
 * Release (Slice 3C)
 * ------------------------------------------------------------------ */

export function releaseResultPath(resultId: number) {
  return `/api/laboratory/results/${resultId}/release`;
}

/**
 * May the panel offer "Release result"?
 *
 * THE LAB API'S RELEASE POLICY, mirrored: the request is `in_progress`, it has
 * one operational result (no conflict), and that result is `validated`. A
 * completed or cancelled request, an entered or released result, and any
 * conflict all offer nothing. Re-checked by the Lab API under lock.
 */
export function canReleaseResult(detail: ResultEntryContext | null | undefined) {
  if (!detail || detail.status !== "in_progress") return false;
  if (detail.result_conflict) return false;
  return detail.result?.state === "validated";
}

/** The review step's instruction, shown above Cancel / Release result…. */
export const RELEASE_REVIEW_TEXT =
  "Review the validated result before release. " +
  "Releasing makes the result visible to the ordering clinician immediately. " +
  "Released results cannot be edited, cancelled or returned to draft.";

export const RELEASE_VISIBILITY_TEXT =
  "The result becomes visible on the Doctor Desk immediately.";

/** "Release LABRES0096 to Dr. Hana Bekele?" -- naming who will see it. */
export function releaseConfirmTitle(
  request: { ordering_physician: { name: string } | null },
  result: { name: string },
) {
  const clinician = request.ordering_physician?.name || "the ordering clinician";
  return `Release ${result.name} to ${clinician}?`;
}

/** "Selam Tesfaye (HMS11834) · CBC" -- whose result, and which tests. */
export function releaseConfirmIdentity(
  request: { patient: { name: string; mrn: string | null } | null },
  result: { lines: { test: { name: string; code: string | null } }[] },
) {
  const patient = request.patient;
  const who = patient
    ? patient.mrn
      ? `${patient.name} (${patient.mrn})`
      : patient.name
    : "Unknown patient";
  const tests = resultTestsLabel(result);
  return tests ? `${who} · ${tests}` : who;
}

/** "LABREQ0215 will be completed once all ordered tests are released." */
export function releaseCompletionText(request: { request_code: string }) {
  return `${request.request_code} will be completed once all ordered tests are released.`;
}

export type LabReleaseOutcome = {
  completed: boolean;
  request_state: string;
  blockers: string[];
};

/**
 * The panel's one-line account of a release, from the SERVER'S completion
 * outcome and nothing else:
 *
 *   "Released · Request completed"
 *   "Released · Request still in progress: 'CBC' result … is validated, not released"
 *
 * Blocker sentences are the model's own and name tests and result codes only.
 */
export function releaseOutcomeText(outcome: LabReleaseOutcome | null | undefined) {
  if (!outcome) return null;
  if (outcome.completed) return "Released · Request completed";
  const reasons = outcome.blockers.filter((reason) => reason.trim()).join("; ");
  return reasons
    ? `Released · Request still in progress: ${reasons}`
    : "Released · Request still in progress";
}

/* ------------------------------------------------------------------ *
 * The modal's draft
 * ------------------------------------------------------------------ */

export type LabResultLineDraft = {
  id: number;
  result_value: string;
  unit: string;
  reference_range: string;
  abnormal_flag: string;
  notes: string;
};

export type LabResultDraft = {
  interpretation: string;
  remarks: string;
  lines: LabResultLineDraft[];
};

/**
 * What a save or Mark entered reports back to the modal.
 *
 * The modal closes on `ok: true` from Mark entered and on nothing else; on
 * `ok: false` it keeps every typed value and shows `message` inside itself.
 */
export type LabResultOutcome =
  | { ok: true; result: LabResult }
  | { ok: false; message: string };

/** The model's default for abnormal_flag. Shown as a default, never as a choice. */
export const DEFAULT_ABNORMAL_FLAG = "normal";

function text(value: string | null | undefined) {
  return typeof value === "string" ? value : "";
}

/**
 * The editable copy of a server result.
 *
 * Nulls become empty strings for the inputs; values are otherwise copied
 * EXACTLY, whitespace included, so what the technician sees is what is stored.
 */
export function draftFromResult(result: LabResult): LabResultDraft {
  return {
    interpretation: text(result.interpretation),
    remarks: text(result.remarks),
    lines: result.lines.map((line: LabResultLine) => ({
      id: line.id,
      result_value: text(line.result_value),
      unit: text(line.unit),
      reference_range: text(line.reference_range),
      abnormal_flag: line.abnormal_flag || DEFAULT_ABNORMAL_FLAG,
      notes: text(line.notes),
    })),
  };
}

/** Whether the draft differs from the last persisted state. */
export function resultDraftChanged(
  baseline: LabResultDraft,
  draft: LabResultDraft,
): boolean {
  if (baseline.interpretation !== draft.interpretation) return true;
  if (baseline.remarks !== draft.remarks) return true;
  if (baseline.lines.length !== draft.lines.length) return true;
  return draft.lines.some((line, index) => {
    const before = baseline.lines[index];
    return (
      !before ||
      before.id !== line.id ||
      before.result_value !== line.result_value ||
      before.unit !== line.unit ||
      before.reference_range !== line.reference_range ||
      before.abnormal_flag !== line.abnormal_flag ||
      before.notes !== line.notes
    );
  });
}

/** Replace one line's field, immutably. */
export function patchDraftLine(
  draft: LabResultDraft,
  lineId: number,
  values: Partial<Omit<LabResultLineDraft, "id">>,
): LabResultDraft {
  return {
    ...draft,
    lines: draft.lines.map((line) =>
      line.id === lineId ? { ...line, ...values } : line,
    ),
  };
}

/**
 * The body /save and /enter accept -- and ONLY the allow-listed keys.
 *
 * Structure never travels: no test, no request line, no specimen, no sequence,
 * no state. An empty input is sent as null (clear the field); anything else is
 * sent exactly as typed, whitespace included, because whether "   " counts as a
 * value is the model's decision.
 */
export function resultPayload(draft: LabResultDraft): LabResultWritePayload {
  const orNull = (value: string) => (value === "" ? null : value);
  return {
    interpretation: orNull(draft.interpretation),
    remarks: orNull(draft.remarks),
    lines: draft.lines.map((line) => ({
      id: line.id,
      result_value: orNull(line.result_value),
      unit: orNull(line.unit),
      reference_range: orNull(line.reference_range),
      abnormal_flag: line.abnormal_flag,
      notes: orNull(line.notes),
    })),
  };
}

/* ------------------------------------------------------------------ *
 * Wording
 * ------------------------------------------------------------------ */

/**
 * The hint under an abnormal flag that is still "Normal".
 *
 * HONEST ABOUT THE DEFAULT. Odoo defaults every line to Normal and does not
 * require a choice, so the desk does not either -- but it must not let a
 * defaulted Normal read as a finding the technician asserted. The hint names
 * it as the default without inventing an "unassessed" value Odoo cannot store.
 */
export function abnormalFlagHint(value: string | null | undefined): string | null {
  return value === DEFAULT_ABNORMAL_FLAG
    ? "Normal is the default flag. Change it if this result is not normal."
    : null;
}

/** "Blood", or an em dash. The specimen is shown, never edited. */
export function sampleTypeLabel(value: string | null | undefined) {
  if (!value) return "—";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** "Selam Tesfaye · HMS11834 · LABREQ0215 · 1 test" */
export function resultModalSubtitle(request: {
  request_code: string;
  test_count: number;
  patient: { name: string; mrn: string | null } | null;
}) {
  return [
    request.patient?.name ?? null,
    request.patient?.mrn ?? null,
    request.request_code,
    request.test_count === 1 ? "1 test" : `${request.test_count} tests`,
  ]
    .filter((part): part is string => Boolean(part))
    .join(" · ");
}

/**
 * The line under the footer buttons that says where the draft stands.
 *
 * Never claims a save that did not happen: "Draft saved" appears only after a
 * save the server confirmed, and only while nothing has been typed since.
 */
export function draftStatusText(state: {
  readOnly: boolean;
  busy: "save" | "enter" | "validate" | "release" | null;
  changed: boolean;
  savedSinceOpen: boolean;
}): string {
  if (state.busy === "validate") return "Validating…";
  if (state.busy === "release") return "Releasing…";
  if (state.readOnly) return "Read only";
  if (state.busy === "save") return "Saving draft…";
  if (state.busy === "enter") return "Marking entered…";
  if (state.changed) return "Unsaved changes · Ctrl+Enter marks entered";
  if (state.savedSinceOpen) return "Draft saved · Ctrl+Enter marks entered";
  return "Ctrl+Enter marks entered";
}

/**
 * The message a result refusal shows inside the modal.
 *
 * The server's own sentence is preferred: it names the test that is missing a
 * value, or the state that refused. The fallback exists for transport failures,
 * where there is no server sentence at all.
 */
export function resultErrorMessage(
  serverMessage: string | null | undefined,
  fallback: string,
) {
  const trimmed = typeof serverMessage === "string" ? serverMessage.trim() : "";
  return trimmed ? trimmed : fallback;
}

/**
 * Codes after which the desk must RE-READ the request rather than trust its
 * screen: the result or the request moved on elsewhere.
 */
const RESULT_RECONCILE_CODES = new Set([
  "lab_result_entry_not_available",
  "lab_result_ambiguous",
  "lab_result_already_final",
  "lab_result_cancelled",
  "lab_result_not_editable",
  "lab_result_not_validatable",
  "lab_result_not_releasable",
  "lab_result_release_not_available",
  "lab_result_not_found",
  "lab_request_not_found",
  "invalid_workflow_state",
]);

export function shouldReconcileAfterResult(code: string | null | undefined) {
  return typeof code === "string" && RESULT_RECONCILE_CODES.has(code);
}

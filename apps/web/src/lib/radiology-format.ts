/**
 * Radiology ordering: display vocabulary, selection and payload construction.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * decides anything clinical or financial: confirmation, billing, coverage and
 * clearance all live in the radiology and billing models, whatever this file
 * says.
 *
 * A SIBLING OF laboratory-format, NOT A GENERALISATION OF IT. The two order
 * kinds look alike and are not the same: radiology carries a modality, a body
 * part and a contrast flag, its status vocabulary has an extra scheduling step,
 * and its request has two free-text fields where laboratory has one. Folding
 * them into one generic module would mean a union type at every call site and a
 * shared file that neither slice owns.
 */
// TYPE-ONLY, and it has to stay that way -- TypeScript erases these, which is
// what lets node:test run radiology-format.test.ts with no resolver and no
// transform.
import type {
  DoctorRadOrder,
  RadExamOption,
  RadOrderForm,
  RadOrderRequest,
} from "@/types/doctor-radiology";

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

/**
 * Labels for the status keys the SERVER produces. Restated rather than rendered
 * from `status_label` alone so the client can style each state, but the
 * server's own label is what the UI displays -- these are the fallback and the
 * styling key.
 */
const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  awaiting_clearance: "Awaiting clearance",
  awaiting_scheduling: "Awaiting scheduling",
  scheduled: "Scheduled",
  in_progress: "Imaging in progress",
  result_available: "Result available",
  cancelled: "Cancelled",
};

const PRIORITY_LABELS: Record<string, string> = {
  routine: "Routine",
  urgent: "Urgent",
  stat: "STAT",
};

const MODALITY_LABELS: Record<string, string> = {
  xray: "X-Ray",
  ultrasound: "Ultrasound",
  ct: "CT",
  mri: "MRI",
  fluoroscopy: "Fluoroscopy",
  mammography: "Mammography",
  other: "Other",
};

export function radStatusLabel(status: string | null | undefined) {
  if (!status) return "—";
  return STATUS_LABELS[status] ?? status;
}

export function radPriorityLabel(priority: string | null | undefined) {
  if (!priority) return "Routine";
  return PRIORITY_LABELS[priority] ?? priority;
}

export function modalityLabel(modality: string | null | undefined) {
  if (!modality) return "";
  return MODALITY_LABELS[modality] ?? modality;
}

/** A status that no longer moves. Used to stop offering actions on it. */
export function isTerminalStatus(status: string | null | undefined) {
  return status === "cancelled" || status === "result_available";
}

/**
 * True while the study is held up by money.
 *
 * The desk uses this to tint one badge, never to decide anything: the
 * authoritative gate is Odoo's action_mark_in_progress(), which re-checks
 * clearance whatever this returns.
 */
export function isAwaitingClearance(status: string | null | undefined) {
  return status === "awaiting_clearance";
}

/** Exam name with its catalogue code, when there is one. */
export function examLabel(exam: { name: string; code: string | null }) {
  return exam.code ? `${exam.name} (${exam.code})` : exam.name;
}

/**
 * The modality and body part, as one short line.
 *
 * Both are optional on hospital.radiology.exam, so every combination has to
 * render: an ultrasound with no body part is "Ultrasound", a body part with no
 * modality is the body part alone, and neither is the empty string rather than
 * a stray separator.
 */
export function examContext(exam: {
  modality: string | null;
  body_part: string | null;
}) {
  const parts = [modalityLabel(exam.modality), exam.body_part ?? ""].filter(
    (part) => part.length > 0,
  );
  return parts.join(" · ");
}

/** The one-line summary of what an order contains. */
export function orderExamSummary(order: DoctorRadOrder) {
  return order.exams.map((exam) => exam.name).join(" · ");
}

/** The same summary for a request still being composed. */
export function selectionSummary(selected: { name: string }[]) {
  return selected.map((exam) => exam.name).join(" · ");
}

/** "1 study" / "3 studies". */
export function examCountLabel(count: number) {
  return count === 1 ? "1 study" : `${count} studies`;
}

/**
 * True when any study in the order needs contrast.
 *
 * Surfaced on the order row because contrast is patient preparation -- fasting,
 * a cannula, a renal-function check -- and the doctor who ordered it is the one
 * who has to tell the patient before they leave the room.
 */
export function orderNeedsContrast(order: DoctorRadOrder) {
  return order.exams.some((exam) => exam.contrast_required);
}

/* ------------------------------------------------------------------ *
 * Selection
 * ------------------------------------------------------------------ */

/**
 * Add a study to the pending selection, DE-DUPLICATED.
 *
 * Ordering the same study twice in one submission would raise two charges for
 * one scan, so the same id can only appear once. The server de-duplicates
 * again; this is what stops the doctor seeing a duplicate row and wondering
 * which one is real.
 */
export function addExam(selected: RadExamOption[], exam: RadExamOption) {
  if (selected.some((entry) => entry.id === exam.id)) return selected;
  return [...selected, exam];
}

export function removeExam(selected: RadExamOption[], examId: number) {
  return selected.filter((entry) => entry.id !== examId);
}

export function isSelected(selected: RadExamOption[], examId: number) {
  return selected.some((entry) => entry.id === examId);
}

/** True when anything in the pending selection needs contrast. */
export function selectionNeedsContrast(selected: RadExamOption[]) {
  return selected.some((exam) => exam.contrast_required);
}

/* ------------------------------------------------------------------ *
 * Payload
 * ------------------------------------------------------------------ */

export const EMPTY_ORDER_FORM: RadOrderForm = {
  priority: "routine",
  clinical_indication: "",
  instructions: "",
  diagnosis_id: null,
};

/**
 * The order body.
 *
 * `request_token` IS ALWAYS SENT. It is what makes a double-clicked Place Order
 * return the first request instead of raising a second identical set of charges
 * against the same encounter -- which matters more here than for laboratory,
 * because an imaging charge is an order of magnitude larger.
 *
 * Ownership is absent by construction: patient, physician, encounter,
 * appointment and consultation are all derived server-side, and the API rejects
 * them by name if a client ever sends one.
 */
export function buildOrderPayload(
  selected: RadExamOption[],
  form: RadOrderForm,
  requestToken: string,
): RadOrderRequest {
  const payload: RadOrderRequest = {
    exams: selected.map((exam) => exam.id),
    request_token: requestToken,
    priority: form.priority,
  };
  if (form.clinical_indication.trim()) {
    payload.clinical_indication = form.clinical_indication;
  }
  if (form.instructions.trim()) {
    payload.instructions = form.instructions;
  }
  if (form.diagnosis_id) {
    payload.diagnosis_id = form.diagnosis_id;
  }
  return payload;
}

/** A submission needs at least one study. */
export function canSubmitOrder(selected: RadExamOption[], busy: boolean) {
  return selected.length > 0 && !busy;
}

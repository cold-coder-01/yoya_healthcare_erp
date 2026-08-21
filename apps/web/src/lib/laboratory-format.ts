/**
 * Laboratory ordering: display vocabulary, selection and payload construction.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * decides anything clinical or financial: confirmation, billing, coverage and
 * clearance all live in the laboratory and billing models, whatever this file
 * says.
 */
// TYPE-ONLY, and it has to stay that way -- TypeScript erases these, which is
// what lets node:test run laboratory-format.test.ts with no resolver and no
// transform.
import type {
  DoctorLabOrder,
  LabOrderForm,
  LabOrderRequest,
  LabTestOption,
} from "@/types/doctor-laboratory";

/** Runtime copy of the ORDERS sub-sections. See the note in the types file. */
export const ORDER_KINDS: ReadonlyArray<{
  key: string;
  label: string;
  live: boolean;
}> = [
  { key: "laboratory", label: "Laboratory", live: true },
  { key: "radiology", label: "Radiology", live: false },
  { key: "medication", label: "Medication", live: false },
  { key: "procedure", label: "Procedure", live: false },
];

/** True for an order kind this slice actually implements. */
export function isLiveOrderKind(key: string): boolean {
  return ORDER_KINDS.some((kind) => kind.key === key && kind.live);
}

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

/**
 * Labels for the status keys the SERVER produces. Restated rather than
 * rendered from `status_label` alone so the client can style each state, but
 * the server's own label is what the UI displays -- these are the fallback and
 * the styling key.
 */
const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  awaiting_clearance: "Awaiting clearance",
  ready_for_collection: "Ready for collection",
  collected: "Sample collected",
  result_pending: "Result pending",
  result_available: "Result available",
  cancelled: "Cancelled",
};

const PRIORITY_LABELS: Record<string, string> = {
  routine: "Routine",
  urgent: "Urgent",
  stat: "STAT",
};

export function labStatusLabel(status: string | null | undefined) {
  if (!status) return "—";
  return STATUS_LABELS[status] ?? status;
}

export function labPriorityLabel(priority: string | null | undefined) {
  if (!priority) return "Routine";
  return PRIORITY_LABELS[priority] ?? priority;
}

/** A status that no longer moves. Used to stop offering actions on it. */
export function isTerminalStatus(status: string | null | undefined) {
  return status === "cancelled" || status === "result_available";
}

/** Test name with its catalogue code, when there is one. */
export function testLabel(test: { name: string; code: string | null }) {
  return test.code ? `${test.name} (${test.code})` : test.name;
}

/** The one-line summary of what an order contains. */
export function orderTestSummary(order: DoctorLabOrder) {
  return order.tests.map((test) => test.name).join(" · ");
}

/* ------------------------------------------------------------------ *
 * Selection
 * ------------------------------------------------------------------ */

/**
 * Add a test to the pending selection, DE-DUPLICATED.
 *
 * Ordering the same test twice in one submission would raise two charges for
 * one test, so the same id can only appear once. The server de-duplicates
 * again; this is what stops the doctor seeing a duplicate row and wondering
 * which one is real.
 */
export function addTest(selected: LabTestOption[], test: LabTestOption) {
  if (selected.some((entry) => entry.id === test.id)) return selected;
  return [...selected, test];
}

export function removeTest(selected: LabTestOption[], testId: number) {
  return selected.filter((entry) => entry.id !== testId);
}

export function isSelected(selected: LabTestOption[], testId: number) {
  return selected.some((entry) => entry.id === testId);
}

/* ------------------------------------------------------------------ *
 * Payload
 * ------------------------------------------------------------------ */

export const EMPTY_ORDER_FORM: LabOrderForm = {
  priority: "routine",
  clinical_notes: "",
  diagnosis_id: null,
};

/**
 * The order body.
 *
 * `request_token` IS ALWAYS SENT. It is what makes a double-clicked Place
 * Order return the first request instead of raising a second identical set of
 * charges against the same encounter.
 *
 * Ownership is absent by construction: patient, physician, encounter,
 * appointment and consultation are all derived server-side, and the API
 * rejects them by name if a client ever sends one.
 */
export function buildOrderPayload(
  selected: LabTestOption[],
  form: LabOrderForm,
  requestToken: string,
): LabOrderRequest {
  const payload: LabOrderRequest = {
    tests: selected.map((test) => test.id),
    request_token: requestToken,
    priority: form.priority,
  };
  if (form.clinical_notes.trim()) {
    payload.clinical_notes = form.clinical_notes;
  }
  if (form.diagnosis_id) {
    payload.diagnosis_id = form.diagnosis_id;
  }
  return payload;
}

/** A submission needs at least one test. */
export function canSubmitOrder(selected: LabTestOption[], busy: boolean) {
  return selected.length > 0 && !busy;
}

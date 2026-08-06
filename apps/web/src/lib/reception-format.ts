/**
 * Display formatting for reception payloads.
 *
 * The Odoo reception serializers return raw selection KEYS (`o_positive`,
 * `awaiting_payment`, `follow_up`) and plain numbers. Nothing here changes a
 * value that is sent back to the API; these are labels only.
 *
 * Selection keys are typed as plain `string` rather than unions on purpose:
 * they cross a network boundary, so a value outside the known set is a real
 * runtime possibility. Every map below therefore degrades to a humanised
 * fallback instead of rendering blank.
 */

const MONEY = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** "1,500.00 ETB" */
export function formatEtb(
  value: number | null | undefined,
  currency = "ETB",
): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "—";
  }
  return `${MONEY.format(value)} ${currency}`;
}

function humanise(value: string): string {
  return value
    .split("_")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function labelFrom(
  map: Record<string, string>,
  value: string | null | undefined,
  fallback = "—",
): string {
  if (!value) {
    return fallback;
  }
  return map[value] ?? humanise(value);
}

const VISIT_TYPE_LABELS: Record<string, string> = {
  routine: "Routine",
  emergency: "Emergency",
  follow_up: "Follow-up",
  referral: "Referral",
};

export const VISIT_TYPE_OPTIONS = [
  { value: "routine", label: "Routine" },
  { value: "emergency", label: "Emergency" },
  { value: "follow_up", label: "Follow-up" },
  { value: "referral", label: "Referral" },
];

export function formatVisitType(value: string | null | undefined): string {
  return labelFrom(VISIT_TYPE_LABELS, value);
}

/** hospital.patient.card.issue.state */
const CARD_STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  charged: "Charged",
  paid: "Paid",
  issued: "Issued",
  waived: "Waived",
  deferred: "Deferred",
  cancelled: "Cancelled",
};

export function formatCardStatus(value: string | null | undefined): string {
  return labelFrom(CARD_STATUS_LABELS, value, "No card");
}

/**
 * hospital.billing.account.financial_clearance_state, surfaced by the
 * encounter's live reception clearance. Distinct concept from queue stage.
 */
const CLEARANCE_LABELS: Record<string, string> = {
  not_required: "Not Required",
  pending: "Pending",
  cleared: "Cleared",
  credit_authorized: "Payer Authorized",
  emergency_bypass: "Emergency Bypass",
};

export function formatClearanceState(value: string | null | undefined): string {
  return labelFrom(CLEARANCE_LABELS, value, "Unknown");
}

export const CLEARANCE_STATE_OPTIONS = Object.entries(CLEARANCE_LABELS).map(
  ([value, label]) => ({ value, label }),
);

/** hospital.appointment.clinical_queue_stage */
const QUEUE_STAGE_LABELS: Record<string, string> = {
  registered: "Registered",
  awaiting_payment: "Awaiting Payment",
  awaiting_triage: "Awaiting Triage",
  in_triage: "In Triage",
  awaiting_doctor: "Awaiting Doctor",
  in_consultation: "In Consultation",
  completed: "Completed",
  cancelled: "Cancelled",
};

export function formatQueueStage(value: string | null | undefined): string {
  return labelFrom(QUEUE_STAGE_LABELS, value, "Unknown");
}

export const QUEUE_STAGE_OPTIONS = [
  "awaiting_payment",
  "awaiting_triage",
  "in_triage",
  "awaiting_doctor",
  "in_consultation",
  "completed",
  "cancelled",
].map((value) => ({ value, label: QUEUE_STAGE_LABELS[value] }));

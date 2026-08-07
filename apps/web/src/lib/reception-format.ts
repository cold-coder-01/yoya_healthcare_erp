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

import { HOSPITAL_TIME_ZONE } from "@/lib/clinical-format";

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

export const GENDER_OPTIONS = [
  { value: "male", label: "Male" },
  { value: "female", label: "Female" },
];

const GENDER_LABELS: Record<string, string> = {
  male: "Male",
  female: "Female",
};

export function formatGender(value: string | null | undefined): string {
  return labelFrom(GENDER_LABELS, value);
}

/** hospital.patient.blood_group keys -> clinical notation. */
const BLOOD_GROUP_LABELS: Record<string, string> = {
  a_positive: "A+",
  a_negative: "A-",
  b_positive: "B+",
  b_negative: "B-",
  ab_positive: "AB+",
  ab_negative: "AB-",
  o_positive: "O+",
  o_negative: "O-",
  unknown: "Unknown",
};

export const BLOOD_GROUP_OPTIONS = Object.entries(BLOOD_GROUP_LABELS).map(
  ([value, label]) => ({ value, label }),
);

export function formatBloodGroupKey(value: string | null | undefined): string {
  return labelFrom(BLOOD_GROUP_LABELS, value);
}

const CHARGE_CATEGORY_LABELS: Record<string, string> = {
  patient_card: "Patient Card",
  consultation: "Consultation",
};

export function formatChargeCategory(value: string | null | undefined): string {
  return labelFrom(CHARGE_CATEGORY_LABELS, value, "Other");
}

const PAYMENT_STATE_LABELS: Record<string, string> = {
  unpaid: "Not Funded",
  partially_paid: "Partially Funded",
  paid: "Funded",
  refunded: "Refunded",
};

export function formatPaymentState(value: string | null | undefined): string {
  return labelFrom(PAYMENT_STATE_LABELS, value);
}

const AUTHORIZATION_STATE_LABELS: Record<string, string> = {
  not_required: "Not Required",
  pending: "Pending",
  authorized: "Authorized",
  rejected: "Rejected",
  bypassed: "Bypassed (Emergency)",
};

export function formatAuthorizationState(
  value: string | null | undefined,
): string {
  return labelFrom(AUTHORIZATION_STATE_LABELS, value);
}

const ENCOUNTER_STATE_LABELS: Record<string, string> = {
  planned: "Planned",
  checked_in: "Checked In",
  active: "Active",
  completed: "Completed",
  closed: "Closed",
  cancelled: "Cancelled",
};

export function formatEncounterState(value: string | null | undefined): string {
  return labelFrom(ENCOUNTER_STATE_LABELS, value);
}

const SEVERITY_LABELS: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

export function formatSeverity(value: string | null | undefined): string {
  return labelFrom(SEVERITY_LABELS, value);
}

/* ------------------------------------------------------------------ *
 * Appointment datetime
 * ------------------------------------------------------------------ */

function twoDigit(value: number): string {
  return String(value).padStart(2, "0");
}

/**
 * Offset in minutes between an instant and its hospital-local wall clock.
 *
 * Computed from Intl rather than hardcoding +03:00, so the conversion stays
 * correct if the hospital timezone is ever changed.
 */
function hospitalOffsetMinutes(instant: Date): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: HOSPITAL_TIME_ZONE,
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);

  const get = (type: string) =>
    Number(parts.find((part) => part.type === type)?.value ?? "0");

  // Intl renders hour 24 for midnight in some environments.
  const hour = get("hour") % 24;
  const asIfUtc = Date.UTC(
    get("year"),
    get("month") - 1,
    get("day"),
    hour,
    get("minute"),
    get("second"),
  );
  return (asIfUtc - instant.getTime()) / 60000;
}

/**
 * Convert a `datetime-local` value (hospital wall time) to the naive UTC
 * string Odoo stores: "YYYY-MM-DD HH:MM:SS".
 *
 * The browser's own timezone is never used: a receptionist working on a laptop
 * still set to another zone would otherwise book the wrong slot.
 */
export function hospitalLocalToOdooUtc(
  localValue: string | null | undefined,
): string | undefined {
  if (!localValue) {
    return undefined;
  }
  const match = localValue.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/,
  );
  if (!match) {
    return undefined;
  }
  const [, year, month, day, hour, minute, second] = match;
  const naive = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second ?? "0"),
  );
  // Addis Ababa has no DST, so a single correction is exact.
  const offset = hospitalOffsetMinutes(new Date(naive));
  const utc = new Date(naive - offset * 60000);

  return (
    `${utc.getUTCFullYear()}-${twoDigit(utc.getUTCMonth() + 1)}-` +
    `${twoDigit(utc.getUTCDate())} ${twoDigit(utc.getUTCHours())}:` +
    `${twoDigit(utc.getUTCMinutes())}:${twoDigit(utc.getUTCSeconds())}`
  );
}

/** Current hospital-local time as a `datetime-local` input value. */
export function hospitalNowLocalInput(): string {
  const now = new Date();
  const offset = hospitalOffsetMinutes(now);
  const local = new Date(now.getTime() + offset * 60000);
  return (
    `${local.getUTCFullYear()}-${twoDigit(local.getUTCMonth() + 1)}-` +
    `${twoDigit(local.getUTCDate())}T${twoDigit(local.getUTCHours())}:` +
    `${twoDigit(local.getUTCMinutes())}`
  );
}

/**
 * Display vocabulary and derived affordances for the Doctor Desk.
 *
 * Pure presentation. Nothing here writes, and nothing here is authorization --
 * see the note on `visitReadiness`.
 */
import type {
  DoctorQueueRow,
  DoctorReadiness,
  DoctorTriageStatus,
  DoctorVitals,
} from "@/types/doctor";

const LABELS: Record<string, string> = {
  // hospital.appointment.state
  draft: "Draft",
  confirmed: "Confirmed",
  in_consultation: "In Consultation",
  done: "Completed",
  cancelled: "Cancelled",

  // front_desk_stage (rendered once the backend supplies it)
  new: "Intake",
  intake: "Intake",
  triage: "Triage",
  awaiting_cashier: "Cashier",
  ready_doctor: "Ready",
  completed: "Completed",

  // triage_priority
  routine: "Routine",
  urgent: "Urgent",
  emergency: "Emergency",

  // visit_type
  follow_up: "Follow Up",
  referral: "Referral",

  // encounter.payer_type -- sponsorship CATEGORY only, never a payer name
  self_pay: "Self Pay",
  insurance: "Insurance",
  credit: "Credit",

  // triage status
  not_started: "Not started",
  waiting: "Waiting",
  in_progress: "In progress",
};

export function doctorLabel(value: string | null | undefined, fallback = "-") {
  if (!value) return fallback;
  return (
    LABELS[value] ??
    value
      .split("_")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
}

export function compactGender(value: string | null | undefined) {
  if (value === "male") return "M";
  if (value === "female") return "F";
  return value ? value.charAt(0).toUpperCase() : "-";
}

/* ------------------------------------------------------------------ *
 * Worklist buckets
 * ------------------------------------------------------------------ */

/**
 * The vendor OPD screen files every row into All / Wait / Review / Finished,
 * and doctors read that strip before anything else, so the vocabulary is kept
 * exactly. The MAPPING is ours, and is built only from facts the payload
 * actually carries:
 *
 *   wait      triage is not finished yet -- the patient is not workable
 *   review    triage is done and the visit is still confirmed -- the doctor's
 *             actual working set
 *   finished  the visit has moved to consultation or beyond
 *
 * This is a VIEW FILTER over rows Odoo already returned. It grants nothing and
 * hides nothing that scope did not already hide.
 */
export const DOCTOR_BUCKETS = [
  { key: "all", label: "All" },
  { key: "wait", label: "Wait" },
  { key: "review", label: "Review" },
  { key: "finished", label: "Finished" },
] as const;

export type DoctorBucket = (typeof DOCTOR_BUCKETS)[number]["key"];

export function bucketOf(row: DoctorQueueRow): Exclude<DoctorBucket, "all"> {
  if (row.state === "in_consultation" || row.state === "done") return "finished";
  return row.triage_status === "completed" ? "review" : "wait";
}

export function bucketCounts(rows: DoctorQueueRow[]) {
  const counts = { all: rows.length, wait: 0, review: 0, finished: 0 };
  for (const row of rows) counts[bucketOf(row)] += 1;
  return counts;
}

/** Short Stat cell, matching the vendor column's three-letter register. */
export function statLabel(row: DoctorQueueRow) {
  const bucket = bucketOf(row);
  if (bucket === "finished") return row.state === "done" ? "Done" : "Cons";
  if (bucket === "review") return "Rev";
  return "Wait";
}

/* ------------------------------------------------------------------ *
 * Readiness
 * ------------------------------------------------------------------ */

/**
 * Why the Start Consultation button is or is not offered.
 *
 * AFFORDANCE, NOT AUTHORIZATION. This exists so a doctor is told the reason
 * before clicking instead of after, and so the desk does not invite an action
 * that is certain to be refused. Every condition below is enforced again,
 * independently and authoritatively, by
 * hospital.appointment.action_start_consultation() at the model layer.
 *
 * It is deliberately CONSERVATIVE in one direction only: it may report "not
 * ready" for a visit Odoo would in fact accept, and the doctor can still send
 * the request and get Odoo's own answer. It must never report ready for one
 * Odoo would refuse, which is why the assigned-doctor check is NOT attempted
 * here -- the browser cannot see the four groups that gate it, and guessing
 * would be the one failure mode that matters.
 */
export function visitReadiness(input: {
  state: string | null;
  triageStatus: DoctorTriageStatus;
  clearanceBlocked: boolean;
  clearanceMessage: string | null;
}): DoctorReadiness {
  if (input.state === "in_consultation") {
    return { ready: false, reason: "Consultation already in progress.", gate: "state" };
  }
  if (input.state === "done") {
    return { ready: false, reason: "This visit is already completed.", gate: "state" };
  }
  if (input.state === "cancelled") {
    return { ready: false, reason: "This visit was cancelled.", gate: "state" };
  }
  if (input.state !== "confirmed") {
    return {
      ready: false,
      reason: "The visit is not confirmed yet.",
      gate: "state",
    };
  }
  if (input.triageStatus !== "completed") {
    return {
      ready: false,
      reason: "Nursing triage must be completed before consultation can start.",
      gate: "triage",
    };
  }
  if (input.clearanceBlocked) {
    return {
      ready: false,
      reason:
        input.clearanceMessage ??
        "Financial clearance is required before consultation can start.",
      gate: "clearance",
    };
  }
  return { ready: true, reason: null, gate: null };
}

/* ------------------------------------------------------------------ *
 * Vitals
 * ------------------------------------------------------------------ */

/** A vital reading with its unit, or an em dash. Never a zero for "unknown". */
export function vitalText(
  value: number | null | undefined,
  unit = "",
  digits = 1,
): string {
  if (value === null || value === undefined) return "—";
  const rounded = Number.isInteger(value) ? String(value) : value.toFixed(digits);
  return unit ? `${rounded} ${unit}` : rounded;
}

/** "120/80", or an em dash when either half is missing. */
export function bloodPressureText(vitals: DoctorVitals): string {
  const { systolic_bp: systolic, diastolic_bp: diastolic } = vitals;
  if (systolic === null || diastolic === null) return "—";
  return `${Math.round(systolic)}/${Math.round(diastolic)}`;
}

/** True when any vital was recorded, so an empty grid can say so honestly. */
export function hasAnyVital(vitals: DoctorVitals): boolean {
  return (
    Object.entries(vitals).filter(
      ([key, value]) => key !== "bmi_state" && value !== null && value !== undefined,
    ).length > 0
  );
}

export function displayText(
  value: string | number | null | undefined,
  fallback = "—",
) {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

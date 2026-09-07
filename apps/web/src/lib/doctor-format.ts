/**
 * Display vocabulary and derived affordances for the Doctor Desk.
 *
 * Pure presentation. Nothing here writes, and nothing here is authorization --
 * see the note on `visitReadiness`.
 */
// TYPE-ONLY, and it has to stay that way. TypeScript erases this statement
// entirely, which is what lets node:test run doctor-format.test.ts directly
// with no resolver, no transform and no config -- the `@/` alias is a
// tsconfig path Node knows nothing about. A runtime import from here would
// make this module untestable under `npm test`.
import type {
  DoctorQueueRow,
  DoctorQueueStage,
  DoctorReadiness,
  DoctorVitals,
} from "@/types/doctor";

/**
 * THE stage that means workable: hospital.appointment.front_desk_stage ==
 * 'ready_doctor'. Compared against, never recomputed.
 *
 * Declared here rather than in types/doctor.ts so that module stays purely
 * declarative -- see the note on the import above. It mirrors READY_STAGE in
 * yoya_emr_api/services/doctor_serializers.py, which is the authority.
 */
export const READY_STAGE = "ready_doctor";

const LABELS: Record<string, string> = {
  // hospital.appointment.state
  draft: "Draft",
  confirmed: "Confirmed",
  in_consultation: "In Consultation",
  done: "Completed",
  cancelled: "Cancelled",

  // front_desk_stage -- authoritative, supplied by the doctor controller
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
 * exactly. The MAPPING is now driven by the AUTHORITATIVE stage:
 *
 *   wait      any stage before ready_doctor that still belongs in the doctor's
 *             day -- intake, triage, awaiting_cashier
 *   review    queue_stage === ready_doctor AND the visit is still confirmed --
 *             the doctor's actual working set
 *   open      in_consultation -- the patient the doctor is with right now
 *   finished  completed -- signed off
 *
 * WHAT THIS REPLACED, AND WHY. Review used to be `triage_status === "completed"`.
 * Triage completion is NOT readiness: a patient whose triage is done but who
 * still owes money sits at awaiting_cashier, and the old rule filed them under
 * Review -- inviting the doctor to call them through while the desk still had
 * them. front_desk_stage already answers this question correctly, having
 * consulted encounter-wide clearance, so the answer is read rather than
 * re-derived.
 *
 * Mirrors bucket_of() in yoya_emr_api/services/doctor_serializers.py, which
 * computes the counters server-side from the same rule.
 *
 * This is a VIEW FILTER over rows Odoo already returned. It grants nothing and
 * hides nothing that scope did not already hide.
 */
export const DOCTOR_BUCKETS = [
  { key: "all", label: "All" },
  { key: "wait", label: "Wait" },
  { key: "review", label: "Review" },
  { key: "open", label: "Open" },
  { key: "finished", label: "Finished" },
] as const;

export type DoctorBucket = (typeof DOCTOR_BUCKETS)[number]["key"];

/*
  'in_consultation' USED TO LIVE IN FINISHED, and Slice 4 is why it does not any
  more: once a consultation can be completed, Finished would hold both the
  patient the doctor is with and the patient they signed off, indistinguishably
  -- in the one tab a doctor uses to confirm they have nothing left to finish.

  Mirrors OPEN_STAGES / FINISHED_STAGES in doctor_serializers.py. The two are
  asserted equal in the backend tests rather than trusted to stay in step.
*/
const OPEN_STAGES: readonly DoctorQueueStage[] = ["in_consultation"];
const FINISHED_STAGES: readonly DoctorQueueStage[] = ["completed"];

export function bucketOf(row: DoctorQueueRow): Exclude<DoctorBucket, "all"> {
  // Order matters: open before finished, so an in-consultation visit never
  // falls through into the signed-off tab.
  if (OPEN_STAGES.includes(row.queue_stage)) return "open";
  if (FINISHED_STAGES.includes(row.queue_stage)) return "finished";
  if (row.queue_stage === READY_STAGE && row.state === "confirmed") return "review";
  return "wait";
}

export function bucketCounts(rows: DoctorQueueRow[]) {
  const counts = { all: rows.length, wait: 0, review: 0, open: 0, finished: 0 };
  for (const row of rows) counts[bucketOf(row)] += 1;
  return counts;
}

/** Short Stat cell, matching the vendor column's three-letter register. */
export function statLabel(row: DoctorQueueRow) {
  const bucket = bucketOf(row);
  if (bucket === "finished") return "Done";
  if (bucket === "open") return "Cons";
  if (bucket === "review") return "Rev";
  // Distinguishes the two reasons a patient is waiting, which is the single
  // most useful thing this column can say: the desk still has them, or triage
  // does. Both remain non-workable.
  if (row.queue_stage === "awaiting_cashier") return "Cash";
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
 * READINESS IS THE AUTHORITATIVE STAGE, AND NOTHING ELSE.
 * This function no longer reconstructs readiness from triage status and a
 * billing flag. It asks one question -- is queue_stage `ready_doctor` -- and
 * uses the stage it is NOT at to explain why. That is the whole point of the
 * integration: front_desk_stage has already composed appointment state, the
 * nursing evaluation and encounter-WIDE financial clearance, and re-deriving
 * any of those in a browser can only produce a different, worse answer.
 *
 * `canStart` is the server's own affordance verdict, which additionally folds
 * in whether THIS user is the assigned doctor -- a question the browser cannot
 * answer and must not guess. It is ANDed in, so this can only ever be more
 * conservative than the stage alone.
 *
 * THERE IS DELIBERATELY NO `clearanceBlocked` INPUT. It would be dead weight:
 * clearance.blocked is hospital.appointment._is_payment_blocking(), and
 * front_desk_stage returns ready_doctor only when that same predicate is
 * false. The two cannot disagree, so testing both would suggest a second gate
 * where there is one. `clearanceReason` is still taken -- not to decide
 * anything, only to quote Odoo's allowlisted sentence when the stage is
 * awaiting_cashier.
 */
export function visitReadiness(input: {
  state: string | null;
  queueStage: DoctorQueueStage;
  canStart: boolean;
  clearanceReason: string | null;
}): DoctorReadiness {
  if (input.state === "in_consultation" || input.queueStage === "in_consultation") {
    return { ready: false, reason: "Consultation already in progress.", gate: "state" };
  }
  if (input.state === "done" || input.queueStage === "completed") {
    return { ready: false, reason: "This visit is already completed.", gate: "state" };
  }
  if (input.state === "cancelled" || input.queueStage === "cancelled") {
    return { ready: false, reason: "This visit was cancelled.", gate: "state" };
  }
  if (input.state !== "confirmed") {
    return { ready: false, reason: "The visit is not confirmed yet.", gate: "state" };
  }

  if (input.queueStage !== READY_STAGE) {
    return {
      ready: false,
      reason: stageBlockReason(input.queueStage, input.clearanceReason),
      gate: input.queueStage === "awaiting_cashier" ? "clearance" : "stage",
    };
  }

  // The stage says workable. The server may still withhold the action because
  // this user is not the doctor the visit is assigned to.
  if (!input.canStart) {
    return {
      ready: false,
      reason:
        "This visit is ready, but it is not assigned to you. Only the assigned " +
        "doctor, a Hospital Manager or a System Administrator may start it.",
      gate: "assignment",
    };
  }

  return { ready: true, reason: null, gate: null };
}

/** Operator-facing sentence for a stage that is not yet ready_doctor. */
function stageBlockReason(
  stage: DoctorQueueStage,
  clearanceReason: string | null,
): string {
  if (stage === "awaiting_cashier") {
    // Odoo's allowlisted sentence when it has one. It is a fixed string from
    // DOCTOR_CLEARANCE_REASONS and carries no figure and no payer name.
    return (
      clearanceReason ??
      "Financial clearance is still pending at the front desk."
    );
  }
  if (stage === "triage") {
    return "Nursing triage is in progress.";
  }
  // new / intake
  return "Nursing triage must be completed before consultation can start.";
}

/* ------------------------------------------------------------------ *
 * Vitals
 * ------------------------------------------------------------------ */

/**
 * ZERO MEANS NOT RECORDED, for every numeric vital on this screen.
 *
 * WHY THE CLIENT DECIDES THIS. hospital.patient.evaluation stores each vital as
 * a plain Float with no null sentinel, so a reading nobody took is stored as
 * 0.0 and is indistinguishable in the column from a measured zero.
 * api_response.float_value forwards the raw number deliberately -- its comment
 * says "let the client decide" -- and this is the client deciding.
 *
 * WHY 0 IS ALWAYS "NOT RECORDED" HERE AND NEVER A READING. Every field this
 * guard covers is physiologically impossible at zero in a living patient:
 * weight, height, temperature, heart rate, respiratory rate, systolic and
 * diastolic pressure, SpO2, RBS, head circumference and BMI. There is no
 * measurement being suppressed, because there is no patient in front of a
 * doctor with a pulse of 0 or an oxygen saturation of 0%.
 *
 * PAIN SCORE IS NOT ONE OF THESE, and that is the exception that makes the
 * rule safe to state. A pain level of 0 is a real, meaningful answer -- no
 * pain -- and it never reaches this guard: hospital.patient.evaluation stores
 * pain_level as a Selection keyed '0'..'10', the serializer sends it through
 * selection_value(), and it arrives as a string.
 *
 * THE ALTERNATIVE WAS WORSE. Before this, a triage record saved with vitals
 * left blank rendered "BP 0/0 · Pulse 0 bpm · SpO2 0 %" on the Doctor Desk --
 * not a missing reading, but a fabricated one describing a dead patient.
 */
function isRecordedVital(value: number | null | undefined): value is number {
  return value !== null && value !== undefined && value !== 0;
}

/** A vital reading with its unit, or an em dash. Never a zero for "unknown". */
export function vitalText(
  value: number | null | undefined,
  unit = "",
  digits = 1,
): string {
  if (!isRecordedVital(value)) return "—";
  const rounded = Number.isInteger(value) ? String(value) : value.toFixed(digits);
  return unit ? `${rounded} ${unit}` : rounded;
}

/**
 * "120/80", or an em dash when either half is missing.
 *
 * BOTH HALVES MUST BE REAL. "120/0" is not a blood pressure with a missing
 * diastolic; it is a reading that would send a clinician looking for shock.
 */
export function bloodPressureText(vitals: DoctorVitals): string {
  const { systolic_bp: systolic, diastolic_bp: diastolic } = vitals;
  if (!isRecordedVital(systolic) || !isRecordedVital(diastolic)) return "—";
  return `${Math.round(systolic)}/${Math.round(diastolic)}`;
}

/**
 * True when any vital was recorded, so an empty grid can say so honestly.
 *
 * Uses the SAME recorded-ness test the cells do, so the grid cannot claim to
 * hold vitals and then render a row of em dashes.
 */
export function hasAnyVital(vitals: DoctorVitals): boolean {
  /*
    PAIN SCORE COUNTS, AND IT COUNTS AT ZERO. It is the one reading on this
    grid whose zero is a real answer -- no pain -- and the grid renders it
    behind this gate, so testing it for recorded-ness the way a pulse is
    tested would hide a legitimately reported score of 0.
  */
  if (vitals.pain_level !== null && vitals.pain_level !== undefined) return true;
  return (
    Object.entries(vitals).filter(
      ([key, value]) =>
        key !== "bmi_state" &&
        key !== "pain_level" &&
        isRecordedVital(value as number | null | undefined),
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

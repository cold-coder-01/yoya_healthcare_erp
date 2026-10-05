/**
 * Accountant Desk wording and tones. Pure: no fetch, no figure decided here.
 *
 * The refundable amount is always the server's (`refund.max_amount`); the
 * review preview only SHOWS "before - refund = after" for what the accountant
 * typed, and the server re-checks it under the admission's lock.
 */
import type {
  AccountantIdentity,
  AccountantLane,
  AccountantPaymentIn,
} from "@/types/accountant";

export type AccountantTab = "all" | AccountantLane;

export const ACCOUNTANT_TABS: { key: AccountantTab; label: string }[] = [
  { key: "all", label: "All" },
  { key: "refund_due", label: "Refund due" },
  { key: "needs_review", label: "Needs review" },
  { key: "refunded", label: "Completed / Refunded" },
];

export const ACCOUNTANT_EMPTY_STATE = "No inpatient refunds currently require accounting action.";

export const DEFAULT_REFUND_REASON =
  "Refund of unused inpatient advance after final inpatient settlement.";

export const REFUND_ACCOUNTING_PENDING = "Accounting journal posting pending";

const LANE_LABELS: Record<AccountantLane, string> = {
  refund_due: "Refund due",
  needs_review: "Needs review",
  refunded: "Refunded",
};

/** Refund due amber, review red, refunded green. */
const LANE_TONES: Record<AccountantLane, string> = {
  refund_due: "border-amber-300 bg-amber-50 text-amber-900",
  needs_review: "border-red-300 bg-red-50 text-red-800",
  refunded: "border-emerald-300 bg-emerald-50 text-emerald-800",
};

export function accountantLaneLabel(lane: AccountantLane | null | undefined): string {
  return lane ? LANE_LABELS[lane] ?? lane : "No accounting action";
}

export function accountantLaneTone(lane: AccountantLane | null | undefined): string {
  return lane ? LANE_TONES[lane] ?? "border-slate-300 bg-slate-50 text-slate-700" : "border-slate-300 bg-slate-50 text-slate-700";
}

/** The stay's workflow state as a fact. A refund never changes it. */
export function accountantAdmissionStatus(admission: AccountantIdentity["admission"]): string {
  if (admission.state === "discharged") return "Discharged";
  if (admission.medical_discharge_ready) return "Medically ready";
  if (admission.state === "admitted" || admission.state === "transferred") return "In care";
  return admission.state;
}

const PAYMENT_KIND_LABELS: Record<AccountantPaymentIn["kind"], string> = {
  advance: "Advance",
  settlement: "Settlement payment",
  payment: "Other payment",
};

export function paymentKindLabel(kind: AccountantPaymentIn["kind"]): string {
  return PAYMENT_KIND_LABELS[kind] ?? kind;
}

export function locationText(location: AccountantIdentity["location"]): string {
  const parts = [
    location.ward_code ?? location.ward,
    location.room,
    location.bed_code ?? location.bed,
  ].filter((part): part is string => Boolean(part));
  return parts.length ? parts.join(" / ") : "—";
}

/** What the review modal shows. `max` is the server's refundable balance. */
export function refundPreview(max: number, typed: string) {
  const amount = Number(typed);
  const finite = typed.trim() !== "" && Number.isFinite(amount);
  const exceeds = finite && amount - max > 0.005;
  const valid = finite && amount > 0 && !exceeds;
  const round = (value: number) => Math.round(value * 100) / 100;
  return {
    before: max,
    refund: valid ? round(amount) : 0,
    after: valid ? round(max - amount) : max,
    exceeds,
    valid,
  };
}

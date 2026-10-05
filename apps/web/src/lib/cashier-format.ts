import type {
  CashierCollectability,
  CashierInpatientIdentity,
  CashierInpatientLane,
  CashierInpatientRow,
  CashierLane,
  CashierServiceCategory,
  ResponsibilityMode,
} from "@/types/cashier";

/**
 * Presentation helpers for the Cashier Desk.
 *
 * FORMATTING ONLY. Nothing here decides whether money may be collected, how
 * much is owed, or which lane a visit belongs in -- those are server verdicts
 * and arrive in the payload. A helper that computed one would be a second
 * source of truth in the one place a hospital can least afford it.
 */

const LANE_LABELS: Record<CashierLane, string> = {
  collect: "Awaiting payment",
  partial: "Part paid",
  blocked: "Blocked",
  cleared: "Cleared",
};

const MODE_LABELS: Record<ResponsibilityMode, string> = {
  off: "Legacy billing",
  shadow: "Split advisory",
  enforce: "Split enforced",
};

const STATE_LABELS: Record<string, string> = {
  self_pay: "Patient only",
  proposed: "Sponsor proposed",
  authorized: "Sponsor authorized",
  mixed: "Mixed",
  not_required: "Not required",
  pending: "Pending",
  cleared: "Cleared",
  credit_authorized: "Credit authorized",
  sponsor_cleared: "Sponsor cleared",
  emergency_bypass: "Emergency bypass",
  inpatient_credit: "Covered by inpatient advance",
  awaiting_cashier: "Awaiting cashier",
  ready_doctor: "Ready for doctor",
  // Appointment workflow states, for the active-service lane. A visit sits in
  // that lane WITHOUT leaving in_consultation -- the label states the clinical
  // fact plainly so nobody reads the queue as a change of care state.
  in_consultation: "In consultation",
  confirmed: "Checked in",
  // Slice 4. A completed visit stays in the service-payment lane while money
  // is still owed on it, so the lane has to be able to say so. "Visit
  // finished" describes the CLINICAL state and nothing else -- the patient is
  // no longer with the doctor; the money is a separate fact the row's amount
  // already carries.
  done: "Visit finished",
};

export function laneLabel(lane: CashierLane) {
  return LANE_LABELS[lane] ?? lane;
}

export function modeLabel(mode: ResponsibilityMode) {
  return MODE_LABELS[mode] ?? mode;
}

export function cashierLabel(value: string | null | undefined, fallback = "-") {
  if (!value) return fallback;
  return (
    STATE_LABELS[value] ??
    value
      .split("_")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
}

/**
 * Money, in the hospital's own convention: grouped thousands, two decimals,
 * no currency symbol inline. The currency is stated once per panel instead of
 * repeated on every figure, which is what keeps a dense column scannable.
 */
export function money(value: number | null | undefined) {
  const amount = typeof value === "number" && Number.isFinite(value) ? value : 0;
  return amount.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function shortTime(value: string | null | undefined) {
  if (!value) return "-";
  const parsed = new Date(value.replace(" ", "T") + "Z");
  if (Number.isNaN(parsed.getTime())) return "-";
  return parsed.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Tailwind classes per lane. Colour encodes state, never decoration. */
export function laneTone(lane: CashierLane) {
  switch (lane) {
    case "collect":
      return "border-emerald-300 bg-emerald-50 text-emerald-800";
    case "partial":
      return "border-amber-300 bg-amber-50 text-amber-900";
    case "blocked":
      return "border-red-300 bg-red-50 text-red-800";
    case "cleared":
      return "border-slate-300 bg-slate-100 text-slate-600";
    default:
      return "border-slate-300 bg-slate-100 text-slate-600";
  }
}

/**
 * What to tell the cashier when they may not collect.
 *
 * Uses the server's own reason text, and only supplies a fallback if the
 * server sent none. It never invents an explanation -- particularly for
 * `sponsor_authorization_pending`, where naming the wrong role would send a
 * patient to the wrong desk.
 */
export function blockedMessage(collectability: CashierCollectability) {
  if (collectability.collectable) return null;
  if (collectability.reason) return collectability.reason;
  return "This visit is not collectable at the cashier.";
}

/**
 * The generic service categories holding a visit at the window, as one string.
 *
 * The LABELS COME FROM THE SERVER. This joins them and nothing else -- it does
 * not map a key to a word, because doing so would put a second, silently
 * drifting copy of hospital.billing.service.service_type in the browser, and a
 * category this build had never heard of would render blank instead of
 * rendering itself.
 *
 * Returns null when the server sent no category, so a caller renders nothing
 * rather than an empty badge.
 */
export function serviceCategorySummary(
  categories: CashierServiceCategory[] | null | undefined,
) {
  if (!categories?.length) return null;
  const labels = categories
    .map((category) => category.label?.trim())
    .filter((label): label is string => Boolean(label));
  return labels.length ? labels.join(" · ") : null;
}

// ----------------------------------------------------------------------
// INPATIENT SETTLEMENT
// ----------------------------------------------------------------------

const INPATIENT_LANE_LABELS: Record<CashierInpatientLane, string> = {
  due: "Payment required",
  part_paid: "Part paid",
  advance_required: "Advance required",
  refund_due: "Refund due",
  needs_review: "Needs review",
  settled: "Settled",
};

/** With the stay's advance: once some advance is held, a revised-up estimate
 *  asks for the DIFFERENCE -- "Additional advance required". */
export function inpatientLaneLabel(
  lane: CashierInpatientLane,
  advance?: { received: number } | null,
) {
  if (lane === "advance_required" && (advance?.received ?? 0) > 0) {
    return "Additional advance required";
  }
  return INPATIENT_LANE_LABELS[lane] ?? cashierLabel(lane);
}

export function inpatientLaneTone(lane: CashierInpatientLane) {
  switch (lane) {
    case "due":
      return "border-emerald-300 bg-emerald-50 text-emerald-800";
    case "part_paid":
      return "border-amber-300 bg-amber-50 text-amber-900";
    case "refund_due":
      return "border-sky-300 bg-sky-50 text-sky-800";
    case "advance_required":
      return "border-violet-300 bg-violet-50 text-violet-800";
    case "needs_review":
      return "border-red-300 bg-red-50 text-red-800";
    default:
      return "border-slate-300 bg-slate-100 text-slate-600";
  }
}

/** The one server figure a queue row leads with, by lane: the refundable
 *  credit, the uncovered estimate, or the balance due. Chosen, never computed. */
export function inpatientRowFigure(row: CashierInpatientRow) {
  if (row.lane === "refund_due") return row.refundable_balance;
  if (row.lane === "advance_required") return row.advance?.outstanding ?? 0;
  return row.remaining_due;
}

/** The stay's workflow state, as a fact. Never a clinical finding. */
export function inpatientStateLabel(admission: CashierInpatientIdentity["admission"]) {
  if (admission.state === "discharged") return "Discharged";
  if (admission.medical_discharge_ready) return "Medically ready";
  return "Inpatient";
}

/** Ward · room · bed, whichever parts exist. */
export function inpatientLocation(location: CashierInpatientIdentity["location"]) {
  const parts = [
    location.ward_code ?? location.ward,
    location.room,
    location.bed_code ?? location.bed,
  ].filter((part): part is string => Boolean(part && part.trim()));
  return parts.length ? parts.join(" · ") : "-";
}

/**
 * The payment dialog's AFTER-ENTRY PREVIEW, and nothing else.
 *
 * The one place this desk subtracts: "outstanding before", "payment",
 * "outstanding after", shown to the cashier before they commit. It decides
 * nothing -- the server re-derives the balance, refuses any amount above it,
 * and the figures shown after success come from its response, not from here.
 * Rounded to cents so 0.1 + 0.2 never reaches the screen.
 */
export function settlementPreview(outstanding: number, amount: number) {
  const before = Math.round(outstanding * 100) / 100;
  const payment = Number.isFinite(amount) ? Math.round(amount * 100) / 100 : 0;
  return {
    before,
    payment,
    after: Math.round((before - payment) * 100) / 100,
    exceeds: payment - before > 0.005,
  };
}

/** A stable idempotency key per payment attempt. */
export function newIdempotencyKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 12)}`;
}

import type {
  CashierCollectability,
  CashierLane,
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
  awaiting_cashier: "Awaiting cashier",
  ready_doctor: "Ready for doctor",
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

/** A stable idempotency key per payment attempt. */
export function newIdempotencyKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().replace(/-/g, "");
  }
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 12)}`;
}

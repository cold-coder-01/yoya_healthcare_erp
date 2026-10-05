import type {
  InpatientSettlement,
  SettlementStage,
  SettlementState,
} from "@/types/inpatient-settlement";

/**
 * Presentation for the FINAL INPATIENT SETTLEMENT window.
 *
 * FORMATTING ONLY. The state, every amount and every stage status come from
 * the server's one settlement computation. These helpers choose words and
 * colours for what the server decided; none of them computes a balance.
 */

/** The stages the window lists while the server is still computing, in the
 *  order the server reports them. Labels are replaced by the server's own
 *  once the result arrives. */
export const SETTLEMENT_STAGE_PLAN: { key: string; label: string }[] = [
  { key: "admission", label: "Admission" },
  { key: "stay", label: "Stay / bed" },
  { key: "procedures", label: "Procedures" },
  { key: "pharmacy", label: "Pharmacy" },
  { key: "laboratory", label: "Laboratory" },
  { key: "radiology", label: "Radiology" },
  { key: "other", label: "Consultation & other services" },
  { key: "payer", label: "Payer responsibility" },
  { key: "payments", label: "Patient advances & payments" },
  { key: "reconciliation", label: "Final reconciliation" },
];

const HEADLINES: Record<SettlementState, { label: string; tone: string }> = {
  due: { label: "Payment required", tone: "border-amber-300 bg-amber-50 text-amber-900" },
  refund_due: { label: "Refund due", tone: "border-sky-300 bg-sky-50 text-sky-900" },
  credit: { label: "Advance / patient credit", tone: "border-emerald-300 bg-emerald-50 text-emerald-900" },
  even: { label: "Even", tone: "border-emerald-300 bg-emerald-50 text-emerald-900" },
  needs_review: { label: "Needs review", tone: "border-red-300 bg-red-50 text-red-900" },
  not_applicable: { label: "No stay to settle", tone: "border-slate-300 bg-slate-50 text-slate-700" },
};

/** The one line the window leads with, and the server figure it carries. */
export function settlementHeadline(settlement: InpatientSettlement) {
  const base = HEADLINES[settlement.state] ?? HEADLINES.needs_review;
  const amount =
    settlement.state === "due"
      ? settlement.remaining_due
      : settlement.state === "refund_due"
        ? settlement.refundable_balance
        : settlement.state === "credit"
          ? settlement.unapplied_credit
        : settlement.state === "even"
          ? 0
          : null;
  return { label: base.label, tone: base.tone, amount };
}

/**
 * How far the calculation got, AS THE SERVER REPORTED IT: the share of stages
 * the server marked complete. While the request is in flight there is no
 * report yet, so the bar is indeterminate (null) -- never a timer.
 */
export function stageProgress(stages: SettlementStage[] | null | undefined) {
  if (!stages) return { complete: 0, total: SETTLEMENT_STAGE_PLAN.length, percent: null };
  const total = stages.length;
  const complete = stages.filter((stage) => stage.status === "complete").length;
  return {
    complete,
    total,
    percent: total ? Math.round((complete / total) * 100) : 100,
  };
}

export function stageStatusLabel(status: SettlementStage["status"] | "pending") {
  if (status === "complete") return "Complete";
  if (status === "review") return "Needs review";
  return "Calculating…";
}

/**
 * Inpatient advance + final settlement (Advance slice): helpers and source
 * contracts. Run with `npm test` (node:test; no DOM renderer, so components
 * are held at the SOURCE, as the other contract tests do).
 *
 * The properties defended:
 *   * the settlement window leads with the server's state and figure
 *   * the progress is the SERVER's report -- never a timer
 *   * every mutation route rebuilds its body from exactly its fields
 *   * refunds are routed to Accounting; the Cashier never gets a refund button
 *   * the Admissions window is reachable only through the discharge role
 *   * the doctor's estimate section shows no payment, balance or settlement
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { InpatientSettlement, SettlementStage } from "@/types/inpatient-settlement";

import {
  SETTLEMENT_STAGE_PLAN,
  settlementHeadline,
  stageProgress,
  stageStatusLabel,
} from "./settlement-format.ts";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

function settlement(overrides: Partial<InpatientSettlement>): InpatientSettlement {
  return {
    state: "even",
    financial_state: "covered",
    quote: "q",
    estimate_amount: 50000,
    advance_received: 50000,
    delivered_by_category: [],
    actual_delivered: 50000,
    payer_authorized: 0,
    patient_responsibility: 50000,
    funds: { advance: 50000, other_payments: 0, settlement_payments: 0, total: 50000 },
    advance_applied: 50000,
    unapplied_credit: 0,
    remaining_due: 0,
    refundable_balance: 0,
    settlement_difference: 0,
    stay_unposted: 0,
    pending_delivery: false,
    stages: [],
    review_reasons: [],
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
test("the headline is the server's state and figure", () => {
  assert.deepEqual(
    [settlementHeadline(settlement({ state: "due", remaining_due: 7000 })).label,
     settlementHeadline(settlement({ state: "due", remaining_due: 7000 })).amount],
    ["Payment required", 7000],
  );
  assert.equal(settlementHeadline(settlement({ state: "refund_due", refundable_balance: 2000 })).amount, 2000);
  assert.equal(settlementHeadline(settlement({ state: "refund_due" })).label, "Refund due");
  const even = settlementHeadline(settlement({ state: "even" }));
  assert.deepEqual([even.label, even.amount], ["Even", 0]);
  assert.equal(settlementHeadline(settlement({ state: "needs_review" })).amount, null);
});

test("progress is what the server reported, and indeterminate before it answers", () => {
  assert.equal(stageProgress(null).percent, null);
  const stage = (status: SettlementStage["status"]): SettlementStage => ({ key: "k", label: "L", status, amount: 0 });
  const all = Array.from({ length: 10 }, () => stage("complete"));
  assert.deepEqual(stageProgress(all), { complete: 10, total: 10, percent: 100 });
  const oneReview = [...all.slice(0, 9), stage("review")];
  assert.equal(stageProgress(oneReview).percent, 90);
  assert.equal(stageStatusLabel("pending"), "Calculating…");
  assert.equal(stageStatusLabel("review"), "Needs review");
  assert.deepEqual(
    SETTLEMENT_STAGE_PLAN.map((entry) => entry.key),
    ["admission", "stay", "procedures", "pharmacy", "laboratory", "radiology", "other",
     "payer", "payments", "reconciliation"],
  );
});

test("the settlement view has no timer: stages complete only from the server", () => {
  const view = code(read("components/inpatient/settlement-view.tsx"));
  assert.doesNotMatch(view, /setTimeout|setInterval|requestAnimationFrame/);
  assert.match(view, /stageProgress\(loading \? null : settlement\?\.stages\)/);
  assert.match(view, /const status = stage\.status \?\? "pending";/);
  for (const label of [
    "Estimated amount", "Advance received", "Total delivered care", "Payer share",
    "Patient responsibility", "Total patient funds", "Remaining due", "Refundable balance",
  ]) {
    assert.ok(view.includes(label), label);
  }
});

// ---------------------------------------------------------------------------
// BFF
// ---------------------------------------------------------------------------
function postedKeys(source: string): string[] {
  const start = source.indexOf('"POST",');
  // The rebuilt object literal: from the POST verb to its closing brace.
  const block = source.slice(start, source.indexOf("\n        },", start));
  return [...block.matchAll(/^\s*(\w+):/gm)].map((match) => match[1]).sort();
}

test("the settlement window route is read-only and names no host", () => {
  const route = code(read("app/api/admissions/[id]/settlement/route.ts"));
  assert.match(route, /export async function GET/);
  assert.doesNotMatch(route, /export async function (POST|PUT|PATCH|DELETE)/);
  assert.match(route, /\$\{ADMISSIONS_API\}\/\$\{parsed\.value\}\/settlement/);
  assert.doesNotMatch(route, /localhost|:8069|:8171/);
});

test("the advance, refund and payment routes rebuild their bodies exactly", () => {
  assert.deepEqual(
    postedKeys(code(read("app/api/cashier/admissions/[admissionId]/advance/route.ts"))),
    ["amount", "idempotency_key", "note", "payment_method", "payment_reference"],
  );
  assert.deepEqual(
    postedKeys(code(read("app/api/cashier/admissions/[admissionId]/refund/route.ts"))),
    ["amount", "idempotency_key", "reason"],
  );
  assert.deepEqual(
    postedKeys(code(read("app/api/cashier/admissions/[admissionId]/payments/route.ts"))),
    ["amount", "idempotency_key", "note", "payment_method", "payment_reference", "quote"],
  );
});

test("the estimate route forwards exactly four fields", () => {
  const body = code(read("app/api/admissions/_body.ts"));
  const picker = body.slice(body.indexOf("export function pickEstimateBody"));
  const keys = [...picker.slice(0, picker.indexOf("};")).matchAll(/^\s*(\w+):/gm)].map((m) => m[1]).sort();
  assert.deepEqual(keys, ["amount", "expected_revision", "operation_token", "reason"]);
  const route = code(read("app/api/doctor/visits/[appointmentId]/admission-estimate/route.ts"));
  assert.match(route, /pickEstimateBody\(body\.body\)/);
});

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------
test("refunds are routed to Accounting; the form exists only for may_record", () => {
  const refund = code(read("components/cashier/cashier-inpatient-refund.tsx"));
  assert.match(refund, /if \(!refund\.refund_due\) return null;/);
  assert.match(refund, /if \(!refund\.may_record\) \{/);
  assert.ok(refund.includes("Routed to Accounting"));
  const guardAt = refund.indexOf("if (!refund.may_record)");
  assert.ok(refund.indexOf("<form") > guardAt, "the form is only past the may_record guard");
});

test("the Admissions window is the discharge role's, and re-runs the server calculation", () => {
  const panel = code(read("components/admissions/admission-detail-panel.tsx"));
  assert.match(panel, /mayDischarge && \(detail\.state === "admitted" \|\| detail\.state === "transferred"\)/);
  assert.match(panel, /<FinalSettlementDialog/);
  const dialog = code(read("components/admissions/final-settlement-dialog.tsx"));
  assert.match(dialog, /fetch\(`\/api\/admissions\/\$\{admissionId\}\/settlement`/);
  assert.match(dialog, /data\?\.discharge_allowed && onContinueToFinalize/);
  assert.doesNotMatch(dialog, /method: "POST"/);
});

test("the doctor's estimate section shows no payment, balance or settlement", () => {
  const estimate = code(read("components/doctor/doctor-inpatient-estimate.tsx"));
  assert.match(estimate, /\/api\/doctor\/visits\/\$\{appointmentId\}\/admission-estimate/);
  assert.match(estimate, /data\.can_edit/);
  for (const forbidden of [
    "remaining_due", "refundable", "advance_received", ".settlement", "settlement_", "/api/cashier",
  ]) {
    assert.ok(!estimate.includes(forbidden), forbidden);
  }
  const card = code(read("components/doctor/doctor-admission-card.tsx"));
  assert.match(card, /<DoctorInpatientEstimate/);
  // PRE-ADMISSION: shown in the consultation strip (compact) too -- the defect
  // was a !compact guard hiding it exactly where admission is requested.
  assert.doesNotMatch(card, /!compact &&/);
  assert.match(card, /summary\.status === "requested"/);
  assert.match(card, /compact=\{compact\}/);
});

// ---------------------------------------------------------------------------
// The care stage (UAT defect, ENC13994): credit while in care, never "refund"
// ---------------------------------------------------------------------------
test("in care, an excess reads as advance / patient credit on every desk", async () => {
  const credit = settlementHeadline(settlement({ state: "credit", unapplied_credit: 300 }));
  assert.deepEqual([credit.label, credit.amount], ["Advance / patient credit", 300]);

  const { financialLabel } = await import("./admissions-desk-format.ts");
  const { financialState } = await import("./admission-preview.ts");
  const base = { billing_blocked: false, settlement_required: false, refund_due: false, review_reasons: [] };
  const detail = financialLabel({ ...base, financial_state: "credit", patient_credit: true });
  assert.match(detail.text, /^Advance \/ patient credit: patient funds are being held toward ongoing inpatient care\./);
  assert.doesNotMatch(detail.text, /refund|return/i);
  assert.equal(financialState({ ...base, financial_state: "credit", patient_credit: true }).label, "Advance / patient credit");
  // Refund wording is reserved for the server's "refundable" (care over).
  assert.match(financialLabel({ ...base, financial_state: "refundable", refund_due: true }).text, /^Refund due: care is complete/);
});

// ---------------------------------------------------------------------------
// Pre-admission financial clearance (Admissions)
// ---------------------------------------------------------------------------
test("a pending request shows WHY it cannot be admitted; figures only for the clerk", () => {
  const panel = code(read("components/admissions/admission-detail-panel.tsx"));
  assert.match(panel, /detail\.admission_clearance \? \(/);
  assert.match(panel, /mayReadAmounts=\{mayDischarge\}/);
  // Admit stays behind the server's can_admit, which now includes clearance.
  assert.ok(panel.includes("{mayAdmit && detail.can_admit && onRequestAdmit ? ("));
  const note = code(read("components/admissions/admission-clearance-note.tsx"));
  assert.match(note, /if \(!mayReadAmounts \|\| clearance\.state !== "awaiting_advance"\) return;/);
  assert.match(note, /\/api\/admissions\/\$\{admissionId\}\/settlement/);
  assert.match(note, /\{clearance\.message\}/);
  assert.doesNotMatch(note, /method: "POST"/);
});

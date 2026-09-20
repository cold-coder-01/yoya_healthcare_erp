/**
 * Pharmacy Desk mutation helpers (Slice 2). node:test, no DOM.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { PharmacyDispenseDetail, PharmacyDispenseLine } from "@/types/pharmacy-desk";

import {
  QUEUE_REFRESH_FAILED_NOTICE,
  draftFromDetail,
  draftKey,
  isResolved,
  lineIssueText,
  needsReload,
  parseQuantity,
  prepareBody,
  preparePath,
  preparePlan,
  requestSignature,
  tokenFor,
  validateBody,
  validatePath,
  validationSummary,
} from "./pharmacy-desk-actions.ts";

function line(overrides: Partial<PharmacyDispenseLine> = {}): PharmacyDispenseLine {
  return {
    id: 11,
    medicine: { id: 2, name: "Cetirizine", code: "CET", strength: "10mg", dosage_form: "tablet", dosage_form_label: "Tablet" },
    dosage: null, frequency: null, duration: null, route: null, instruction: null,
    prescribed_quantity: 10,
    intended_quantity: 4,
    delivered_quantity: 4,
    consumed_quantity: 4,
    remaining_quantity: 6,
    pending_increment: 0,
    minimum_intended_quantity: 4,
    billing_mapped: true, charge_linked: true, inventory_mapped: true,
    stock_basis: "remaining", stock_sufficient: true,
    ...overrides,
  };
}

function detail(lines: PharmacyDispenseLine[], revision = 2): PharmacyDispenseDetail {
  return {
    id: 7, dispense_code: "DISP00007", state: "partial", state_label: "Partially Dispensed",
    lane: "partially_supplied", lane_label: "Partially supplied", reason: null, reason_message: null,
    priority: "routine", priority_label: "Routine", dispense_date: null,
    patient: { id: 1, name: "Test Patient", mrn: "MRN-7", age: 30, gender: "female" },
    prescription: null, prescriber: null, billing_blocked: false, stock_short: false,
    line_count: lines.length, lines_complete: 0, medicines_summary: null,
    workflow_revision: revision, can_prepare: true, can_validate: false,
    notes: null, ordered_from_consultation: true, unified_billing: true, pharmacy_store_configured: true,
    lines,
  };
}

test("paths are BFF paths", () => {
  assert.equal(preparePath(7), "/api/pharmacy/dispenses/7/prepare");
  assert.equal(validatePath(7), "/api/pharmacy/dispenses/7/validate");
});

test("quantities parse strictly at three decimals", () => {
  assert.equal(parseQuantity("10"), 10);
  assert.equal(parseQuantity(" 19.9964 "), 19.996);
  assert.equal(parseQuantity("-1"), null);
  assert.equal(parseQuantity("1e3"), null);
  assert.equal(parseQuantity("abc"), null);
  assert.equal(parseQuantity(""), null);
});

test("the draft is seeded from the server and keyed by revision", () => {
  const d = detail([line()]);
  assert.deepEqual(draftFromDetail(d), { 11: "4" });
  assert.equal(draftKey(d), "7:2");
});

test("the plan is CUMULATIVE: 10 after 4 supplied is an increment of 6", () => {
  const d = detail([line()]);
  const plan = preparePlan(d, { 11: "10" });
  assert.equal(plan.valid, true);
  assert.equal(plan.hasIncrement, true);
  assert.equal(plan.lines[0].increment, 6);
  assert.equal(plan.lines[0].supplied, 4);
});

test("the plan explains the obvious bounds", () => {
  const d = detail([line()]);
  assert.equal(preparePlan(d, { 11: "3" }).lines[0].issue, "below_supplied");
  assert.equal(preparePlan(d, { 11: "11" }).lines[0].issue, "above_prescribed");
  assert.equal(preparePlan(d, { 11: "x" }).lines[0].issue, "invalid");
  assert.equal(preparePlan(d, { 11: "4" }).hasIncrement, false, "no positive increment");
  assert.equal(lineIssueText("below_supplied"), "Below already supplied");
  assert.equal(lineIssueText(null), null);
});

test("request bodies carry exactly the allowed fields", () => {
  const d = detail([line(), line({ id: 12 })]);
  const plan = preparePlan(d, { 11: "10", 12: "4" });
  assert.deepEqual(prepareBody(d, plan, "tok"), {
    operation_token: "tok",
    expected_revision: 2,
    lines: [{ line_id: 11, intended_quantity: 10 }, { line_id: 12, intended_quantity: 4 }],
  });
  assert.deepEqual(validateBody(d, "tok"), { operation_token: "tok", expected_revision: 2 });
  assert.equal("lines" in validateBody(d, "tok"), false, "validate sends no quantity");
});

test("the validation summary shows what is handed over NOW", () => {
  const d = detail([line({ intended_quantity: 10, minimum_intended_quantity: 4 })]);
  const [summary] = validationSummary(d);
  assert.deepEqual([summary.intended, summary.delivered, summary.increment], [10, 4, 6]);
});

test("a retry of the SAME unresolved request reuses its token; anything else mints", () => {
  let minted = 0;
  const mint = () => `new-${++minted}`;
  const sig = requestSignature({ expected_revision: 2, lines: [{ line_id: 11, intended_quantity: 10 }] });
  const pending = { kind: "prepare" as const, dispenseId: 7, signature: sig, token: "kept" };
  assert.equal(tokenFor(pending, "prepare", 7, sig, mint), "kept");
  assert.equal(tokenFor(pending, "validate", 7, sig, mint), "new-1");
  assert.equal(tokenFor(pending, "prepare", 8, sig, mint), "new-2");
  const changed = requestSignature({ expected_revision: 2, lines: [{ line_id: 11, intended_quantity: 9 }] });
  assert.equal(tokenFor(pending, "prepare", 7, changed, mint), "new-3");
  assert.equal(tokenFor(null, "prepare", 7, sig, mint), "new-4");
});

test("only an answered request is resolved", () => {
  assert.equal(isResolved(200, true), true);
  assert.equal(isResolved(409, true), true);
  assert.equal(isResolved(null, false), false, "network failure: outcome unknown");
  assert.equal(isResolved(502, false), false, "non-JSON: outcome unknown");
});

test("stale-data refusals ask for a reload", () => {
  assert.equal(needsReload("pharmacy_dispense_revision_conflict"), true);
  assert.equal(needsReload("pharmacy_dispense_state_conflict"), true);
  assert.equal(needsReload("pharmacy_billing_blocked"), false);
  assert.equal(
    QUEUE_REFRESH_FAILED_NOTICE,
    "Dispense updated. Queue refresh failed; the displayed result is authoritative.",
  );
});

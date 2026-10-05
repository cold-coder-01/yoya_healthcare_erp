/**
 * Cashier inpatient settlement: presentation helpers and source contracts.
 *
 * Run with `npm test` (node:test). The workspace has no DOM renderer, so the
 * components are held at the SOURCE -- the technique the admissions and
 * pharmacy contract tests use -- and the helpers are exercised directly.
 *
 * The properties defended:
 *   * the inpatient row says who (name, MRN), which stay (visit, admission),
 *     where (ward/room/bed), and how much -- in the server's words;
 *   * the payment is REVIEWED before it is recorded, with before/payment/after;
 *   * the BFF rebuilds the body from exactly five fields, so the browser cannot
 *     steer the balance;
 *   * no clinical field is read anywhere in the inpatient components;
 *   * the outpatient lanes and the desk chrome are unchanged.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import type { CashierInpatientRow } from "@/types/cashier";

import {
  inpatientLaneLabel,
  inpatientLaneTone,
  inpatientLocation,
  inpatientStateLabel,
  money,
  settlementPreview,
} from "./cashier-format.ts";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const HALIMA: CashierInpatientRow = {
  admission: {
    id: 3,
    name: "ADM00001",
    state: "transferred",
    medical_discharge_ready: true,
    admission_date: "2026-09-23 12:00:00",
    discharge_date: null,
  },
  patient: { id: 11835, name: "Halima Biru", identification_code: "HMS11835" },
  encounter: { id: 13993, name: "ENC13993", state: "active" },
  location: {
    ward: "Surgical Ward",
    ward_code: "SURG-WARD",
    room: "SR201",
    bed: "Bed 201A",
    bed_code: "BED-201A",
  },
  lane: "due",
  source: "inpatient_settlement",
  financial_state: "due",
  settlement_state: "due",
  advance: { requested: 0, received: 0, outstanding: 0, open: false },
  currency: "ETB",
  remaining_due: 1950,
  refundable_balance: 0,
  settlement_paid: 0,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
test("a Halima-like row reads as an inpatient needing payment", () => {
  assert.equal(inpatientLaneLabel(HALIMA.lane), "Payment required");
  assert.equal(inpatientStateLabel(HALIMA.admission), "Medically ready");
  assert.equal(inpatientLocation(HALIMA.location), "SURG-WARD · SR201 · BED-201A");
  assert.equal(money(HALIMA.remaining_due), "1,950.00");
});

test("every inpatient lane has its own label and tone", () => {
  assert.equal(inpatientLaneLabel("part_paid"), "Part paid");
  assert.equal(inpatientLaneLabel("refund_due"), "Refund due");
  assert.equal(inpatientLaneLabel("needs_review"), "Needs review");
  assert.equal(inpatientLaneLabel("settled"), "Settled");
  // A revised-up estimate on a stay that already holds advance asks for the
  // difference only.
  assert.equal(inpatientLaneLabel("advance_required"), "Advance required");
  assert.equal(inpatientLaneLabel("advance_required", { received: 0 }), "Advance required");
  assert.equal(inpatientLaneLabel("advance_required", { received: 50000 }), "Additional advance required");
  assert.equal(inpatientLaneLabel("settled", { received: 70000 }), "Settled");
  const tones = new Set(
    (["due", "part_paid", "refund_due", "needs_review", "settled"] as const).map(inpatientLaneTone),
  );
  assert.equal(tones.size, 5);
});

test("state labels are workflow facts only", () => {
  assert.equal(
    inpatientStateLabel({ ...HALIMA.admission, medical_discharge_ready: false }),
    "Inpatient",
  );
  assert.equal(
    inpatientStateLabel({ ...HALIMA.admission, state: "discharged" }),
    "Discharged",
  );
});

test("location falls back to names and to a dash", () => {
  assert.equal(
    inpatientLocation({ ward: "Medical Ward", ward_code: null, room: null, bed: "B1", bed_code: null }),
    "Medical Ward · B1",
  );
  assert.equal(
    inpatientLocation({ ward: null, ward_code: null, room: null, bed: null, bed_code: null }),
    "-",
  );
});

test("the payment preview: full, partial, then the remainder", () => {
  assert.deepEqual(settlementPreview(1950, 1950), { before: 1950, payment: 1950, after: 0, exceeds: false });
  assert.deepEqual(settlementPreview(1950, 1000), { before: 1950, payment: 1000, after: 950, exceeds: false });
  assert.deepEqual(settlementPreview(950, 950), { before: 950, payment: 950, after: 0, exceeds: false });
  assert.equal(settlementPreview(1950, 1950.01).exceeds, true);
  assert.equal(settlementPreview(0.3, 0.1).after, 0.2);
  assert.equal(settlementPreview(100, Number.NaN).payment, 0);
});

// ---------------------------------------------------------------------------
// BFF
// ---------------------------------------------------------------------------
const DETAIL_ROUTE = "app/api/cashier/admissions/[admissionId]/route.ts";
const PAY_ROUTE = "app/api/cashier/admissions/[admissionId]/payments/route.ts";

test("the inpatient BFF routes exist and use the Odoo session", () => {
  for (const route of [DETAIL_ROUTE, PAY_ROUTE]) {
    assert.ok(existsSync(new URL(`../${route}`, import.meta.url)), route);
    const source = code(read(route));
    assert.match(source, /requireOdooSession\(\)/);
    assert.match(source, /parseAdmissionId\(raw\)/);
    assert.doesNotMatch(source, /localhost|:8069|:8171|http:\/\//);
  }
  assert.match(code(read(DETAIL_ROUTE)), /\/yoya-emr\/api\/v1\/cashier\/admissions\/\$\{parsed\.value\}`/);
});

test("the payment body is rebuilt from exactly five fields", () => {
  const source = code(read(PAY_ROUTE));
  assert.match(source, /\/yoya-emr\/api\/v1\/cashier\/admissions\/\$\{parsed\.value\}\/payment`/);
  const block = source.slice(source.indexOf('"POST",'), source.indexOf("),", source.indexOf('"POST",')));
  const keys = [...block.matchAll(/^\s*(\w+):/gm)].map((match) => match[1]).sort();
  assert.deepEqual(keys, ["amount", "idempotency_key", "note", "payment_method", "payment_reference"]);
  for (const forbidden of [
    "remaining_due", "patient_responsibility", "financial_state", "billing_account_id",
    "encounter_id", "admission_id", "...body",
  ]) {
    assert.ok(!block.includes(forbidden), forbidden);
  }
  assert.match(source, /idempotency_key_required/);
});

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------
const PANEL = code(read("components/cashier/cashier-inpatient-panel.tsx"));
const PAYMENT = code(read("components/cashier/cashier-inpatient-payment.tsx"));
const QUEUE = code(read("components/cashier/cashier-queue.tsx"));
const WORKSTATION = code(read("components/cashier/cashier-workstation.tsx"));

test("the queue renders an inpatient section beside the unchanged outpatient ones", () => {
  assert.match(QUEUE, /title="Inpatient settlement"/);
  assert.match(QUEUE, /title="Initial clearance"/);
  assert.match(QUEUE, /title="Service payments"/);
  assert.match(QUEUE, /row\.patient\.identification_code/);
  assert.match(QUEUE, /row\.encounter\.name/);
  assert.match(QUEUE, /row\.admission\.name/);
  assert.match(QUEUE, /inpatientLaneLabel\(row\.lane, row\.advance\)/);
  // The refund lane shows the refundable figure, every other lane the due.
  assert.match(QUEUE, /money\(inpatientRowFigure\(row\)\)/);
});

test("the panel shows the server's final settlement, the advance and every receipt", () => {
  // The settlement (stages, result, breakdown) is the shared SettlementView,
  // fed the server's block as-is.
  assert.match(PANEL, /<SettlementView\s+settlement=\{account\.settlement\}/);
  for (const label of [
    "Inpatient settlement", "Advance", "Estimate (advance requested)", "Advance received",
    "Advance payments", "Settlement payments",
  ]) {
    assert.ok(PANEL.includes(label), label);
  }
  assert.match(PANEL, /account\.advance_receipts/);
  assert.match(PANEL, /account\.settlement_receipts/);
});

test("payment and advance are both reviewed before they are recorded", () => {
  assert.match(PAYMENT, /role="dialog"/);
  for (const label of [
    "{text.outstanding} before", "{text.outstanding} after", "Payment", "Patient", "Encounter",
    "Admission", "Record payment", "Record advance", "Review inpatient payment",
    "Review inpatient advance", "Uncovered estimate",
  ]) {
    assert.ok(PAYMENT.includes(label), label);
  }
  // Settlement collects against the collect verdict; advance against its own.
  assert.match(PAYMENT, /mode === "advance" \? account\.advance_collectability : account\.collectability/);
  // The final click is the dialog's; the form's submit only opens the review.
  assert.match(PAYMENT, /onSubmit=\{handleReview\}/);
  assert.match(PAYMENT, /onClick=\{handleConfirm\}/);
  assert.match(PAYMENT, /settlementPreview\(collectability\.max_amount, parsedAmount\)/);
  assert.match(PAYMENT, /!preview\.exceeds/);
});

test("no clinical field is read by the inpatient components", () => {
  for (const source of [PANEL, PAYMENT, QUEUE]) {
    for (const field of ["physician", "diagnosis", "discharge_summary", "admission_reason", "notes", "evaluation"]) {
      assert.ok(!source.includes(field), field);
    }
  }
});

test("after payment the account and queue are re-read, never resubmitted", () => {
  assert.match(WORKSTATION, /const path = kind === "advance" \? "advance" : "payments";/);
  assert.match(WORKSTATION, /\/api\/cashier\/admissions\/\$\{selectedAdmissionId\}\/\$\{path\}/);
  assert.match(WORKSTATION, /\/api\/cashier\/admissions\/\$\{admissionId\}`/);
  assert.match(WORKSTATION, /setInpatient\(payload\.data\)/);
  // A settlement quotes the figures on screen; an advance does not.
  assert.match(WORKSTATION, /kind === "settlement" \? \{ quote: inpatient\.settlement\.quote \} : \{\}/);
  const handler = WORKSTATION.slice(WORKSTATION.indexOf("const handleInpatientMoney"));
  assert.ok((handler.match(/setRefreshToken/g) ?? []).length >= 3);
  assert.match(handler, /setInpatientToken/);
  assert.match(handler, /idempotency_key: newIdempotencyKey\(\)/);
});

test("the desk chrome and the outpatient payment path are untouched", () => {
  assert.match(WORKSTATION, /<CashierPaymentForm/);
  assert.match(WORKSTATION, /\/api\/cashier\/visits\/\$\{selectedId\}\/payments/);
  assert.match(read("app/cashier/layout.tsx"), /<FrontDeskUserMenu /);
});

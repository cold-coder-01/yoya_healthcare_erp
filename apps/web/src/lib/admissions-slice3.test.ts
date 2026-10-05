/**
 * ADMISSIONS SLICE 3: Transfer patient and Cancel request (Admissions Desk),
 * Cancel own request (Doctor Desk), and the amount-free financial state.
 *
 *   * Pure helpers exercised directly: paths, bodies, retry signatures.
 *   * Body pickers forward EXACTLY the allowed fields.
 *   * The financial label names a STATE and never a figure.
 *   * The doctor card is held at the source: cancel only on the server's
 *     `can_cancel_request`, pointer-only, kept-on-unknown token, and the
 *     server's returned summary shown as-is.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { pickCancelRequestBody, pickTransferBody } from "../app/api/admissions/_body.ts";
import {
  cancelRequestBody,
  cancelRequestPath,
  cancelRequestSignature,
  tokenFor,
  transferBody,
  transferPath,
  transferSignature,
} from "./admissions-desk-actions.ts";
import { financialLabel } from "./admissions-desk-format.ts";

function code(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const CARD = code("components/doctor/doctor-admission-card.tsx");
const TYPES = code("types/admissions-desk.ts");

test("transfer and cancel paths are BFF paths, shared by both desks", () => {
  assert.equal(transferPath(12), "/api/admissions/12/transfer");
  assert.equal(cancelRequestPath(12), "/api/admissions/12/cancel-request");
});

test("bodies carry exactly the fields the server accepts", () => {
  assert.deepEqual(transferBody(3, 9, "Isolation", "t"), {
    operation_token: "t", expected_revision: 3, bed_id: 9, reason: "Isolation",
  });
  assert.deepEqual(cancelRequestBody(1, "t"), { operation_token: "t", expected_revision: 1 });
});

test("the BFF pickers drop every other field", () => {
  const noisy = {
    operation_token: "t", expected_revision: 2, bed_id: 5, reason: "r",
    ward_id: 1, room_id: 2, daily_rate: 999, state: "discharged",
  };
  assert.deepEqual(pickTransferBody(noisy), { operation_token: "t", expected_revision: 2, bed_id: 5, reason: "r" });
  assert.deepEqual(pickCancelRequestBody(noisy), { operation_token: "t", expected_revision: 2 });
});

test("a transfer retry reuses its token only for the SAME revision, bed and reason", () => {
  const signature = transferSignature(3, 9, " Isolation ");
  const pending = { kind: "transfer" as const, targetId: 4, signature, token: "kept" };
  const mint = () => "fresh";
  assert.equal(tokenFor(pending, "transfer", 4, transferSignature(3, 9, "Isolation"), mint), "kept");
  assert.equal(tokenFor(pending, "transfer", 4, transferSignature(3, 10, "Isolation"), mint), "fresh");
  assert.equal(tokenFor(pending, "transfer", 4, transferSignature(4, 9, "Isolation"), mint), "fresh");
  assert.equal(tokenFor(pending, "cancel_request", 4, cancelRequestSignature(3), mint), "fresh");
});

test("the financial label names the state and never a number", () => {
  const base = { billing_blocked: false, settlement_required: false, refund_due: false, review_reasons: [] };
  for (const state of ["covered", "due", "refundable", "pending", "not_applicable", "needs_review"] as const) {
    const label = financialLabel({ ...base, financial_state: state });
    assert.doesNotMatch(label.text, /\d|amount|balance|ETB|Birr/i, state);
  }
  assert.equal(financialLabel({ ...base, financial_state: "due" }).tone, "warn");
  assert.equal(financialLabel({ ...base, financial_state: "covered" }).tone, "ok");
  assert.equal(financialLabel(null).tone, "neutral");
});

test("the wire type carries a financial STATE with booleans and no amount field", () => {
  const block = TYPES.slice(TYPES.indexOf("export type AdmissionFinancial"), TYPES.indexOf("export type AdmissionDetail ="));
  assert.ok(block.includes("financial_state: FinancialState;"));
  for (const flag of ["billing_blocked", "settlement_required", "refund_due"]) {
    assert.ok(block.includes(`${flag}: boolean;`), flag);
  }
  assert.doesNotMatch(block, /: number/);
});

test("the doctor may cancel only on the server's can_cancel_request, pointer-only", () => {
  assert.ok(CARD.includes('{summary.can_cancel_request && step === "idle" ? ('));
  assert.ok(CARD.includes("if (busy || !admission || !summary?.can_cancel_request) return;"));
  assert.ok(CARD.includes("cancelRequestBody(admission.workflow_revision, token)"));
  assert.ok(CARD.includes('tokenFor(pendingRef.current, "cancel_request", admission.id, signature, () => crypto.randomUUID())'));
  const cancelBlock = CARD.slice(CARD.indexOf('{step === "cancel" && admission ? ('));
  assert.ok(cancelBlock.includes("if (event.detail === 0) return;"));
  assert.ok(CARD.includes("setReturned({ basis: initial, value: payload.data.doctor_admission })"));
  // The doctor still never names a bed and never transfers.
  assert.doesNotMatch(CARD, /transferPath|bed_id|bedsPath/);
});

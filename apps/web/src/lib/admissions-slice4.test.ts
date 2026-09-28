/**
 * ADMISSIONS SLICE 4: Request discharge (Doctor Desk) and Finalize discharge
 * (Admissions Desk).
 *
 *   * Pure helpers exercised directly: paths, bodies, retry signatures, the
 *     reload rule for the discharge gates.
 *   * Body pickers forward EXACTLY the allowed fields -- a browser cannot send
 *     a discharge time, a bed or an amount.
 *   * The doctor card and both BFF routes are held at the source.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { pickDischargeRequestBody, pickFinalizeDischargeBody } from "../app/api/admissions/_body.ts";
import {
  dischargeRequestBody,
  dischargeRequestPath,
  dischargeRequestSignature,
  finalizeDischargeBody,
  finalizeDischargePath,
  finalizeDischargeSignature,
  needsReload,
  tokenFor,
} from "./admissions-desk-actions.ts";
import { financialLabel, laneLabel } from "./admissions-desk-format.ts";

function code(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const CARD = code("components/doctor/doctor-admission-card.tsx");
const DOCTOR_ROUTE = code("app/api/doctor/visits/[appointmentId]/discharge-request/route.ts");
const FINALIZE_ROUTE = code("app/api/admissions/[id]/finalize-discharge/route.ts");

test("discharge paths are BFF paths: the doctor by visit, the desk by admission", () => {
  assert.equal(dischargeRequestPath(7), "/api/doctor/visits/7/discharge-request");
  assert.equal(finalizeDischargePath(12), "/api/admissions/12/finalize-discharge");
});

test("bodies carry exactly the fields the server accepts", () => {
  assert.deepEqual(dischargeRequestBody(3, "Home today.", "t"), {
    operation_token: "t", expected_revision: 3, summary: "Home today.",
  });
  assert.deepEqual(finalizeDischargeBody(4, "t"), { operation_token: "t", expected_revision: 4 });
});

test("the BFF pickers drop a forged discharge time, bed, state or amount", () => {
  const noisy = {
    operation_token: "t", expected_revision: 4, summary: "s",
    discharge_date: "2020-01-01", bed_id: 1, state: "discharged", amount: 0, medical_discharge_ready: true,
  };
  assert.deepEqual(pickFinalizeDischargeBody(noisy), { operation_token: "t", expected_revision: 4 });
  assert.deepEqual(pickDischargeRequestBody(noisy), { operation_token: "t", expected_revision: 4, summary: "s" });
});

test("a retry reuses its token only for the SAME revision (and summary)", () => {
  const mint = () => "fresh";
  const pending = { kind: "final_discharge" as const, targetId: 9, signature: finalizeDischargeSignature(3), token: "kept" };
  assert.equal(tokenFor(pending, "final_discharge", 9, finalizeDischargeSignature(3), mint), "kept");
  assert.equal(tokenFor(pending, "final_discharge", 9, finalizeDischargeSignature(4), mint), "fresh");
  const doctor = { kind: "medical_discharge" as const, targetId: 9, signature: dischargeRequestSignature(2, " Home "), token: "kept" };
  assert.equal(tokenFor(doctor, "medical_discharge", 9, dischargeRequestSignature(2, "Home"), mint), "kept");
  assert.equal(tokenFor(doctor, "medical_discharge", 9, dischargeRequestSignature(2, "Other"), mint), "fresh");
});

test("a discharge-gate refusal reloads what went stale", () => {
  for (const codeName of [
    "admission_settlement_required",
    "admission_not_medically_ready",
    "admission_financial_review_required",
  ]) {
    assert.equal(needsReload(codeName), true, codeName);
  }
});

test("the desk names the operational states the slice asks for, never a figure", () => {
  const base = { billing_blocked: false, settlement_required: false, refund_due: false, review_reasons: [] };
  assert.match(financialLabel({ ...base, financial_state: "covered" }).text, /^Covered/);
  assert.match(financialLabel({ ...base, financial_state: "due" }).text, /^Payment required/);
  assert.match(financialLabel({ ...base, financial_state: "refundable" }).text, /^Refund due/);
  assert.match(financialLabel({ ...base, financial_state: "needs_review" }).text, /needs review/);
  assert.equal(laneLabel("discharge_pending"), "Ready for discharge");
});

test("the doctor requests discharge only on the server's can_request_discharge, pointer-only", () => {
  assert.ok(CARD.includes('{summary.can_request_discharge && step === "idle" ? ('));
  assert.ok(CARD.includes("if (busy || !admission || !cleanedDischarge || !summary?.can_request_discharge) return;"));
  assert.ok(CARD.includes("dischargeRequestBody(admission.workflow_revision, cleanedDischarge, token)"));
  assert.ok(CARD.includes('tokenFor(pendingRef.current, "medical_discharge", admission.id, signature, () => crypto.randomUUID())'));
  const confirm = CARD.slice(CARD.indexOf('{step === "discharge_confirm" && admission && cleanedDischarge ? ('));
  assert.ok(confirm.includes("if (event.detail === 0) return;"));
  for (const label of ["Patient", "Admission", "Location", "Summary", "Revision"]) {
    assert.ok(confirm.includes(`>${label}</dt>`), label);
  }
  assert.ok(confirm.includes("summary.discharge_warnings.map"));
  // The doctor never frees a bed, finalizes, or sees money.
  assert.doesNotMatch(CARD, /finalizeDischargePath|bedsPath|bed_id|amount|financial_state/);
});

test("both discharge routes validate, rebuild and forward, POST only", () => {
  for (const [source, picker, upstream] of [
    [DOCTOR_ROUTE, "pickDischargeRequestBody(body.body)", "/discharge-request`"],
    [FINALIZE_ROUTE, "pickFinalizeDischargeBody(body.body)", "/finalize-discharge`"],
  ] as const) {
    assert.match(source, /export async function POST\(/);
    assert.doesNotMatch(source, /export async function (GET|PUT|PATCH|DELETE)\(/);
    assert.ok(source.includes("await requireOdooSession()"));
    assert.ok(source.includes(picker));
    assert.ok(source.includes(upstream));
    assert.doesNotMatch(source, /https?:\/\/|localhost|:8069/);
  }
});

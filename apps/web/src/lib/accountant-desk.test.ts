/**
 * ACCOUNTANT DESK: inpatient refunds and settlement review on /accountant.
 *
 * Run with `npm test`. Routing and wording are exercised directly; the page,
 * the BFF routes, the workstation and the review modal are held at the source
 * (no DOM test stack exists).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  ACCOUNTANT_EMPTY_STATE,
  ACCOUNTANT_TABS,
  DEFAULT_REFUND_REASON,
  accountantAdmissionStatus,
  accountantLaneLabel,
  accountantLaneTone,
  paymentKindLabel,
  refundPreview,
} from "./accountant-format.ts";
import {
  ACCOUNTANT_ROUTE,
  CASHIER_ROUTE,
  canUseAccountantDesk,
  canUseCashier,
  landingRouteForRoles,
  type ReceptionRoles,
} from "./reception-roles.ts";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(relative: string): string {
  return read(relative)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const NONE: ReceptionRoles = {
  receptionist: false, cashier: false, accountant: false, manager: false,
  system_administrator: false, emergency_authorizer: false, front_desk_nurse: false,
  insurance_officer: false, doctor: false, lab_technician: false, radiology_technician: false,
  radiologist: false, pharmacist: false, nurse: false,
};
const roles = (overrides: Partial<ReceptionRoles>) => ({ ...NONE, ...overrides });

const DESK = code("components/accountant/accountant-workstation.tsx");
const DIALOG = code("components/accountant/accountant-refund-review-dialog.tsx");
const LAYOUT = code("app/accountant/layout.tsx");
const REFUND_ROUTE = code("app/api/accountant/admissions/[admissionId]/refund/route.ts");
const WORKLIST_ROUTE = code("app/api/accountant/worklist/route.ts");
const DETAIL_ROUTE = code("app/api/accountant/admissions/[admissionId]/route.ts");
const SESSION_ROUTE = code("app/api/accountant/session/route.ts");
const SIDEBAR = code("components/reception/reception-sidebar.tsx");

test("an accountant login lands on /accountant, never /cashier", () => {
  assert.equal(ACCOUNTANT_ROUTE, "/accountant");
  assert.equal(landingRouteForRoles(roles({ accountant: true })), ACCOUNTANT_ROUTE);
  assert.notEqual(landingRouteForRoles(roles({ accountant: true })), CASHIER_ROUTE);
  assert.equal(landingRouteForRoles(roles({ cashier: true })), CASHIER_ROUTE);
  assert.equal(canUseAccountantDesk(roles({ accountant: true })), true);
  assert.equal(canUseAccountantDesk(roles({ cashier: true })), false);
  assert.equal(canUseCashier(roles({ accountant: true })), false);
});

test("oversight is offered both desks; the sidebar links the Accountant Desk", () => {
  for (const oversight of [{ manager: true }, { system_administrator: true }]) {
    assert.equal(canUseAccountantDesk(roles(oversight)), true);
    assert.equal(canUseCashier(roles(oversight)), true);
  }
  assert.ok(SIDEBAR.includes('{ label: "Accountant Desk", href: ACCOUNTANT_ROUTE, visible: showAccountant }'));
});

test("lanes: All / Refund due / Needs review / Completed-Refunded; refund due amber, refunded green", () => {
  assert.deepEqual(ACCOUNTANT_TABS.map((tab) => tab.key), ["all", "refund_due", "needs_review", "refunded"]);
  assert.equal(accountantLaneLabel("refund_due"), "Refund due");
  assert.match(accountantLaneTone("refund_due"), /amber/);
  assert.match(accountantLaneTone("refunded"), /emerald/);
  assert.match(accountantLaneTone("needs_review"), /red/);
  assert.equal(
    ACCOUNTANT_EMPTY_STATE, "No inpatient refunds currently require accounting action.",
  );
  assert.ok(DESK.includes("{ACCOUNTANT_EMPTY_STATE}"));
  assert.doesNotMatch(DESK, /not authorized to record operational payment/);
});

test("a discharged stay reads Discharged, and a refund never changes it", () => {
  const discharged = {
    id: 4, name: "ADM00004", state: "discharged", medical_discharge_ready: true,
    admission_date: null, discharge_date: "2026-10-03 21:47:00", physician: "Dr. Hana Bekele",
  };
  assert.equal(accountantAdmissionStatus(discharged), "Discharged");
  assert.equal(accountantAdmissionStatus({ ...discharged, state: "admitted" }), "Medically ready");
  assert.equal(accountantAdmissionStatus({ ...discharged, state: "admitted", medical_discharge_ready: false }), "In care");
});

test("the refund preview shows the server's balance; it never decides it", () => {
  assert.deepEqual(refundPreview(43400, "43400"), { before: 43400, refund: 43400, after: 0, exceeds: false, valid: true });
  assert.deepEqual(refundPreview(43400, "10000"), { before: 43400, refund: 10000, after: 33400, exceeds: false, valid: true });
  assert.equal(refundPreview(43400, "43400.01").exceeds, true);
  assert.equal(refundPreview(43400, "43400.01").valid, false);
  assert.equal(refundPreview(43400, "0").valid, false);
  assert.equal(refundPreview(43400, "").valid, false);
  // The default is the server's whole refundable balance.
  assert.ok(DESK.includes("setAmount(next.refund.may_record ? next.refund.max_amount.toFixed(2) : \"\");"));
  assert.equal(DEFAULT_REFUND_REASON, "Refund of unused inpatient advance after final inpatient settlement.");
});

test("the detail panel renders identity, admission, settlement, care and history", () => {
  for (const label of [
    "Ward / room / bed", "Admitted", "Discharged", "Responsible physician",
    "Estimated amount", "Advance received", "Other patient payments", "Settlement payments",
    "Total patient funds", "Actual delivered care", "Payer share", "Patient responsibility",
    "Advance applied", "Remaining due", "Refundable balance", "Financial state",
    "Delivered care", "Payment in", "Refund out",
  ]) {
    assert.ok(DESK.includes(label), label);
  }
  assert.ok(DESK.includes("detail.patient.identification_code"));
  assert.ok(DESK.includes("s.delivered_by_category.map"));
  // PAYMENT IN and REFUND OUT are separate tables, never a negative charge.
  assert.ok(DESK.includes("detail.payments_in.map"));
  assert.ok(DESK.includes("detail.refunds_out.map"));
  assert.ok(DESK.includes("paymentKindLabel(row.kind)"));
  assert.equal(paymentKindLabel("advance"), "Advance");
  assert.equal(paymentKindLabel("settlement"), "Settlement payment");
});

test("the accounting limitation is stated, never hidden", () => {
  assert.ok(DESK.includes("Operational refund recorded"));
  assert.ok(DESK.includes("REFUND_ACCOUNTING_PENDING"));
  assert.ok(DIALOG.includes("REFUND_ACCOUNTING_PENDING"));
  assert.doesNotMatch(DESK + DIALOG, /Posted to accounting/i);
});

test("Record refund opens the Review refund modal, not a browser confirm", () => {
  assert.ok(DESK.includes("setReviewing(true);"));
  assert.ok(DESK.includes("<AccountantRefundReviewDialog"));
  assert.doesNotMatch(DESK + DIALOG, /window\.confirm|\balert\(|\bconfirm\(/);
  assert.ok(DIALOG.includes('role="dialog"') && DIALOG.includes('aria-modal="true"'));
  assert.ok(DIALOG.includes('title="Review refund"'));
  for (const label of ["Patient", "MRN", "Encounter", "Admission", "Admission status", "Refundable before", "Refund amount", "Refundable after", "Reason"]) {
    assert.ok(DIALOG.includes(label), label);
  }
  // Back / X / Escape close it; Record refund is a pointer click.
  assert.ok(DIALOG.includes(">\n            Back\n"));
  assert.ok(DIALOG.includes('aria-label="Close refund review"'));
  assert.ok(DIALOG.includes('if (event.key === "Escape" && !busy) onClose();'));
  assert.ok(DIALOG.includes("if (event.detail === 0) return;"));
  assert.ok(DIALOG.includes('{busy ? "Recording…" : "Record refund"}'));
  assert.ok(DIALOG.includes("max-h-[88vh]"));
});

test("the refund goes through the Accountant BFF with one token per intended refund", () => {
  assert.ok(DESK.includes("fetch(`/api/accountant/admissions/${detail.admission.id}/refund`"));
  assert.ok(DESK.includes("idempotency_key: pending.current.token"));
  assert.ok(DESK.includes("pending.current = { signature, token: crypto.randomUUID() };"));
  // An unknown outcome keeps the token (replay, never a second refund).
  const unknown = DESK.slice(DESK.indexOf("} catch {\n      // Unknown outcome"));
  assert.doesNotMatch(unknown.slice(0, 300), /pending\.current = null/);
  // Success refreshes the account (from the response) and the queue.
  const success = DESK.slice(DESK.indexOf("pending.current = null;\n      applyDetail(payload.data);"));
  assert.ok(success.includes("setQueueToken((token) => token + 1);"));
  assert.doesNotMatch(DESK, /\/api\/cashier/);
});

test("the BFF routes are pass-throughs to the accountant API, the refund body rebuilt", () => {
  assert.ok(REFUND_ROUTE.includes("`/yoya-emr/api/v1/accountant/admissions/${parsed.value}/refund`"));
  assert.match(REFUND_ROUTE, /amount: body\.amount,\s*reason: body\.reason,\s*idempotency_key: idempotencyKey\.trim\(\),/);
  assert.ok(REFUND_ROUTE.includes("await requireOdooSession()"));
  assert.ok(WORKLIST_ROUTE.includes("`/yoya-emr/api/v1/accountant/worklist${url.search}`"));
  assert.ok(DETAIL_ROUTE.includes("`/yoya-emr/api/v1/accountant/admissions/${parsed.value}`"));
  assert.ok(SESSION_ROUTE.includes('"/yoya-emr/api/v1/accountant/session"'));
  for (const source of [REFUND_ROUTE, WORKLIST_ROUTE, DETAIL_ROUTE, SESSION_ROUTE]) {
    assert.doesNotMatch(source, /https?:\/\/|localhost|:8069/);
  }
});

test("no cashier payment entry, no clinical or admissions controls on the desk", () => {
  const both = DESK + DIALOG;
  for (const forbidden of [
    /Take advance/, /\bCollect\b/, /\/advance\b/, /\/payments\b/, /Record payment/,
    /Revise estimate/, /Request discharge/, /Finalize discharge/, /\bAdmit\b/, /\bTransfer\b/,
    /Release bed/, /diagnosis/i, /\bDispense\b/, /admission-estimate/,
  ]) {
    assert.doesNotMatch(both, forbidden, String(forbidden));
  }
  // No payment-method picker: payment_method is only READ in the history.
  assert.doesNotMatch(DESK, /<select/);
  assert.ok(DESK.includes("{row.payment_method ?? \"—\"}"));
});

test("the shell keeps the AWASH branding, the desk label and fullscreen", () => {
  assert.ok(LAYOUT.includes("hospitalBrand(session?.companyName)"));
  assert.ok(LAYOUT.includes("Accountant Desk"));
  assert.ok(LAYOUT.includes("<FullscreenToggle />"));
  assert.ok(LAYOUT.includes("<FrontDeskUserMenu"));
});

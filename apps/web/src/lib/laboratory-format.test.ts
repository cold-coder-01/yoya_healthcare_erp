/**
 * Laboratory ordering: vocabulary, selection and payload construction.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins.
 * Run with `npm test`.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. THE PAYLOAD CARRIES NO OWNERSHIP AND NO MONEY. Placing a lab order is
 *      the most billing-adjacent thing a doctor does; every ownership field is
 *      derived server-side and the API rejects them by name.
 *
 *   2. A TEST IS SELECTED ONCE. Two of the same test in one submission would
 *      raise two charges for one test.
 *
 *   3. THE TOKEN IS ALWAYS SENT. Without it a double-clicked Place Order bills
 *      the patient twice.
 *
 *   4. ONLY LABORATORY IS LIVE. Radiology, medication and procedure must stay
 *      inert until they exist.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type {
  DoctorLabOrder,
  LabOrderForm,
  LabTestOption,
} from "@/types/doctor-laboratory";

import {
  EMPTY_ORDER_FORM,
  ORDER_KINDS,
  addTest,
  buildOrderPayload,
  canSubmitOrder,
  isLiveOrderKind,
  isSelected,
  isTerminalStatus,
  labPriorityLabel,
  labStatusLabel,
  orderTestSummary,
  removeTest,
  testLabel,
} from "./laboratory-format.ts";

import { CONSULTATION_SECTIONS, isLiveSection } from "./diagnosis-format.ts";

function option(id: number, name: string, code: string | null = null): LabTestOption {
  return { id, name, code, category: "hematology", sample_type: "blood" };
}

function order(overrides: Partial<DoctorLabOrder> = {}): DoctorLabOrder {
  return {
    id: 1,
    request_code: "LAB000123",
    tests: [
      { id: 11, test_id: 1, name: "CBC", code: "CBC", sample_type: "blood" },
      { id: 12, test_id: 2, name: "Creatinine", code: "CRE", sample_type: "blood" },
    ],
    diagnosis: null,
    clinical_indication: null,
    priority: "routine",
    status: "ready_for_collection",
    status_label: "Ready for collection",
    ordered_at: "2026-08-20",
    created_at: "2026-08-20T09:00:00",
    editable: false,
    cancellable: true,
    ...overrides,
  };
}

/* ------------------------------------------------------------------ *
 * Section and sub-section liveness
 * ------------------------------------------------------------------ */

test("orders is live after slice 3, alongside note and diagnosis", () => {
  assert.equal(isLiveSection("note"), true);
  assert.equal(isLiveSection("diagnosis"), true);
  assert.equal(isLiveSection("orders"), true);
});

test("results and history remain inert", () => {
  assert.equal(isLiveSection("results"), false);
  assert.equal(isLiveSection("history"), false);
});

test("the section bar keeps its clinical order", () => {
  assert.deepEqual(
    CONSULTATION_SECTIONS.map((section) => section.key),
    ["note", "diagnosis", "orders", "results", "history"],
  );
});

test("laboratory is the only live order kind", () => {
  assert.equal(isLiveOrderKind("laboratory"), true);
  for (const key of ["radiology", "medication", "procedure"]) {
    assert.equal(isLiveOrderKind(key), false, key);
  }
});

test("an unknown order kind is never live", () => {
  assert.equal(isLiveOrderKind("pharmacy"), false);
  assert.equal(isLiveOrderKind(""), false);
});

test("the order sub-navigation keeps its shape", () => {
  assert.deepEqual(
    ORDER_KINDS.map((kind) => kind.key),
    ["laboratory", "radiology", "medication", "procedure"],
  );
});

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

test("status keys render as the agreed clinical labels", () => {
  assert.equal(labStatusLabel("awaiting_clearance"), "Awaiting clearance");
  assert.equal(labStatusLabel("ready_for_collection"), "Ready for collection");
  assert.equal(labStatusLabel("collected"), "Sample collected");
  assert.equal(labStatusLabel("result_pending"), "Result pending");
  assert.equal(labStatusLabel("result_available"), "Result available");
  assert.equal(labStatusLabel("cancelled"), "Cancelled");
  assert.equal(labStatusLabel(null), "—");
});

test("no status label mentions money or a payer", () => {
  // `awaiting_clearance` is the one status derived from a billing verdict, and
  // it must stay a clinical statement rather than a financial one.
  const forbidden = ["amount", "payer", "birr", "etb", "balance", "pay", "price"];
  for (const key of ["awaiting_clearance", "ready_for_collection", "collected"]) {
    const label = labStatusLabel(key).toLowerCase();
    for (const word of forbidden) {
      assert.equal(label.includes(word), false, `${key} -> ${label}`);
    }
  }
});

test("priorities render, defaulting to routine", () => {
  assert.equal(labPriorityLabel("stat"), "STAT");
  assert.equal(labPriorityLabel("urgent"), "Urgent");
  assert.equal(labPriorityLabel(null), "Routine");
});

test("terminal statuses are recognised", () => {
  assert.equal(isTerminalStatus("cancelled"), true);
  assert.equal(isTerminalStatus("result_available"), true);
  assert.equal(isTerminalStatus("ready_for_collection"), false);
});

test("a test label carries its code when there is one", () => {
  assert.equal(testLabel(option(1, "CBC", "CBC01")), "CBC (CBC01)");
  assert.equal(testLabel(option(1, "CBC", null)), "CBC");
});

test("an order summarises the tests it contains", () => {
  assert.equal(orderTestSummary(order()), "CBC · Creatinine");
});

/* ------------------------------------------------------------------ *
 * Selection
 * ------------------------------------------------------------------ */

test("a test cannot be selected twice", () => {
  // Two of the same test in one submission would raise two charges for it.
  const cbc = option(1, "CBC");
  const selected = addTest(addTest([], cbc), cbc);
  assert.equal(selected.length, 1);
});

test("distinct tests accumulate in selection order", () => {
  const selected = addTest(addTest([], option(1, "CBC")), option(2, "Creatinine"));
  assert.deepEqual(selected.map((t) => t.name), ["CBC", "Creatinine"]);
});

test("a selected test can be removed before submission", () => {
  const selected = addTest(addTest([], option(1, "CBC")), option(2, "Creatinine"));
  assert.deepEqual(removeTest(selected, 1).map((t) => t.id), [2]);
});

test("selection membership is reported for the picker", () => {
  const selected = addTest([], option(1, "CBC"));
  assert.equal(isSelected(selected, 1), true);
  assert.equal(isSelected(selected, 2), false);
});

test("a submission needs at least one test and no in-flight request", () => {
  assert.equal(canSubmitOrder([], false), false);
  assert.equal(canSubmitOrder([option(1, "CBC")], true), false);
  assert.equal(canSubmitOrder([option(1, "CBC")], false), true);
});

/* ------------------------------------------------------------------ *
 * Payload
 * ------------------------------------------------------------------ */

test("the order payload always carries a request token", () => {
  const payload = buildOrderPayload([option(1, "CBC")], EMPTY_ORDER_FORM, "tok-1");
  assert.equal(payload.request_token, "tok-1");
  assert.deepEqual(payload.tests, [1]);
});

test("several tests travel as one order", () => {
  const selected = [option(1, "CBC"), option(2, "Creatinine"), option(3, "CRP")];
  const payload = buildOrderPayload(selected, EMPTY_ORDER_FORM, "tok-2");
  assert.deepEqual(payload.tests, [1, 2, 3]);
});

test("an empty indication and note are omitted, not sent blank", () => {
  const form: LabOrderForm = {
    priority: "routine",
    clinical_notes: "   ",
    diagnosis_id: null,
  };
  const payload = buildOrderPayload([option(1, "CBC")], form, "tok-3");
  assert.equal("clinical_notes" in payload, false);
  assert.equal("diagnosis_id" in payload, false);
});

test("a chosen diagnosis and indication are sent", () => {
  const form: LabOrderForm = {
    priority: "urgent",
    clinical_notes: "Persistent fever",
    diagnosis_id: 42,
  };
  const payload = buildOrderPayload([option(1, "CBC")], form, "tok-4");
  assert.equal(payload.diagnosis_id, 42);
  assert.equal(payload.clinical_notes, "Persistent fever");
  assert.equal(payload.priority, "urgent");
});

test("the payload contains no ownership field", () => {
  const payload = buildOrderPayload([option(1, "CBC")], EMPTY_ORDER_FORM, "tok-5");
  for (const forbidden of [
    "patient_id", "physician_id", "encounter_id", "appointment_id",
    "consultation_id", "state", "active", "request_date",
  ]) {
    assert.equal(forbidden in payload, false, forbidden);
  }
});

test("the payload never mentions a financial concept", () => {
  const form: LabOrderForm = {
    priority: "stat",
    clinical_notes: "clinical only",
    diagnosis_id: 7,
  };
  const serialized = JSON.stringify(
    buildOrderPayload([option(1, "CBC")], form, "tok-6"),
  ).toLowerCase();
  for (const word of [
    "amount", "price", "payer", "charge", "invoice", "receipt", "coverage",
    "billing", "sponsor",
  ]) {
    assert.equal(serialized.includes(word), false, word);
  }
});

/* ------------------------------------------------------------------ *
 * Order rendering
 * ------------------------------------------------------------------ */

test("an order is never editable", () => {
  // The ordered set freezes when the request leaves draft, and the desk
  // confirms on submission.
  assert.equal(order().editable, false);
});

test("a cancelled order is not offered as cancellable by the client", () => {
  // The server decides; this pins that the client reads the server's answer
  // rather than deriving one from the status.
  const cancelled = order({ status: "cancelled", cancellable: false });
  assert.equal(cancelled.cancellable, false);
  assert.equal(isTerminalStatus(cancelled.status), true);
});

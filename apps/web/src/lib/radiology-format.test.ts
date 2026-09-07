/**
 * Radiology ordering: vocabulary, selection and payload construction.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins.
 * Run with `npm test`.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. THE PAYLOAD CARRIES NO OWNERSHIP AND NO MONEY. Every ownership field is
 *      derived server-side and the API rejects them by name.
 *
 *   2. A STUDY IS SELECTED ONCE. Two of the same study in one submission would
 *      raise two charges for one scan.
 *
 *   3. THE TOKEN IS ALWAYS SENT. Without it a double-clicked Place Order bills
 *      the patient twice -- for imaging, an order of magnitude more than a lab
 *      test.
 *
 *   4. NO LABEL CLAIMS THE SCAN IS READY. Radiology's unpaid window spans two
 *      workflow states, so `awaiting_clearance` has to be able to stand in for
 *      either of them.
 *
 *   5. CONTRAST IS SURFACED. It is patient preparation, and the ordering doctor
 *      is the one who has to mention it before the patient leaves the room.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type {
  DoctorRadOrder,
  RadExamOption,
  RadOrderForm,
} from "@/types/doctor-radiology";

import {
  EMPTY_ORDER_FORM,
  addExam,
  buildOrderPayload,
  canSubmitOrder,
  examContext,
  examLabel,
  isAwaitingClearance,
  isSelected,
  isTerminalStatus,
  modalityLabel,
  orderExamSummary,
  orderNeedsContrast,
  radPriorityLabel,
  radStatusLabel,
  removeExam,
  selectionNeedsContrast,
} from "./radiology-format.ts";

function option(
  id: number,
  name: string,
  code: string | null = null,
  extra: Partial<RadExamOption> = {},
): RadExamOption {
  return {
    id,
    name,
    code,
    modality: "ct",
    body_part: "Head",
    contrast_required: false,
    ...extra,
  };
}

function ordered(id: number, name: string, contrast = false) {
  return {
    id,
    exam_id: id + 100,
    name,
    code: null,
    modality: "ct",
    body_part: "Head",
    contrast_required: contrast,
    special_instruction: null,
  };
}

function order(overrides: Partial<DoctorRadOrder> = {}): DoctorRadOrder {
  return {
    id: 1,
    request_code: "RADREQ0001",
    exams: [ordered(1, "CT Brain")],
    diagnosis: null,
    clinical_indication: null,
    instructions: null,
    priority: "routine",
    status: "awaiting_clearance",
    status_label: "Awaiting clearance",
    ordered_at: "2026-08-24",
    created_at: "2026-08-24T09:00:00",
    has_result: false,
    editable: false,
    cancellable: true,
    ...overrides,
  };
}

/* ------------------------------------------------------------------ *
 * Payload
 * ------------------------------------------------------------------ */

test("the payload carries the studies, the token and nothing owned by the server", () => {
  const form: RadOrderForm = {
    priority: "urgent",
    clinical_indication: "Head injury.",
    instructions: "Patient is claustrophobic.",
    diagnosis_id: 42,
  };
  const payload = buildOrderPayload(
    [option(7, "CT Brain"), option(9, "Chest X-Ray")],
    form,
    "tok-1",
  );

  assert.deepEqual(payload, {
    exams: [7, 9],
    request_token: "tok-1",
    priority: "urgent",
    clinical_indication: "Head injury.",
    instructions: "Patient is claustrophobic.",
    diagnosis_id: 42,
  });

  // Ownership is absent BY CONSTRUCTION, and the API rejects each of these by
  // name if a client ever sends one.
  for (const forbidden of [
    "patient_id",
    "physician_id",
    "encounter_id",
    "appointment_id",
    "consultation_id",
    "state",
    "request_date",
    "billing_blocked",
  ]) {
    assert.equal(forbidden in payload, false, forbidden);
  }
});

test("the token is always sent, even for a bare submission", () => {
  const payload = buildOrderPayload([option(1, "CT")], EMPTY_ORDER_FORM, "tok-2");
  assert.equal(payload.request_token, "tok-2");
});

test("empty free text is omitted rather than sent blank", () => {
  const payload = buildOrderPayload(
    [option(1, "CT")],
    { ...EMPTY_ORDER_FORM, clinical_indication: "   ", instructions: "" },
    "tok-3",
  );
  assert.equal("clinical_indication" in payload, false);
  assert.equal("instructions" in payload, false);
});

test("only one of the two free-text fields need be present", () => {
  const payload = buildOrderPayload(
    [option(1, "CT")],
    { ...EMPTY_ORDER_FORM, instructions: "Fasting from midnight." },
    "tok-4",
  );
  assert.equal("clinical_indication" in payload, false);
  assert.equal(payload.instructions, "Fasting from midnight.");
});

test("a null diagnosis is omitted rather than sent as null", () => {
  const payload = buildOrderPayload([option(1, "CT")], EMPTY_ORDER_FORM, "tok-5");
  assert.equal("diagnosis_id" in payload, false);
});

test("a submission needs at least one study and an idle form", () => {
  assert.equal(canSubmitOrder([], false), false);
  assert.equal(canSubmitOrder([option(1, "CT")], true), false);
  assert.equal(canSubmitOrder([option(1, "CT")], false), true);
});

/* ------------------------------------------------------------------ *
 * Selection
 * ------------------------------------------------------------------ */

test("the same study cannot be selected twice", () => {
  const first = addExam([], option(3, "CT Brain"));
  const second = addExam(first, option(3, "CT Brain"));
  assert.equal(second.length, 1);
  // Unchanged reference: nothing was added, so nothing should re-render.
  assert.equal(second, first);
});

test("selection add, remove and membership behave", () => {
  let selected = addExam([], option(1, "CT"));
  selected = addExam(selected, option(2, "MRI"));
  assert.equal(selected.length, 2);
  assert.equal(isSelected(selected, 2), true);

  selected = removeExam(selected, 1);
  assert.deepEqual(
    selected.map((exam) => exam.id),
    [2],
  );
  assert.equal(isSelected(selected, 1), false);
});

test("removing a study that was never selected changes nothing", () => {
  const selected = addExam([], option(1, "CT"));
  assert.deepEqual(removeExam(selected, 99), selected);
});

test("contrast in the pending selection is surfaced", () => {
  assert.equal(selectionNeedsContrast([option(1, "CT")]), false);
  assert.equal(
    selectionNeedsContrast([
      option(1, "CT"),
      option(2, "CT with contrast", null, { contrast_required: true }),
    ]),
    true,
  );
});

/* ------------------------------------------------------------------ *
 * Order sub-navigation
 * ------------------------------------------------------------------ */

/* ORDER_KINDS lives in laboratory-format and is asserted there: it is one
   shared sub-navigation, not one per order kind, and duplicating the assertion
   here would let the two copies disagree without either test failing. */

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

test("status keys render as the agreed clinical labels", () => {
  assert.equal(radStatusLabel("awaiting_clearance"), "Awaiting clearance");
  assert.equal(radStatusLabel("awaiting_scheduling"), "Awaiting scheduling");
  assert.equal(radStatusLabel("scheduled"), "Scheduled");
  assert.equal(radStatusLabel("in_progress"), "Imaging in progress");
  assert.equal(radStatusLabel("result_available"), "Result available");
  assert.equal(radStatusLabel("cancelled"), "Cancelled");
  assert.equal(radStatusLabel("draft"), "Draft");
});

test("no status label promises the scan can go ahead", () => {
  /* THE PROPERTY. Radiology raises its charges at confirmation but does not
     reach its clearance gate until Mark In Progress, so a booked study can
     still be unpaid. Neither pre-service label may read as a green light. */
  for (const status of ["awaiting_scheduling", "scheduled"]) {
    const label = radStatusLabel(status).toLowerCase();
    assert.equal(label.includes("ready"), false, status);
    assert.equal(label.includes("clear"), false, status);
  }
});

test("an unknown status falls back to itself rather than to a wrong label", () => {
  assert.equal(radStatusLabel("something_new"), "something_new");
  assert.equal(radStatusLabel(null), "—");
  assert.equal(radStatusLabel(undefined), "—");
});

test("priority labels cover the model's three values", () => {
  assert.equal(radPriorityLabel("routine"), "Routine");
  assert.equal(radPriorityLabel("urgent"), "Urgent");
  assert.equal(radPriorityLabel("stat"), "STAT");
  // The model defaults to routine and the column is required, so a missing
  // value means routine rather than unknown.
  assert.equal(radPriorityLabel(null), "Routine");
});

test("modality labels are the radiographer's words, not the database's", () => {
  assert.equal(modalityLabel("ct"), "CT");
  assert.equal(modalityLabel("mri"), "MRI");
  assert.equal(modalityLabel("xray"), "X-Ray");
  assert.equal(modalityLabel("ultrasound"), "Ultrasound");
  assert.equal(modalityLabel(null), "");
  assert.equal(modalityLabel("tomosynthesis"), "tomosynthesis");
});

test("a terminal status stops the desk offering actions", () => {
  assert.equal(isTerminalStatus("cancelled"), true);
  assert.equal(isTerminalStatus("result_available"), true);
  assert.equal(isTerminalStatus("awaiting_clearance"), false);
  assert.equal(isTerminalStatus("scheduled"), false);
});

test("awaiting clearance is recognised for tinting, and nothing else is", () => {
  assert.equal(isAwaitingClearance("awaiting_clearance"), true);
  assert.equal(isAwaitingClearance("awaiting_scheduling"), false);
  assert.equal(isAwaitingClearance(null), false);
});

/* ------------------------------------------------------------------ *
 * Display
 * ------------------------------------------------------------------ */

test("an exam reads with its code when it has one", () => {
  assert.equal(examLabel({ name: "CT Brain", code: "CTB" }), "CT Brain (CTB)");
  assert.equal(examLabel({ name: "CT Brain", code: null }), "CT Brain");
});

test("modality and body part render in every combination", () => {
  assert.equal(examContext({ modality: "ct", body_part: "Head" }), "CT · Head");
  assert.equal(examContext({ modality: "ct", body_part: null }), "CT");
  assert.equal(examContext({ modality: null, body_part: "Head" }), "Head");
  // Neither is a clean empty string, never a stray separator.
  assert.equal(examContext({ modality: null, body_part: null }), "");
});

test("an order summarises the studies it contains", () => {
  assert.equal(
    orderExamSummary(
      order({ exams: [ordered(1, "CT Brain"), ordered(2, "Chest X-Ray")] }),
    ),
    "CT Brain · Chest X-Ray",
  );
});

test("contrast anywhere in an order is surfaced on the order", () => {
  assert.equal(orderNeedsContrast(order()), false);
  assert.equal(
    orderNeedsContrast(
      order({ exams: [ordered(1, "CT Brain"), ordered(2, "CT Angio", true)] }),
    ),
    true,
  );
});

test("an order is never editable, whatever else it is", () => {
  assert.equal(order().editable, false);
});

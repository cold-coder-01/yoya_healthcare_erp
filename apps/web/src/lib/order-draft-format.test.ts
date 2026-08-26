/**
 * WHAT AN UNSENT CLINICAL ORDER IS ALLOWED TO LOSE, asserted rather than hoped.
 *
 * THE BUG THESE TESTS EXIST FOR. Staged clinical work used to live in whichever
 * order panel happened to be mounted, and the ORDERS tabs mount one panel at a
 * time. A doctor who staged Cetirizine, glanced at Laboratory and came back
 * found the prescription gone -- dose, quantity, pharmacy note and all --
 * because a navigation click unmounted the component holding it.
 *
 * WHAT IS PINNED HERE, in the order it would hurt:
 *
 *   1. NOTHING BUT THE FOUR ALLOWED EVENTS CLEARS A DRAFT. A definitive
 *      submission success, an explicit discard, a patient change, and a
 *      consultation becoming read-only. Tab navigation is not on that list and
 *      has no function here that could put it there.
 *
 *   2. EVERY UNSENT FIELD SURVIVES, not merely the item list. A quantity, a
 *      preparation instruction or a pharmacy note lost in transit is a
 *      prescribing error waiting to happen.
 *
 *   3. THE PRESCRIPTION TOKEN SURVIVES A FAILED ATTEMPT. It identifies the
 *      SUBMISSION; regenerating it on a retry would let the server write a
 *      second prescription with a second pharmacy dispense behind it.
 *
 *   4. ONE KIND'S DRAFT NEVER DISTURBS ANOTHER'S.
 *
 *   5. THE TAB INDICATOR COUNTS UNSENT WORK AND NOTHING ELSE.
 *
 * The DOM behaviour that rests on these -- which component holds the state, and
 * where the provider is mounted -- is pinned by order-wizard-contract.test.ts,
 * because this project ships no DOM test stack.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { LabTestOption } from "@/types/doctor-laboratory";
import type { StagedMedicine } from "@/types/doctor-medication";
import type { RadExamOption } from "@/types/doctor-radiology";

import { EMPTY_ORDER_FORM as EMPTY_LAB_FORM } from "./laboratory-format.ts";
import { EMPTY_PRESCRIPTION_FORM } from "./medication-format.ts";
import { EMPTY_ORDER_FORM as EMPTY_RAD_FORM } from "./radiology-format.ts";
import {
  DRAFTABLE_ORDER_KINDS,
  EMPTY_LAB_DRAFT,
  EMPTY_MED_DRAFT,
  EMPTY_ORDER_DRAFTS,
  EMPTY_RAD_DRAFT,
  clearAllDrafts,
  clearDraft,
  draftFormTouched,
  draftIndicator,
  draftItemCount,
  hasDraftWork,
  isDraftableOrderKind,
  labDraftChanged,
  prescriptionToken,
  putDraft,
  radDraftChanged,
  unfinishedDraftKinds,
} from "./order-draft-format.ts";

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

const CBC: LabTestOption = {
  id: 11,
  name: "Complete blood count",
  code: "LAB-CBC",
  category: "Haematology",
  sample_type: "blood",
};

const UREA: LabTestOption = {
  id: 12,
  name: "Urea and electrolytes",
  code: "LAB-UE",
  category: "Chemistry",
  sample_type: "blood",
};

const CHEST_XRAY: RadExamOption = {
  id: 21,
  name: "Chest X-Ray",
  code: "RAD-CXR",
  modality: "xray",
  body_part: "Chest",
  contrast_required: false,
};

const CETIRIZINE: StagedMedicine = {
  key: "staged-1",
  medicine: {
    id: 31,
    name: "Cetirizine",
    code: "MED-CTZ",
    generic_name: "Cetirizine hydrochloride",
    brand_name: null,
    dosage_form: "tablet",
    strength: "10mg",
    route: "oral",
    category: "Antihistamine",
  },
  quantity: "10",
  dosage: "10mg",
  route: "oral",
  frequency: "once daily",
  duration: "10 days",
  instructions: "Take at night",
};

const AMOXICILLIN: StagedMedicine = {
  key: "staged-2",
  medicine: {
    id: 32,
    name: "Amoxicillin",
    code: "MED-AMOX",
    generic_name: null,
    brand_name: null,
    dosage_form: "capsule",
    strength: "500mg",
    route: "oral",
    category: "Antibiotic",
  },
  quantity: "21",
  dosage: "500mg",
  route: "oral",
  frequency: "three times daily",
  duration: "7 days",
  instructions: "Complete the course",
};

/** The full consultation state a doctor might be part-way through. */
function busyDrafts() {
  let drafts = EMPTY_ORDER_DRAFTS;
  drafts = putDraft(drafts, "laboratory", {
    tests: [CBC, UREA],
    form: {
      priority: "urgent",
      clinical_notes: "Febrile, query sepsis",
      diagnosis_id: 7,
    },
  });
  drafts = putDraft(drafts, "radiology", {
    exams: [CHEST_XRAY],
    form: {
      priority: "routine",
      clinical_indication: "Cough for three weeks",
      instructions: "Patient uses a wheelchair",
      diagnosis_id: null,
    },
  });
  drafts = putDraft(drafts, "medication", {
    staged: [CETIRIZINE, AMOXICILLIN],
    form: { diagnosis_id: 7, notes: "Dispense the syrup if tablets are out" },
    token: "token-abc",
  });
  return drafts;
}

/* ------------------------------------------------------------------ *
 * The empty forms have not drifted from their runtime originals
 * ------------------------------------------------------------------ */

test("the empty drafts still match the order forms the payload builders consume", () => {
  /*
    order-draft-format restates these rather than importing them, so that the
    three format modules stay free of cross-module value imports and node:test
    can keep running with no resolver. That trade is only safe while a test
    holds the two copies together.
  */
  assert.deepEqual(EMPTY_LAB_DRAFT.form, EMPTY_LAB_FORM);
  assert.deepEqual(EMPTY_RAD_DRAFT.form, EMPTY_RAD_FORM);
  assert.deepEqual(EMPTY_MED_DRAFT.form, EMPTY_PRESCRIPTION_FORM);
});

test("an empty draft stages nothing and carries no token", () => {
  assert.deepEqual(EMPTY_LAB_DRAFT.tests, []);
  assert.deepEqual(EMPTY_RAD_DRAFT.exams, []);
  assert.deepEqual(EMPTY_MED_DRAFT.staged, []);
  assert.equal(EMPTY_MED_DRAFT.token, null);
});

/* ------------------------------------------------------------------ *
 * 1-2. Drafts survive everything except the four allowed events
 * ------------------------------------------------------------------ */

test("writing one kind's draft leaves the other kinds untouched", () => {
  const drafts = busyDrafts();

  // The lab tab is re-entered and its request is edited. Radiology and
  // medication must not notice.
  const after = putDraft(drafts, "laboratory", {
    ...drafts.laboratory,
    tests: [CBC],
  });

  assert.equal(after.radiology, drafts.radiology);
  assert.equal(after.medication, drafts.medication);
  assert.deepEqual(after.laboratory.form, drafts.laboratory.form);
});

test("every unsent field survives, not merely the item list", () => {
  /*
    Laboratory -> Radiology -> Medication -> Laboratory is state the panels read
    from, never state they own, so a round trip is the identity function. Field
    by field, because "the tests came back" would still be a bug if the
    indication had not.
  */
  const drafts = busyDrafts();
  const roundTripped = putDraft(
    putDraft(putDraft(drafts, "radiology", drafts.radiology), "medication", drafts.medication),
    "laboratory",
    drafts.laboratory,
  );

  assert.deepEqual(roundTripped, drafts);

  assert.deepEqual(roundTripped.laboratory.tests.map((test) => test.id), [11, 12]);
  assert.equal(roundTripped.laboratory.form.priority, "urgent");
  assert.equal(
    roundTripped.laboratory.form.clinical_notes,
    "Febrile, query sepsis",
  );
  assert.equal(roundTripped.laboratory.form.diagnosis_id, 7);

  assert.deepEqual(roundTripped.radiology.exams.map((exam) => exam.id), [21]);
  assert.equal(
    roundTripped.radiology.form.clinical_indication,
    "Cough for three weeks",
  );
  assert.equal(
    roundTripped.radiology.form.instructions,
    "Patient uses a wheelchair",
  );

  // Every per-medicine field, because each is a separate prescribing decision.
  const [first, second] = roundTripped.medication.staged;
  assert.equal(first.medicine.name, "Cetirizine");
  assert.equal(first.quantity, "10");
  assert.equal(first.dosage, "10mg");
  assert.equal(first.route, "oral");
  assert.equal(first.frequency, "once daily");
  assert.equal(first.duration, "10 days");
  assert.equal(first.instructions, "Take at night");
  assert.equal(second.medicine.name, "Amoxicillin");
  assert.equal(second.quantity, "21");
  assert.equal(roundTripped.medication.form.diagnosis_id, 7);
  assert.equal(
    roundTripped.medication.form.notes,
    "Dispense the syrup if tablets are out",
  );
});

test("multiple staged medicines all survive, including the same drug twice", () => {
  const tapering: StagedMedicine = { ...CETIRIZINE, key: "staged-3", duration: "3 days" };
  const drafts = putDraft(busyDrafts(), "medication", {
    ...busyDrafts().medication,
    staged: [CETIRIZINE, AMOXICILLIN, tapering],
  });

  assert.equal(draftItemCount(drafts, "medication"), 3);
  // The same medicine id twice is legitimate -- a course and a rescue dose --
  // so nothing here may de-duplicate it.
  assert.deepEqual(
    drafts.medication.staged.map((entry) => entry.medicine.id),
    [31, 32, 31],
  );
  assert.deepEqual(
    drafts.medication.staged.map((entry) => entry.key),
    ["staged-1", "staged-2", "staged-3"],
  );
});

/* ------------------------------------------------------------------ *
 * 3. The prescription token
 * ------------------------------------------------------------------ */

test("an existing prescription token is reused, never regenerated", () => {
  const drafts = busyDrafts();
  const minted = prescriptionToken(drafts.medication, () => "token-NEW");
  assert.equal(
    minted,
    "token-abc",
    "a retry must carry the SAME token or the server writes a second prescription",
  );
});

test("a prescription with no token yet mints one", () => {
  assert.equal(prescriptionToken(EMPTY_MED_DRAFT, () => "token-first"), "token-first");
});

test("laboratory and radiology drafts hold no token at all", () => {
  /*
    Both endpoints have always minted their token inside the submission and
    never retained one between attempts. Moving that into the draft would change
    idempotency behaviour rather than merely move where an unsent form is held,
    so the shape refuses to carry one.
  */
  assert.equal("token" in EMPTY_LAB_DRAFT, false);
  assert.equal("token" in EMPTY_RAD_DRAFT, false);
});

/* ------------------------------------------------------------------ *
 * 4. Clearing: only the allowed events, and only what they own
 * ------------------------------------------------------------------ */

test("a submitted kind's draft is cleared and the others are left alone", () => {
  const drafts = busyDrafts();
  const afterPrescribing = clearDraft(drafts, "medication");

  assert.deepEqual(afterPrescribing.medication, EMPTY_MED_DRAFT);
  assert.equal(
    afterPrescribing.medication.token,
    null,
    "a definitive success ends the submission, so its token ends with it",
  );
  // The lab request the doctor had not sent yet is still there.
  assert.equal(afterPrescribing.laboratory, drafts.laboratory);
  assert.equal(afterPrescribing.radiology, drafts.radiology);
});

test("an explicit discard clears exactly one kind", () => {
  const drafts = busyDrafts();
  const afterRemove = clearDraft(drafts, "laboratory");
  assert.deepEqual(afterRemove.laboratory, EMPTY_LAB_DRAFT);
  assert.equal(afterRemove.medication, drafts.medication);
});

test("a patient change and a completed consultation clear everything", () => {
  assert.deepEqual(clearAllDrafts(), EMPTY_ORDER_DRAFTS);
  for (const kind of DRAFTABLE_ORDER_KINDS) {
    assert.equal(hasDraftWork(clearAllDrafts(), kind), false);
  }
});

/* ------------------------------------------------------------------ *
 * 5. The unfinished-work indicator
 * ------------------------------------------------------------------ */

test("the indicator counts staged items per kind", () => {
  const drafts = busyDrafts();
  assert.deepEqual(draftIndicator(drafts, "laboratory"), { dirty: true, count: 2 });
  assert.deepEqual(draftIndicator(drafts, "radiology"), { dirty: true, count: 1 });
  assert.deepEqual(draftIndicator(drafts, "medication"), { dirty: true, count: 2 });
});

test("a typed form with nothing staged still marks the tab, without a count", () => {
  const drafts = putDraft(EMPTY_ORDER_DRAFTS, "radiology", {
    exams: [],
    form: {
      priority: "routine",
      clinical_indication: "",
      instructions: "Fasting from midnight",
      diagnosis_id: null,
    },
  });
  assert.deepEqual(draftIndicator(drafts, "radiology"), { dirty: true, count: 0 });
});

test("whitespace is not unsent work", () => {
  const drafts = putDraft(EMPTY_ORDER_DRAFTS, "laboratory", {
    tests: [],
    form: { priority: "routine", clinical_notes: "   \n ", diagnosis_id: null },
  });
  assert.equal(draftFormTouched(drafts, "laboratory"), false);
  assert.deepEqual(draftIndicator(drafts, "laboratory"), { dirty: false, count: 0 });
});

test("a raised priority alone counts as unsent work", () => {
  const drafts = putDraft(EMPTY_ORDER_DRAFTS, "laboratory", {
    tests: [],
    form: { priority: "stat", clinical_notes: "", diagnosis_id: null },
  });
  assert.equal(hasDraftWork(drafts, "laboratory"), true);
});

test("an empty consultation marks no tab at all", () => {
  assert.deepEqual(unfinishedDraftKinds(EMPTY_ORDER_DRAFTS), []);
  for (const kind of DRAFTABLE_ORDER_KINDS) {
    assert.deepEqual(draftIndicator(EMPTY_ORDER_DRAFTS, kind), {
      dirty: false,
      count: 0,
    });
  }
});

test("the indicator disappears once the draft is submitted or discarded", () => {
  const drafts = busyDrafts();
  assert.deepEqual(unfinishedDraftKinds(drafts), [
    "laboratory",
    "radiology",
    "medication",
  ]);

  const afterPrescribing = clearDraft(drafts, "medication");
  assert.deepEqual(unfinishedDraftKinds(afterPrescribing), [
    "laboratory",
    "radiology",
  ]);
  assert.deepEqual(draftIndicator(afterPrescribing, "medication"), {
    dirty: false,
    count: 0,
  });
});

/* ------------------------------------------------------------------ *
 * Procedure has no draft slot, because it has no order flow
 * ------------------------------------------------------------------ */

test("only the three implemented order kinds can hold a draft", () => {
  assert.deepEqual([...DRAFTABLE_ORDER_KINDS], [
    "laboratory",
    "radiology",
    "medication",
  ]);
  assert.equal(isDraftableOrderKind("procedure"), false);
  assert.equal(isDraftableOrderKind("diagnosis"), false);
  for (const kind of ["laboratory", "radiology", "medication"]) {
    assert.equal(isDraftableOrderKind(kind), true);
  }
});

/* ------------------------------------------------------------------ *
 * Editor dirty checks
 * ------------------------------------------------------------------ */

test("reopening an order editor on an unchanged draft reports no changes", () => {
  const drafts = busyDrafts();
  assert.equal(labDraftChanged(drafts.laboratory, { ...drafts.laboratory }), false);
  assert.equal(radDraftChanged(drafts.radiology, { ...drafts.radiology }), false);
});

test("the same test re-read from the catalogue is not a change", () => {
  const drafts = busyDrafts();
  // A fresh object with the same id: identity, not reference, is what counts.
  const reread = { ...drafts.laboratory, tests: [{ ...CBC }, { ...UREA }] };
  assert.equal(labDraftChanged(drafts.laboratory, reread), false);
});

test("every editable order field is watched by the dirty check", () => {
  const lab = busyDrafts().laboratory;
  assert.equal(labDraftChanged(lab, { ...lab, tests: [CBC] }), true);
  assert.equal(
    labDraftChanged(lab, { ...lab, form: { ...lab.form, priority: "stat" } }),
    true,
  );
  assert.equal(
    labDraftChanged(lab, { ...lab, form: { ...lab.form, clinical_notes: "x" } }),
    true,
  );
  assert.equal(
    labDraftChanged(lab, { ...lab, form: { ...lab.form, diagnosis_id: null } }),
    true,
  );

  const rad = busyDrafts().radiology;
  assert.equal(radDraftChanged(rad, { ...rad, exams: [] }), true);
  assert.equal(
    radDraftChanged(rad, { ...rad, form: { ...rad.form, priority: "urgent" } }),
    true,
  );
  assert.equal(
    radDraftChanged(rad, {
      ...rad,
      form: { ...rad.form, clinical_indication: "x" },
    }),
    true,
  );
  assert.equal(
    radDraftChanged(rad, { ...rad, form: { ...rad.form, instructions: "x" } }),
    true,
  );
  assert.equal(
    radDraftChanged(rad, { ...rad, form: { ...rad.form, diagnosis_id: 9 } }),
    true,
  );
});

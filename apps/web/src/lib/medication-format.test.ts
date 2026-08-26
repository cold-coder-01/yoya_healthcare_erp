/**
 * The medication prescribing contract, asserted rather than assumed.
 *
 * WHAT THESE TESTS ARE PROTECTING, in the order it would hurt:
 *
 *   1. ONE SUBMISSION IS ONE PRESCRIPTION WITH ONE TOKEN. The domain composes
 *      exactly one pharmacy dispense per prescription, so a token per staged
 *      medicine would let a retry write a second prescription from whatever
 *      had not matched -- and the patient would be dispensed twice.
 *
 *   2. QUANTITY IS NEVER GUESSED. The column has no required=True, but the
 *      pharmacy workflow silently ignores a zero-quantity line and the whole
 *      dispense then cannot be marked ready. A prescription with no quantity is
 *      an order nobody can fill.
 *
 *   3. THE SAME DRUG MAY BE STAGED TWICE. A tapering course and a rescue dose
 *      are two legitimate lines of one prescription, so the staging helpers
 *      must not de-duplicate the way the laboratory and radiology pickers do.
 *
 *   4. NOTHING PRICED OR STOCKED IS EVER BUILT INTO A PAYLOAD.
 *
 *   5. ONLY THE ORDER KINDS THAT EXIST ARE LIVE.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type {
  MedPrescriptionForm,
  MedicineOption,
  PrescribedMedicine,
  StagedMedicine,
} from "@/types/doctor-medication";

import {
  EMPTY_PRESCRIPTION_FORM,
  MED_STATUS_TONE,
  buildPrescriptionPayload,
  canSubmitPrescription,
  dosageFormLabel,
  fulfilment,
  isTerminalStatus,
  medStatusLabel,
  medicineContext,
  medicineCountLabel,
  medicineEditorChanged,
  medicineLabel,
  prescribedSummary,
  quantityError,
  routeLabel,
  stageMedicine,
  stagedRegimenSummary,
  stagedErrors,
  unstageMedicine,
  updateStaged,
} from "./medication-format.ts";

const AMOX: MedicineOption = {
  id: 2,
  name: "Amoxicillin",
  code: "MED-AMOX",
  generic_name: "Amoxicillin",
  brand_name: null,
  dosage_form: "capsule",
  strength: "500mg",
  route: "oral",
  category: "Antibiotic",
};

const CETIRIZINE: MedicineOption = {
  id: 5,
  name: "Cetirizine 10mg Tablet",
  code: "MED-CET-10-TAB",
  generic_name: "Cetirizine",
  brand_name: "Zyrtec",
  dosage_form: "tablet",
  strength: "10mg",
  route: "oral",
  category: null,
};

function staged(overrides: Partial<StagedMedicine> = {}): StagedMedicine {
  return {
    key: "k1",
    medicine: AMOX,
    quantity: "30",
    dosage: "500mg",
    route: "",
    frequency: "",
    duration: "",
    instructions: "",
    ...overrides,
  };
}

function line(overrides: Partial<PrescribedMedicine> = {}): PrescribedMedicine {
  return {
    id: 1,
    medicine_id: 2,
    name: "Amoxicillin",
    code: "MED-AMOX",
    dosage_form: "capsule",
    strength: "500mg",
    dosage: "500mg",
    route: "oral",
    frequency: "twice daily",
    duration: "5 days",
    quantity: 30,
    instructions: null,
    dispensed_quantity: null,
    remaining_quantity: null,
    ...overrides,
  };
}

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

test("status keys render as the agreed clinical labels", () => {
  assert.equal(medStatusLabel("awaiting_pharmacy"), "Awaiting pharmacy");
  assert.equal(medStatusLabel("ready_at_pharmacy"), "Ready at pharmacy");
  assert.equal(medStatusLabel("partially_dispensed"), "Partially dispensed");
  assert.equal(medStatusLabel("dispensed"), "Dispensed");
  assert.equal(medStatusLabel("cancelled"), "Cancelled");
});

test("an unknown status falls back rather than rendering a raw key", () => {
  assert.equal(medStatusLabel("something_new"), "Draft");
  assert.equal(medStatusLabel(null), "Draft");
});

test("no status label mentions money, stock or a payer", () => {
  const forbidden = ["paid", "unpaid", "birr", "etb", "stock", "payer", "price"];
  for (const key of [
    "draft",
    "awaiting_pharmacy",
    "ready_at_pharmacy",
    "partially_dispensed",
    "dispensed",
    "cancelled",
  ]) {
    const label = medStatusLabel(key).toLowerCase();
    for (const word of forbidden) {
      assert.equal(label.includes(word), false, `${key} leaked "${word}"`);
    }
  }
});

test("route labels use the prescription LINE vocabulary", () => {
  // Not the catalogue's. The line says `injection` where the catalogue
  // distinguishes iv/im/subcutaneous, and only the line's values are accepted.
  assert.equal(routeLabel("injection"), "Injection");
  assert.equal(routeLabel("eye_drop"), "Eye drop");
  assert.equal(routeLabel(null), null);
});

test("dosage form labels render, and unknown forms pass through", () => {
  assert.equal(dosageFormLabel("capsule"), "Capsule");
  assert.equal(dosageFormLabel("lozenge"), "lozenge");
  assert.equal(dosageFormLabel(null), null);
});

test("terminal statuses are the two nobody is waiting on", () => {
  assert.equal(isTerminalStatus("dispensed"), true);
  assert.equal(isTerminalStatus("cancelled"), true);
  assert.equal(isTerminalStatus("ready_at_pharmacy"), false);
  assert.equal(isTerminalStatus("partially_dispensed"), false);
});

/* ------------------------------------------------------------------ *
 * Catalogue display
 * ------------------------------------------------------------------ */

test("a medicine reads as name plus strength", () => {
  assert.equal(medicineLabel(AMOX), "Amoxicillin 500mg");
  assert.equal(
    medicineLabel({ name: "ORS Sachet", strength: null }),
    "ORS Sachet",
  );
});

test("the context line does not repeat the name as its own generic", () => {
  // Amoxicillin's generic name IS "Amoxicillin"; printing it twice is noise.
  assert.equal(medicineContext(AMOX), "Capsule");
  assert.equal(medicineContext(CETIRIZINE), "Tablet · Cetirizine");
});

test("the dose line joins only the fields that were filled", () => {
  assert.equal(
    prescribedSummary(line()),
    "500mg · Oral · twice daily · 5 days",
  );
  assert.equal(
    prescribedSummary(
      line({ dosage: null, frequency: null, duration: null, route: "oral" }),
    ),
    "Oral",
  );
  assert.equal(
    prescribedSummary(
      line({ dosage: null, route: null, frequency: null, duration: null }),
    ),
    "",
  );
});

test("medicine counts are singular and plural", () => {
  assert.equal(medicineCountLabel(1), "1 medicine");
  assert.equal(medicineCountLabel(3), "3 medicines");
});

/* ------------------------------------------------------------------ *
 * Fulfilment -- the D6 correlation limit
 * ------------------------------------------------------------------ */

test("fulfilment is null when the server could not correlate the line", () => {
  // Dispense lines carry no prescription_line_id. When the server cannot be
  // certain which line was filled it sends null, and the UI must render
  // nothing rather than an empty or invented figure.
  assert.equal(fulfilment(line()), null);
});

test("fulfilment reports progress when the server was certain", () => {
  const partial = fulfilment(
    line({ dispensed_quantity: 10, remaining_quantity: 20 }),
  );
  assert.deepEqual(partial, {
    dispensed: 10,
    remaining: 20,
    complete: false,
    started: true,
  });

  const done = fulfilment(
    line({ dispensed_quantity: 30, remaining_quantity: 0 }),
  );
  assert.equal(done?.complete, true);

  const untouched = fulfilment(
    line({ dispensed_quantity: 0, remaining_quantity: 30 }),
  );
  assert.equal(untouched?.started, false);
  assert.equal(untouched?.complete, false);
});

/* ------------------------------------------------------------------ *
 * Staging
 * ------------------------------------------------------------------ */

test("staging seeds the dose from the strength but never the quantity", () => {
  const [entry] = stageMedicine([], AMOX, "k1");
  assert.equal(entry.dosage, "500mg");
  // There is no safe default for how many of something a patient receives.
  assert.equal(entry.quantity, "");
  assert.equal(entry.route, "");
});

test("the same medicine may be staged twice", () => {
  // A tapering course and a rescue dose are two lines of one prescription.
  // Unlike the lab and radiology pickers, this must NOT de-duplicate.
  const list = stageMedicine(stageMedicine([], AMOX, "k1"), AMOX, "k2");
  assert.equal(list.length, 2);
  assert.deepEqual(
    list.map((entry) => entry.key),
    ["k1", "k2"],
  );
});

test("a staged row is removed by its own key, not by medicine", () => {
  const list = stageMedicine(stageMedicine([], AMOX, "k1"), AMOX, "k2");
  const left = unstageMedicine(list, "k1");
  assert.equal(left.length, 1);
  assert.equal(left[0].key, "k2");
});

test("editing a staged row touches only that row", () => {
  const list = stageMedicine(stageMedicine([], AMOX, "k1"), CETIRIZINE, "k2");
  const edited = updateStaged(list, "k2", { quantity: "14", route: "oral" });
  assert.equal(edited[0].quantity, "");
  assert.equal(edited[1].quantity, "14");
  assert.equal(edited[1].route, "oral");
  // The medicine itself is never rewritten by an edit.
  assert.equal(edited[1].medicine.id, CETIRIZINE.id);
});

test("the medication editor dirty check protects every editable field", () => {
  const opened = staged();
  assert.equal(medicineEditorChanged(opened, { ...opened }), false);
  for (const patch of [
    { dosage: "250mg" },
    { route: "oral" },
    { quantity: "60" },
    { frequency: "nightly" },
    { duration: "7 days" },
    { instructions: "after food" },
  ]) {
    assert.equal(medicineEditorChanged(opened, { ...opened, ...patch }), true);
  }
});

test("a staged medicine has one compact clinical review line", () => {
  assert.equal(
    stagedRegimenSummary(
      staged({
        route: "oral",
        frequency: "twice daily",
        duration: "5 days",
        quantity: "10",
      }),
    ),
    "500mg / Oral / twice daily / 5 days / Qty 10",
  );
});

test("prescription status tones use the agreed semantic colours", () => {
  assert.match(MED_STATUS_TONE.awaiting_pharmacy, /sky/);
  assert.match(MED_STATUS_TONE.ready_at_pharmacy, /amber/);
  assert.match(MED_STATUS_TONE.partially_dispensed, /orange/);
  assert.match(MED_STATUS_TONE.dispensed, /emerald/);
  assert.match(MED_STATUS_TONE.cancelled, /red/);
});

/* ------------------------------------------------------------------ *
 * Quantity validation
 * ------------------------------------------------------------------ */

test("quantity is required and must be a positive number", () => {
  assert.equal(quantityError("30"), null);
  assert.equal(quantityError("0.5"), null);
  assert.equal(quantityError(""), "Quantity is required.");
  assert.equal(quantityError("   "), "Quantity is required.");
  assert.equal(quantityError("abc"), "Quantity must be a number.");
  assert.equal(quantityError("0"), "Quantity must be greater than zero.");
  assert.equal(quantityError("-4"), "Quantity must be greater than zero.");
});

test("staged errors are keyed by row so each box can show its own", () => {
  const list = [
    staged({ key: "k1", quantity: "30" }),
    staged({ key: "k2", quantity: "" }),
  ];
  assert.deepEqual(stagedErrors(list), { k2: "Quantity is required." });
});

test("a prescription cannot be submitted empty, busy or invalid", () => {
  assert.equal(canSubmitPrescription([], false), false);
  assert.equal(canSubmitPrescription([staged()], true), false);
  assert.equal(canSubmitPrescription([staged({ quantity: "" })], false), false);
  assert.equal(canSubmitPrescription([staged()], false), true);
});

/* ------------------------------------------------------------------ *
 * Payload
 * ------------------------------------------------------------------ */

test("N medicines produce ONE payload carrying ONE token", () => {
  const list = [
    staged({ key: "k1", quantity: "30" }),
    staged({ key: "k2", medicine: CETIRIZINE, quantity: "14" }),
  ];
  const payload = buildPrescriptionPayload(
    list,
    EMPTY_PRESCRIPTION_FORM,
    "token-1",
  );
  assert.equal(payload.request_token, "token-1");
  assert.equal(payload.medicines.length, 2);
  assert.deepEqual(
    payload.medicines.map((m) => m.medicine_id),
    [AMOX.id, CETIRIZINE.id],
  );
});

test("quantity is sent as a number, not the raw input string", () => {
  const payload = buildPrescriptionPayload(
    [staged({ quantity: " 30 " })],
    EMPTY_PRESCRIPTION_FORM,
    "t",
  );
  assert.equal(payload.medicines[0].quantity, 30);
});

test("blank optional fields are omitted rather than sent as empty strings", () => {
  const payload = buildPrescriptionPayload(
    [staged({ dosage: "  ", route: "", frequency: "", duration: "", instructions: "" })],
    EMPTY_PRESCRIPTION_FORM,
    "t",
  );
  assert.deepEqual(Object.keys(payload.medicines[0]).sort(), [
    "medicine_id",
    "quantity",
  ]);
});

test("filled optional fields are trimmed and sent", () => {
  const payload = buildPrescriptionPayload(
    [
      staged({
        dosage: " 500mg ",
        route: "oral",
        frequency: " twice daily ",
        duration: " 5 days ",
        instructions: " after food ",
      }),
    ],
    EMPTY_PRESCRIPTION_FORM,
    "t",
  );
  const entry = payload.medicines[0];
  assert.equal(entry.dosage, "500mg");
  assert.equal(entry.route, "oral");
  assert.equal(entry.frequency, "twice daily");
  assert.equal(entry.duration, "5 days");
  assert.equal(entry.instructions, "after food");
});

test("the header carries only a diagnosis and a note, when set", () => {
  const form: MedPrescriptionForm = { diagnosis_id: 7, notes: " take care " };
  const payload = buildPrescriptionPayload([staged()], form, "t");
  assert.equal(payload.diagnosis_id, 7);
  assert.equal(payload.notes, "take care");

  const bare = buildPrescriptionPayload(
    [staged()],
    EMPTY_PRESCRIPTION_FORM,
    "t",
  );
  assert.equal("diagnosis_id" in bare, false);
  assert.equal("notes" in bare, false);
});

test("the payload never carries ownership, money or stock", () => {
  const payload = buildPrescriptionPayload(
    [staged()],
    { diagnosis_id: 7, notes: "n" },
    "t",
  );
  assert.deepEqual(Object.keys(payload).sort(), [
    "diagnosis_id",
    "medicines",
    "notes",
    "request_token",
  ]);

  const forbidden = [
    "patient_id",
    "physician_id",
    "appointment_id",
    "consultation_id",
    "encounter_id",
    "state",
    "sale_price",
    "unit_price",
    "price_subtotal",
    "billing_service_id",
    "charge_line_id",
    "inventory_item_id",
    "batch_id",
    "dispensed_quantity",
    "pharmacy_dispense_ids",
  ];
  const serialized = JSON.stringify(payload);
  for (const key of forbidden) {
    assert.equal(serialized.includes(key), false, `payload leaked ${key}`);
  }
});

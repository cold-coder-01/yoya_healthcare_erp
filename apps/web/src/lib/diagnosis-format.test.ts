/**
 * Diagnosis display vocabulary and payload construction.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins, so these
 * add NO dependency. Imports are relative for the same reason.
 *
 * Run with `npm test`.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. AN EDIT SENDS THE DIFF, NOT THE FORM. The server writes exactly what it
 *      is given, so posting every field would rewrite values the doctor never
 *      touched with whatever this tab happened to load.
 *
 *   2. AN ADD ALWAYS CARRIES A REQUEST TOKEN. Without it a double-clicked Add
 *      files the diagnosis twice -- and for a PRIMARY diagnosis the retry comes
 *      back as "a primary already exists", blaming the doctor for the browser.
 *
 *   3. THE PRIMARY LEADS. A doctor scanning the list wants the headline first.
 *
 *   4. ONLY NOTE AND DIAGNOSIS ARE LIVE. Orders, Results and History must stay
 *      inert until they exist.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type {
  DiagnosisForm,
  DoctorDiagnosis,
} from "@/types/doctor-diagnosis";

import {
  CONSULTATION_SECTIONS,
  EMPTY_FORM,
  buildAddPayload,
  buildUpdatePayload,
  canEdit,
  diagnosisLabel,
  diseaseLabel,
  formFromDiagnosis,
  groupDiagnoses,
  isEmptyUpdate,
  isLiveSection,
  sortDiagnoses,
} from "./diagnosis-format.ts";

function row(overrides: Partial<DoctorDiagnosis> = {}): DoctorDiagnosis {
  return {
    id: 1,
    disease: { id: 10, name: "Migraine without aura", code: "G43.0", category: "Neuro" },
    diagnosis_type: "primary",
    certainty: "provisional",
    severity: "moderate",
    status: "active",
    notes: null,
    diagnosis_date: "2026-08-19",
    editable: true,
    ...overrides,
  };
}

/* ------------------------------------------------------------------ *
 * Section nav
 * ------------------------------------------------------------------ */

test("note and diagnosis are the live sections after slice 2", () => {
  assert.equal(isLiveSection("note"), true);
  assert.equal(isLiveSection("diagnosis"), true);
});

test("orders, results and history remain inert", () => {
  for (const key of ["orders", "results", "history"]) {
    assert.equal(isLiveSection(key), false, key);
  }
});

test("an unknown section key is never live", () => {
  assert.equal(isLiveSection("billing"), false);
  assert.equal(isLiveSection(""), false);
});

test("the section bar keeps its clinical order", () => {
  assert.deepEqual(
    CONSULTATION_SECTIONS.map((section) => section.key),
    ["note", "diagnosis", "orders", "results", "history"],
  );
});

/* ------------------------------------------------------------------ *
 * Vocabulary and ordering
 * ------------------------------------------------------------------ */

test("selection values render as clinical labels", () => {
  assert.equal(diagnosisLabel("primary"), "Primary");
  assert.equal(diagnosisLabel("provisional"), "Provisional");
  assert.equal(diagnosisLabel("final"), "Final");
  assert.equal(diagnosisLabel("differential"), "Differential");
  assert.equal(diagnosisLabel(null), "—");
});

test("the primary diagnosis sorts first", () => {
  const rows = [
    row({ id: 3, diagnosis_type: "differential" }),
    row({ id: 2, diagnosis_type: "secondary" }),
    row({ id: 1, diagnosis_type: "primary" }),
  ];
  assert.deepEqual(
    sortDiagnoses(rows).map((entry) => entry.diagnosis_type),
    ["primary", "secondary", "differential"],
  );
});

test("legacy history rows sort last rather than being dropped", () => {
  const rows = [
    row({ id: 2, diagnosis_type: "history" }),
    row({ id: 1, diagnosis_type: "primary" }),
  ];
  const sorted = sortDiagnoses(rows);
  assert.equal(sorted[0].diagnosis_type, "primary");
  assert.equal(sorted[1].diagnosis_type, "history");
});

test("grouping omits empty groups and keeps reading order", () => {
  const groups = groupDiagnoses([
    row({ id: 2, diagnosis_type: "secondary" }),
    row({ id: 1, diagnosis_type: "primary" }),
  ]);
  assert.deepEqual(groups.map((group) => group.type), ["primary", "secondary"]);
  assert.equal(groups[0].label, "Primary");
});

test("an empty list produces no groups rather than empty ones", () => {
  assert.deepEqual(groupDiagnoses([]), []);
});

test("the disease label carries the code when the catalogue has one", () => {
  assert.equal(diseaseLabel(row()), "Migraine without aura (G43.0)");
  assert.equal(
    diseaseLabel(row({ disease: { id: 1, name: "Fever", code: null, category: null } })),
    "Fever",
  );
  assert.equal(diseaseLabel(row({ disease: null })), "Unknown diagnosis");
});

/* ------------------------------------------------------------------ *
 * Add payload
 * ------------------------------------------------------------------ */

test("the add payload always carries a request token", () => {
  const payload = buildAddPayload(10, EMPTY_FORM, "tok-1");
  assert.equal(payload.request_token, "tok-1");
  assert.equal(payload.disease_id, 10);
});

test("the add payload carries the type and certainty explicitly", () => {
  const form: DiagnosisForm = { ...EMPTY_FORM, diagnosis_type: "primary", certainty: "final" };
  const payload = buildAddPayload(10, form, "tok-2");
  assert.equal(payload.diagnosis_type, "primary");
  assert.equal(payload.certainty, "final");
});

test("empty optional fields are omitted from an add, not sent blank", () => {
  // The server treats "" as an explicit clear, which on a create would store a
  // blank where the model's own default belongs.
  const form: DiagnosisForm = { ...EMPTY_FORM, severity: "", status: "", notes: "   " };
  const payload = buildAddPayload(10, form, "tok-3");
  assert.equal("severity" in payload, false);
  assert.equal("status" in payload, false);
  assert.equal("notes" in payload, false);
});

test("a note that is only whitespace is not sent", () => {
  const payload = buildAddPayload(10, { ...EMPTY_FORM, notes: "\n  \n" }, "tok-4");
  assert.equal("notes" in payload, false);
});

test("the add payload contains no ownership field", () => {
  const payload = buildAddPayload(10, EMPTY_FORM, "tok-5");
  for (const forbidden of [
    "patient_id", "encounter_id", "consultation_id", "appointment_id",
    "physician_id", "active",
  ]) {
    assert.equal(forbidden in payload, false, forbidden);
  }
});

/* ------------------------------------------------------------------ *
 * Update payload
 * ------------------------------------------------------------------ */

test("the update payload carries only what changed", () => {
  const original = formFromDiagnosis(row());
  const next: DiagnosisForm = { ...original, certainty: "final" };

  const payload = buildUpdatePayload(next, original);

  assert.deepEqual(Object.keys(payload), ["certainty"]);
  assert.equal(payload.certainty, "final");
});

test("clearing a field is sent, not dropped", () => {
  const original = formFromDiagnosis(row({ severity: "moderate" }));
  const next: DiagnosisForm = { ...original, severity: "" };

  const payload = buildUpdatePayload(next, original);

  assert.equal(payload.severity, "");
});

test("an unchanged form produces an empty update", () => {
  const original = formFromDiagnosis(row());
  const payload = buildUpdatePayload({ ...original }, original);
  assert.deepEqual(payload, {});
  assert.equal(isEmptyUpdate(payload), true);
});

test("a promotion to primary is expressed as a type change", () => {
  const original = formFromDiagnosis(row({ diagnosis_type: "secondary" }));
  const payload = buildUpdatePayload(
    { ...original, diagnosis_type: "primary" },
    original,
  );
  assert.equal(payload.diagnosis_type, "primary");
});

test("the form round-trips a row, defaulting only what is null", () => {
  const form = formFromDiagnosis(
    row({ certainty: null, severity: null, status: null, notes: null }),
  );
  assert.equal(form.certainty, "provisional");
  assert.equal(form.severity, "");
  assert.equal(form.status, "");
  assert.equal(form.notes, "");
});

/* ------------------------------------------------------------------ *
 * Editability
 * ------------------------------------------------------------------ */

test("a completed consultation makes nothing editable", () => {
  assert.equal(canEdit(false), false);
  assert.equal(canEdit(false, row({ editable: false })), false);
});

test("an open consultation with an editable row is editable", () => {
  assert.equal(canEdit(true, row({ editable: true })), true);
});

test("a row marked read-only is not editable even in an open consultation", () => {
  assert.equal(canEdit(true, row({ editable: false })), false);
});

/* ------------------------------------------------------------------ *
 * Confidentiality
 * ------------------------------------------------------------------ */

test("no diagnosis form field is a financial one", () => {
  // A diagnosis may later justify a claim; that is a billing surface's job
  // working FROM this record, never a reason to put money into it.
  const forbidden = [
    "amount", "balance", "price", "total", "receipt", "payment", "agreement",
    "sponsor", "payer", "membership", "insurance", "coverage", "claim",
  ];
  for (const key of Object.keys(EMPTY_FORM)) {
    for (const word of forbidden) {
      assert.equal(key.includes(word), false, `${key} contains '${word}'`);
    }
  }
});

test("the add payload never mentions a financial concept", () => {
  const serialized = JSON.stringify(
    buildAddPayload(10, { ...EMPTY_FORM, notes: "clinical" }, "tok-6"),
  ).toLowerCase();
  for (const word of ["amount", "price", "payer", "claim", "insurance"]) {
    assert.equal(serialized.includes(word), false, word);
  }
});

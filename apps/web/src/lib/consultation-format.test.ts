/**
 * Consultation editor logic.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins, so these
 * add NO dependency to the project. Imports are relative rather than aliased
 * for the same reason -- they resolve under any runner.
 *
 * Run with `npm test`.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. A SAVE SENDS THE DIFF, NOT THE FORM. The server writes exactly what it
 *      is given, so posting all six fields every time would rewrite paragraphs
 *      the doctor never touched -- with whatever values this tab happened to
 *      load. That is the same overwrite the version token exists to prevent,
 *      arriving through the front door where no version check can see it.
 *
 *   2. THE VERSION IS ALWAYS SENT. A save without it is refused by the API with
 *      `missing_version`, and a save that omitted it silently would be a save
 *      that skipped the concurrency check.
 *
 *   3. MODE IS THE AUTHORITATIVE STATE. `in_consultation` and nothing else.
 */
import assert from "node:assert/strict";
import test from "node:test";

// TYPE-ONLY. TypeScript erases these entirely, so node:test never has to
// resolve the `@/` alias -- the same discipline consultation-format.ts itself
// follows, and what keeps this file runnable with no transform.
import type {
  ConsultationNarrativeField,
  DoctorConsultation,
} from "@/types/doctor-consultation";

import {
  CONSULTATION_STATE,
  NOTE_FIELDS,
  buildSavePayload,
  draftFromConsultation,
  fieldListsAgree,
  hasUnsavedChanges,
  isConsultationMode,
  isEmptySave,
} from "./consultation-format.ts";

// Mirrors CONSULTATION_NARRATIVE_FIELDS in types/doctor-consultation.ts, which
// mirrors NARRATIVE_FIELDS in the Odoo model. Restated as a VALUE here because
// a runtime import from `@/types/...` would not resolve under node:test;
// `fieldListsAgree` is what checks the restatement rather than trusting it.
const CANONICAL_FIELDS: ConsultationNarrativeField[] = [
  "presenting_complaint",
  "history_of_presenting_illness",
  "review_of_systems",
  "examination_findings",
  "assessment",
  "plan",
];

function serverRecord(
  overrides: Partial<DoctorConsultation> = {},
): DoctorConsultation {
  return {
    id: 7,
    name: "CONS00007",
    state: "draft",
    started_at: "2026-08-19T08:00:00",
    completed_at: null,
    version: "2026-08-19T08:00:00.123456",
    editable: true,
    presenting_complaint: "Chest pain",
    history_of_presenting_illness: null,
    review_of_systems: null,
    examination_findings: null,
    assessment: null,
    plan: null,
    ...overrides,
  };
}

/* ------------------------------------------------------------------ *
 * Mode
 * ------------------------------------------------------------------ */

test("consultation mode is the authoritative in_consultation state", () => {
  assert.equal(CONSULTATION_STATE, "in_consultation");
  assert.equal(isConsultationMode("in_consultation"), true);
});

test("no other visit state opens the workspace", () => {
  for (const state of ["draft", "confirmed", "done", "cancelled", "", null, undefined]) {
    assert.equal(isConsultationMode(state), false, String(state));
  }
});

/* ------------------------------------------------------------------ *
 * Field list
 * ------------------------------------------------------------------ */

test("the editor field list matches the model narrative exactly, in order", () => {
  // A field added to the model and the types but forgotten in NOTE_FIELDS
  // would be silently uneditable AND silently unsaved.
  assert.equal(fieldListsAgree(CANONICAL_FIELDS), true);
  assert.deepEqual(
    NOTE_FIELDS.map((field) => field.key),
    CANONICAL_FIELDS,
  );
});

test("every field carries a label and a usable row count", () => {
  for (const field of NOTE_FIELDS) {
    assert.ok(field.label.length > 0, field.key);
    assert.ok(field.rows >= 2, field.key);
  }
});

/* ------------------------------------------------------------------ *
 * Draft
 * ------------------------------------------------------------------ */

test("a null server field becomes an empty string, never null", () => {
  // A null reaching a controlled textarea flips it to uncontrolled mid-edit.
  const draft = draftFromConsultation(serverRecord());
  assert.equal(draft.presenting_complaint, "Chest pain");
  assert.equal(draft.assessment, "");
  for (const key of CANONICAL_FIELDS) {
    assert.equal(typeof draft[key], "string", key);
  }
});

test("a missing consultation produces a complete empty draft", () => {
  const draft = draftFromConsultation(null);
  assert.deepEqual(Object.keys(draft).sort(), [...CANONICAL_FIELDS].sort());
  for (const key of CANONICAL_FIELDS) {
    assert.equal(draft[key], "");
  }
});

test("line breaks survive the draft round trip", () => {
  const text = "Line one\nLine two\n\nParagraph two";
  const draft = draftFromConsultation(serverRecord({ plan: text }));
  assert.equal(draft.plan, text);
});

/* ------------------------------------------------------------------ *
 * Dirty tracking
 * ------------------------------------------------------------------ */

test("an untouched draft is not dirty", () => {
  const baseline = draftFromConsultation(serverRecord());
  assert.equal(hasUnsavedChanges({ ...baseline }, baseline), false);
});

test("any edited field makes the draft dirty", () => {
  const baseline = draftFromConsultation(serverRecord());
  for (const key of CANONICAL_FIELDS) {
    const draft = { ...baseline, [key]: "changed" };
    assert.equal(hasUnsavedChanges(draft, baseline), true, key);
  }
});

test("clearing a field is an edit, not a no-op", () => {
  const baseline = draftFromConsultation(serverRecord());
  const draft = { ...baseline, presenting_complaint: "" };
  assert.equal(hasUnsavedChanges(draft, baseline), true);
});

/* ------------------------------------------------------------------ *
 * Save payload
 * ------------------------------------------------------------------ */

test("the save payload always carries the version", () => {
  const baseline = draftFromConsultation(serverRecord());
  const draft = { ...baseline, assessment: "Stable angina" };
  const payload = buildSavePayload("v1", draft, baseline);
  assert.equal(payload.version, "v1");
});

test("the save payload carries ONLY the fields that changed", () => {
  const baseline = draftFromConsultation(serverRecord());
  const draft = { ...baseline, assessment: "Stable angina", plan: "ECG" };

  const payload = buildSavePayload("v1", draft, baseline);

  // presenting_complaint was loaded but never touched: sending it would
  // rewrite the nurse-seeded text with whatever this tab happened to hold.
  assert.deepEqual(Object.keys(payload).sort(), ["assessment", "plan", "version"]);
  assert.equal(payload.assessment, "Stable angina");
  assert.equal(payload.plan, "ECG");
  assert.equal("presenting_complaint" in payload, false);
});

test("clearing a paragraph is sent, not dropped", () => {
  // Deleting text written in error is a legitimate edit and is distinct from
  // not touching the field, which is why the diff compares rather than filters
  // on truthiness.
  const baseline = draftFromConsultation(serverRecord());
  const draft = { ...baseline, presenting_complaint: "" };

  const payload = buildSavePayload("v1", draft, baseline);

  assert.equal(payload.presenting_complaint, "");
  assert.deepEqual(Object.keys(payload).sort(), [
    "presenting_complaint",
    "version",
  ]);
});

test("an unchanged draft produces a version-only payload", () => {
  const baseline = draftFromConsultation(serverRecord());
  const payload = buildSavePayload("v1", { ...baseline }, baseline);
  assert.deepEqual(Object.keys(payload), ["version"]);
  assert.equal(isEmptySave(payload), true);
});

test("a payload with any field is not an empty save", () => {
  const baseline = draftFromConsultation(serverRecord());
  const draft = { ...baseline, plan: "Review in one week" };
  assert.equal(isEmptySave(buildSavePayload("v1", draft, baseline)), false);
});

test("the version is echoed opaquely, never reformatted", () => {
  // The server compares it as a STRING, byte for byte.
  const baseline = draftFromConsultation(serverRecord());
  const draft = { ...baseline, plan: "x" };
  const token = "2026-08-19T08:00:00.123456";
  assert.equal(buildSavePayload(token, draft, baseline).version, token);
});

/* ------------------------------------------------------------------ *
 * Confidentiality
 * ------------------------------------------------------------------ */

test("no editor field is a financial or commercial one", () => {
  // The consultation surface has no money dimension. This pins that a future
  // field cannot be added here without the omission being deliberate.
  const forbidden = [
    "amount", "balance", "price", "total", "receipt", "payment",
    "agreement", "sponsor", "payer", "membership", "insurance", "coverage",
  ];
  for (const field of NOTE_FIELDS) {
    for (const word of forbidden) {
      assert.equal(
        field.key.includes(word),
        false,
        `${field.key} contains '${word}'`,
      );
    }
  }
});

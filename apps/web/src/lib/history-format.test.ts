/**
 * Longitudinal history presentation decisions.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins, so these
 * add NO dependency to the project -- the same discipline every other test file
 * here follows. Imports are relative rather than aliased so they resolve under
 * any runner.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. AN UNRECORDED VITAL IS NEVER PRINTED AS ZERO. The columns behind these
 *      values are plain Floats with no null sentinel, so Slice 9A converts the
 *      unrecorded-zero to null on the way out. If this layer coalesced a null
 *      back to 0 it would put a physiologically impossible reading on a chart
 *      -- an SpO2 of 0% is not a missing measurement, it is a dead patient.
 *
 *   2. A ZERO COUNT IS NOT AN INDICATOR. A row reading "Dx 0 · Lab 0 · Rad 0"
 *      is a dashboard, not a chart line, and it costs a doctor the scan it was
 *      supposed to save.
 *
 *   3. EVERY REFUSAL SAYS THE SAME THING. Slice 9A answers a flat 404 for an
 *      episode that never existed, one belonging to another patient, and one
 *      the caller may no longer reach. Three different sentences here would
 *      re-open the disclosure that flat 404 exists to close.
 *
 *   4. HISTORICAL IMAGERY RESOLVES THROUGH ITS OWN ROUTE, naming BOTH
 *      appointments. The current-visit results route deliberately refuses a
 *      historical episode.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTIVE_CARE_VISIT_STATES,
  EPISODE_UNAVAILABLE_TEXT,
  HISTORY_UNAVAILABLE_TEXT,
  NOTE_FIELDS,
  NOTE_LABELS,
  NO_HISTORY_TEXT,
  abnormalNote,
  abnormalNoteClass,
  contentChips,
  hasActiveCareRelationship,
  hasNoteContent,
  hasTriageContent,
  historyImageContentPath,
  historyProgressText,
  isEmptyEpisode,
  presentSections,
  primaryDiagnosisText,
  visitProvenance,
  vitalsLine,
} from "./history-format.ts";

/* ------------------------------------------------------------------ *
 * Vitals: the null rule
 * ------------------------------------------------------------------ */
const NO_VITALS = {
  chief_complaint: null,
  triage_priority: null,
  triage_priority_label: null,
  recorded_at: null,
  systolic_bp: null,
  diastolic_bp: null,
  heart_rate: null,
  temperature: null,
  respiratory_rate: null,
  spo2: null,
  bmi: null,
  bmi_state: null,
  weight: null,
  height: null,
  pain_level: null,
};

test("a recorded vital snapshot reads as one compact line", () => {
  const line = vitalsLine({
    ...NO_VITALS,
    systolic_bp: 120,
    diastolic_bp: 80,
    heart_rate: 83,
    temperature: 37.2,
    spo2: 98,
  });
  assert.equal(line, "BP 120/80 · Pulse 83 · Temp 37.2 · SpO2 98%");
});

test("an UNRECORDED vital is omitted, never printed as zero", () => {
  /*
    THE ASSERTION THIS FILE EXISTS FOR. Only the pulse was taken; every other
    reading is absent. A "0" anywhere in this string would be a fabricated
    measurement on a clinical chart.
  */
  const line = vitalsLine({ ...NO_VITALS, heart_rate: 83 });
  assert.equal(line, "Pulse 83");
  assert.ok(!line?.includes("0/"), "a blood pressure was invented");
  assert.ok(!line?.includes("SpO2"), "an oxygen saturation was invented");
  assert.ok(!line?.includes("Temp"), "a temperature was invented");
});

test("a blood pressure needs BOTH halves or it is not a reading", () => {
  // "120/" is not a blood pressure, and neither is "/80".
  assert.equal(vitalsLine({ ...NO_VITALS, systolic_bp: 120 }), null);
  assert.equal(vitalsLine({ ...NO_VITALS, diastolic_bp: 80 }), null);
  assert.equal(
    vitalsLine({ ...NO_VITALS, systolic_bp: 120, diastolic_bp: 80 }),
    "BP 120/80",
  );
});

test("a triage record with nothing recorded produces no line at all", () => {
  assert.equal(vitalsLine(NO_VITALS), null);
  assert.equal(vitalsLine(null), null);
  assert.equal(hasTriageContent(NO_VITALS), false);
  assert.equal(hasTriageContent(null), false);
});

test("a triage record with only a complaint still has content", () => {
  assert.equal(
    hasTriageContent({ ...NO_VITALS, chief_complaint: "Headache" }),
    true,
  );
});

/* ------------------------------------------------------------------ *
 * Content indicators
 * ------------------------------------------------------------------ */
const NO_COUNTS = {
  diagnoses: 0,
  laboratory: 0,
  radiology: 0,
  medications: 0,
  images: 0,
  abnormal_results: 0,
  critical_results: 0,
};

test("only non-zero counts earn a chip", () => {
  const chips = contentChips({ ...NO_COUNTS, diagnoses: 2, laboratory: 3 });
  assert.deepEqual(
    chips.map((chip) => chip.label),
    ["Dx 2", "Lab 3"],
  );
});

test("an episode with no counted content shows no chips at all", () => {
  assert.deepEqual(contentChips(NO_COUNTS), []);
  assert.deepEqual(contentChips(null), []);
});

test("chips keep their clinical scan order", () => {
  const chips = contentChips({
    ...NO_COUNTS,
    diagnoses: 1,
    laboratory: 1,
    radiology: 1,
    medications: 1,
    images: 1,
  });
  assert.deepEqual(
    chips.map((chip) => chip.label),
    ["Dx 1", "Lab 1", "Rad 1", "Rx 1", "Img 1"],
  );
});

test("abnormality is a COUNT and carries its word, never colour alone", () => {
  const warn = abnormalNote({ ...NO_COUNTS, abnormal_results: 1 });
  assert.equal(warn?.text, "1 abnormal");
  assert.equal(warn?.tone, "warn");
  // The word survives a printout and a reader who cannot separate the tones.
  assert.ok(/abnormal/.test(warn?.text ?? ""));
});

test("critical outranks abnormal and is the only red", () => {
  const note = abnormalNote({
    ...NO_COUNTS,
    abnormal_results: 3,
    critical_results: 1,
  });
  assert.equal(note?.text, "1 critical");
  assert.equal(note?.tone, "critical");
  assert.ok(abnormalNoteClass("critical").includes("red"));
  assert.ok(!abnormalNoteClass("warn").includes("red"));
});

test("a normal episode carries no abnormality note", () => {
  assert.equal(abnormalNote(NO_COUNTS), null);
  assert.equal(abnormalNote(null), null);
});

test("the row never carries a clinical VALUE, only how many there are", () => {
  // A count tells a doctor the visit is worth opening; the value is inside it.
  const note = abnormalNote({ ...NO_COUNTS, abnormal_results: 2 });
  assert.ok(!/[0-9]+\.[0-9]/.test(note?.text ?? ""), "a measured value leaked");
});

/* ------------------------------------------------------------------ *
 * Provenance and the headline diagnosis
 * ------------------------------------------------------------------ */
test("a CROSS-PROVIDER episode names the clinician who actually saw them", () => {
  /*
    The whole clinical point of the slice: today's doctor is reading a visit
    another doctor conducted, and the row must say whose work it was.
  */
  assert.equal(
    visitProvenance({
      encounter_type_label: "Outpatient",
      doctor: "Dr Michael Tesfaye",
      department: "General Medicine",
    }),
    "Outpatient · Dr Michael Tesfaye · General Medicine",
  );
});

test("a legacy episode with no department drops the part rather than the line", () => {
  assert.equal(
    visitProvenance({
      encounter_type_label: "Outpatient",
      doctor: "Dr Michael Tesfaye",
      department: null,
    }),
    "Outpatient · Dr Michael Tesfaye",
  );
  assert.equal(
    visitProvenance({ encounter_type_label: null, doctor: null, department: null }),
    null,
  );
});

test("the headline diagnosis parenthesises a code only when there is one", () => {
  assert.equal(
    primaryDiagnosisText({
      primary_diagnosis: {
        name: "Migraine without aura",
        code: "G43.0",
        certainty: "final",
        severity: "moderate",
      },
    }),
    "Migraine without aura (G43.0)",
  );
  assert.equal(
    primaryDiagnosisText({
      primary_diagnosis: {
        name: "Headache",
        code: null,
        certainty: null,
        severity: null,
      },
    }),
    "Headache",
  );
  assert.equal(primaryDiagnosisText({ primary_diagnosis: null }), null);
});

/* ------------------------------------------------------------------ *
 * Sections and empty states
 * ------------------------------------------------------------------ */
const EMPTY_DETAIL = {
  visit: {
    appointment_id: 1,
    encounter_id: 2,
    encounter_code: "ENC01221",
    consultation_code: null,
    encounter_type: "outpatient",
    encounter_type_label: "Outpatient",
    date: "2026-07-31",
    opened_at: "2026-07-31 07:33:47",
    completed_at: null,
    doctor: "Dr Michael Tesfaye",
    department: null,
    status: "completed",
    status_label: "Completed",
  },
  triage: null,
  note: null,
  diagnoses: [],
  medications: [],
  laboratory: [],
  radiology: [],
};

const NOTE = {
  id: 1,
  name: "CONS00001",
  state: "completed" as const,
  started_at: null,
  completed_at: null,
  presenting_complaint: null,
  history_of_presenting_illness: null,
  review_of_systems: null,
  examination_findings: null,
  assessment: null,
  plan: null,
};

test("a consultation with every narrative blank has no note content", () => {
  // The model has no completeness constraint, so this row is reachable and a
  // viewer that drew six empty headings for it would read as broken.
  assert.equal(hasNoteContent(NOTE), false);
  assert.equal(hasNoteContent({ ...NOTE, assessment: "   " }), false);
  assert.equal(hasNoteContent(null), false);
});

test("a single recorded narrative field is enough to draw the note", () => {
  assert.equal(hasNoteContent({ ...NOTE, assessment: "Migraine" }), true);
});

test("the note reads in clinical order and every field is labelled", () => {
  assert.deepEqual(NOTE_FIELDS, [
    "presenting_complaint",
    "history_of_presenting_illness",
    "review_of_systems",
    "examination_findings",
    "assessment",
    "plan",
  ]);
  for (const field of NOTE_FIELDS) {
    assert.ok(NOTE_LABELS[field], `${field} has no label`);
  }
});

test("only sections with something to say are drawn", () => {
  assert.deepEqual(presentSections(EMPTY_DETAIL), ["visit"]);
  assert.ok(isEmptyEpisode(EMPTY_DETAIL));

  const withContent = {
    ...EMPTY_DETAIL,
    note: { ...NOTE, assessment: "Migraine" },
    diagnoses: [{ id: 1 }] as never,
    laboratory: [{ request_id: 1 }] as never,
  };
  assert.deepEqual(presentSections(withContent), [
    "visit",
    "note",
    "diagnoses",
    "laboratory",
  ]);
  assert.ok(!isEmptyEpisode(withContent));
});

test("an empty history and a refused history say DIFFERENT things", () => {
  // "No history" is a clinical fact about the patient. "Unavailable" is a
  // statement about this request. Collapsing them would tell a doctor a
  // patient has no past when the truth is that access lapsed.
  assert.notEqual(NO_HISTORY_TEXT, HISTORY_UNAVAILABLE_TEXT);
  assert.match(NO_HISTORY_TEXT, /no prior clinical history/i);
});

test("every refusal reason produces the SAME sentence", () => {
  /*
    Slice 9A answers a flat 404 for an episode that never existed, one
    belonging to another patient, and one the caller may no longer reach. The
    UI must not undo that by explaining which it was.
  */
  assert.match(HISTORY_UNAVAILABLE_TEXT, /not available/i);
  assert.ok(!/permission|forbidden|another patient|expired|lapsed/i.test(
    HISTORY_UNAVAILABLE_TEXT,
  ));
  assert.ok(!/permission|forbidden|another patient/i.test(
    EPISODE_UNAVAILABLE_TEXT,
  ));
});

/* ------------------------------------------------------------------ *
 * Pagination
 * ------------------------------------------------------------------ */
test("the progress line appears only while there is more to load", () => {
  assert.equal(historyProgressText(2, 7), "Showing 2 of 7 visits");
  assert.equal(historyProgressText(7, 7), null);
  assert.equal(historyProgressText(0, 0), null);
});

/* ------------------------------------------------------------------ *
 * Historical imagery
 * ------------------------------------------------------------------ */
test("a historical image path names BOTH appointments", () => {
  /*
    The current visit proves the care relationship; the historical one names
    the episode. Odoo checks the pairing rather than the image id alone.
  */
  assert.equal(
    historyImageContentPath(1195, 1188, 7),
    "/api/doctor/visits/1195/history/1188/images/7",
  );
});

test("a historical image path is NEVER the current-visit results path", () => {
  // The results route deliberately refuses a historical appointment; routing
  // history imagery through it would be the leak that route exists to prevent.
  const path = historyImageContentPath(1195, 1188, 7);
  assert.ok(!path.includes("/results/"), "history imagery used the Results route");
});

test("a downloadable historical file carries the one permitted disposition", () => {
  assert.equal(
    historyImageContentPath(1195, 1188, 7, "attachment"),
    "/api/doctor/visits/1195/history/1188/images/7?disposition=attachment",
  );
});

test("no image path can carry an Odoo origin or a token", () => {
  const path = historyImageContentPath(1195, 1188, 7, "attachment");
  for (const banned of [
    "http://",
    "https://",
    "/web/content",
    "/web/image",
    "access_token",
    "8171",
  ]) {
    assert.ok(!path.includes(banned), `'${banned}' reached an image URL`);
  }
  assert.ok(path.startsWith("/api/doctor/visits/"));
});

/* ------------------------------------------------------------------ *
 * Reachability before consultation (Slice 9B UAT defect)
 * ------------------------------------------------------------------ */
test("a CONFIRMED visit holds an active care relationship", () => {
  /*
    THE DEFECT THIS FIXES. Slice 9A opens longitudinal reading on `confirmed`,
    before triage and before clearance, because reading a record is not an act
    that needs either. The desk was hiding History until the consultation
    started, which made an authorized surface unreachable.
  */
  assert.equal(hasActiveCareRelationship("confirmed"), true);
});

test("an IN_CONSULTATION visit is unchanged", () => {
  assert.equal(hasActiveCareRelationship("in_consultation"), true);
});

test("a closed or unconfirmed visit holds NO active care relationship", () => {
  // `done` is absent for the same reason the server excludes it: access LAPSES
  // when the episode closes. `draft` may never become a visit at all.
  for (const state of ["done", "cancelled", "draft", "", null, undefined]) {
    assert.equal(
      hasActiveCareRelationship(state),
      false,
      `${String(state)} should not grant an active care relationship`,
    );
  }
});

test("the mirrored states are EXACTLY the server's two", () => {
  /*
    ACTIVE_CARE_APPOINTMENT_STATES in yoya_clinical_bridge/models/res_users.py
    is the authority; this list is a copy the browser can read. Pinned by value
    so a change on either side is a visible edit rather than a silent drift.

    Drift is survivable in one direction only, and that is the point: a stale
    copy can at worst offer a tab that opens onto "not available". It can never
    show data, because every request is refused by the server's record rules.
  */
  assert.deepEqual(ACTIVE_CARE_VISIT_STATES, ["confirmed", "in_consultation"]);
});

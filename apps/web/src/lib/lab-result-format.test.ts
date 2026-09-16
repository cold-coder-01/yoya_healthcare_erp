/**
 * Laboratory Desk result entry: the pure decisions behind the result sheet.
 *
 * Written against `node:test` and `node:assert`. Run with `npm test`.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. ENTRY IS OFFERED FOR EXACTLY ONE BENCH STATUS. `in_progress`, with no
 *      result or a draft to resume -- never sample_collected, never ready,
 *      never once a result has moved past draft, never when several results
 *      make the operational one ambiguous.
 *
 *   2. THE BROWSER DOES NOT DECIDE COMPLETENESS. Nothing here trims, requires
 *      a unit, or refuses a blank value: "0" and "   " both travel exactly as
 *      typed, and action_mark_entered() decides.
 *
 *   3. ONLY ALLOW-LISTED FIELDS LEAVE THE BROWSER. The payload carries no
 *      test, request line, specimen, sequence or state.
 *
 *   4. NOTHING IS INVENTED. The abnormal flag defaults to the model's own
 *      "normal", is described as a default, and no "unassessed" value exists.
 *
 *   5. EVERY ROUTE IS THE BFF'S. No Odoo path, host or port.
 */
import assert from "node:assert/strict";
import test from "node:test";

// TYPE-ONLY, for the reason lab-desk-format.test.ts gives.
import type { LabResult } from "@/types/lab-desk";

import {
  DEFAULT_ABNORMAL_FLAG,
  abnormalFlagHint,
  canEnterResults,
  canViewResult,
  draftFromResult,
  draftStatusText,
  enterResultPath,
  openResultPath,
  patchDraftLine,
  resultDraftChanged,
  resultErrorMessage,
  resultModalSubtitle,
  resultPayload,
  sampleTypeLabel,
  saveResultPath,
  shouldReconcileAfterResult,
} from "./lab-result-format.ts";

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

function result(overrides: Partial<LabResult> = {}): LabResult {
  return {
    id: 90,
    name: "LABRES0090",
    state: "draft",
    state_label: "Draft",
    result_date: "2026-09-16",
    interpretation: null,
    remarks: null,
    lines: [
      {
        id: 501,
        request_line_id: 355,
        test: { id: 1, name: "Complete Blood Count", code: "CBC" },
        sample_type: "blood",
        result_value: null,
        unit: null,
        reference_range: null,
        abnormal_flag: "normal",
        notes: null,
        sequence: 10,
      },
    ],
    abnormal_flag_options: [
      { value: "normal", label: "Normal" },
      { value: "low", label: "Low" },
      { value: "high", label: "High" },
      { value: "critical", label: "Critical" },
      { value: "abnormal", label: "Abnormal" },
    ],
    ...overrides,
  };
}

const ALL_STATUSES = [
  "draft",
  "awaiting_clearance",
  "ready_for_collection",
  "sample_collected",
  "in_progress",
  "completed",
  "cancelled",
];

/* ------------------------------------------------------------------ *
 * 1. Where Enter results is offered
 * ------------------------------------------------------------------ */

test("Enter results is offered for in_progress with no result", () => {
  assert.equal(
    canEnterResults({ status: "in_progress", result: null, result_conflict: false }),
    true,
  );
});

test("Enter results resumes an in_progress draft", () => {
  assert.equal(
    canEnterResults({ status: "in_progress", result: { state: "draft" } }),
    true,
  );
});

test("Enter results is offered for no status other than in_progress", () => {
  for (const status of ALL_STATUSES) {
    if (status === "in_progress") continue;
    assert.equal(
      canEnterResults({ status, result: null, result_conflict: false }),
      false,
      `${status} must not offer Enter results`,
    );
  }
});

test("Enter results is not offered on sample_collected, even though the model would accept it", () => {
  // DESK POLICY: the model's RESULT_ELIGIBLE_REQUEST_STATES includes
  // sample_collected; the bench deliberately waits for processing.
  assert.equal(canEnterResults({ status: "sample_collected", result: null }), false);
  assert.equal(
    canEnterResults({ status: "sample_collected", result: { state: "draft" } }),
    false,
  );
});

test("Enter results disappears once the result has left draft", () => {
  for (const state of ["entered", "validated", "released", "cancelled"]) {
    assert.equal(
      canEnterResults({ status: "in_progress", result: { state } }),
      false,
      `a ${state} result must not offer entry`,
    );
  }
});

test("Enter results is withheld when several results make the choice ambiguous", () => {
  assert.equal(
    canEnterResults({ status: "in_progress", result: null, result_conflict: true }),
    false,
  );
});

test("Enter results is not offered for an unknown or missing detail", () => {
  assert.equal(canEnterResults(null), false);
  assert.equal(canEnterResults(undefined), false);
  assert.equal(canEnterResults({ status: null, result: null }), false);
  assert.equal(canEnterResults({ status: "processing", result: null }), false);
});

test("Enter results and the bench transitions never share a status", () => {
  // Collect is ready_for_collection only; Start is sample_collected only.
  for (const status of ["ready_for_collection", "sample_collected"]) {
    assert.equal(canEnterResults({ status, result: null }), false);
  }
});

test("View result is offered for entered, validated and released only", () => {
  for (const state of ["entered", "validated", "released"]) {
    assert.equal(canViewResult({ status: "in_progress", result: { state } }), true);
  }
  for (const state of ["draft", "cancelled"]) {
    assert.equal(canViewResult({ status: "in_progress", result: { state } }), false);
  }
  assert.equal(canViewResult({ status: "in_progress", result: null }), false);
  assert.equal(canViewResult(null), false);
});

test("a single status never offers both Enter results and View result", () => {
  for (const state of ["draft", "entered", "validated", "released", "cancelled"]) {
    const detail = { status: "in_progress", result: { state } };
    assert.ok(!(canEnterResults(detail) && canViewResult(detail)), state);
  }
});

/* ------------------------------------------------------------------ *
 * 2. Routes
 * ------------------------------------------------------------------ */

test("every result route is the BFF's, never Odoo's", () => {
  const paths = [openResultPath(215), saveResultPath(90), enterResultPath(90)];
  assert.deepEqual(paths, [
    "/api/laboratory/requests/215/result",
    "/api/laboratory/results/90/save",
    "/api/laboratory/results/90/enter",
  ]);
  for (const path of paths) {
    assert.ok(path.startsWith("/api/laboratory/"));
    assert.ok(!path.includes("yoya-emr"));
    assert.ok(!path.includes("http"));
    assert.ok(!path.includes("8171"));
  }
});

test("there is no validate, release, cancel or reset route helper", async () => {
  const exported = await import("./lab-result-format.ts");
  for (const name of Object.keys(exported)) {
    assert.ok(
      !/validate|release|cancel|reset/i.test(name),
      `${name} suggests a workflow Slice 3 does not ship`,
    );
  }
});

/* ------------------------------------------------------------------ *
 * 3. The draft
 * ------------------------------------------------------------------ */

test("a server result becomes an editable draft with empty strings for nulls", () => {
  const draft = draftFromResult(result());
  assert.deepEqual(draft, {
    interpretation: "",
    remarks: "",
    lines: [
      {
        id: 501,
        result_value: "",
        unit: "",
        reference_range: "",
        abnormal_flag: "normal",
        notes: "",
      },
    ],
  });
});

test("persisted values are copied exactly, whitespace included", () => {
  const source = result();
  source.lines[0].result_value = "  WBC 11.8  ";
  source.interpretation = "Mild leukocytosis.";
  const draft = draftFromResult(source);
  assert.equal(draft.lines[0].result_value, "  WBC 11.8  ");
  assert.equal(draft.interpretation, "Mild leukocytosis.");
});

test("a missing flag reads as the model's own default, not an invented value", () => {
  const source = result();
  source.lines[0].abnormal_flag = null;
  assert.equal(draftFromResult(source).lines[0].abnormal_flag, "normal");
  assert.equal(DEFAULT_ABNORMAL_FLAG, "normal");
});

test("an untouched draft is unchanged; any edited field is a change", () => {
  const baseline = draftFromResult(result());
  assert.equal(resultDraftChanged(baseline, draftFromResult(result())), false);

  for (const values of [
    { result_value: "7.4" },
    { unit: "g/dL" },
    { reference_range: "12-16" },
    { abnormal_flag: "high" },
    { notes: "haemolysed" },
  ]) {
    assert.equal(
      resultDraftChanged(baseline, patchDraftLine(baseline, 501, values)),
      true,
      JSON.stringify(values),
    );
  }
  assert.equal(
    resultDraftChanged(baseline, { ...baseline, interpretation: "x" }),
    true,
  );
  assert.equal(resultDraftChanged(baseline, { ...baseline, remarks: "x" }), true);
});

test("patching one line leaves the others and the original untouched", () => {
  const source = result();
  source.lines.push({ ...source.lines[0], id: 502, request_line_id: 356 });
  const baseline = draftFromResult(source);
  const patched = patchDraftLine(baseline, 502, { result_value: "0" });
  assert.equal(patched.lines[0].result_value, "");
  assert.equal(patched.lines[1].result_value, "0");
  assert.equal(baseline.lines[1].result_value, "", "the baseline is not mutated");
});

test("a saved draft becomes the new baseline, so the screen is no longer 'changed'", () => {
  const typed = patchDraftLine(draftFromResult(result()), 501, {
    result_value: "WBC 11.8, Hgb 14.2, Plt 265",
  });
  const saved = result();
  saved.lines[0].result_value = "WBC 11.8, Hgb 14.2, Plt 265";
  assert.equal(resultDraftChanged(draftFromResult(saved), typed), false);
});

/* ------------------------------------------------------------------ *
 * 4. The payload
 * ------------------------------------------------------------------ */

test("the payload carries only the allow-listed keys", () => {
  const payload = resultPayload(draftFromResult(result()));
  assert.deepEqual(Object.keys(payload).sort(), ["interpretation", "lines", "remarks"]);
  assert.deepEqual(Object.keys(payload.lines[0]).sort(), [
    "abnormal_flag",
    "id",
    "notes",
    "reference_range",
    "result_value",
    "unit",
  ]);
  const blob = JSON.stringify(payload);
  for (const banned of [
    "test", "request_line_id", "sample_type", "sequence", "state",
    "patient", "physician", "technician", "request_id",
  ]) {
    assert.ok(!blob.includes(banned), `${banned} must never be sent`);
  }
});

test("empty inputs are sent as null; typed text travels exactly as typed", () => {
  const draft = patchDraftLine(draftFromResult(result()), 501, {
    result_value: "   ",
    unit: "",
  });
  const payload = resultPayload(draft);
  // Whitespace is NOT trimmed: whether it counts is action_mark_entered's call.
  assert.equal(payload.lines[0].result_value, "   ");
  assert.equal(payload.lines[0].unit, null);
  assert.equal(payload.interpretation, null);
});

test('"0" is sent as a value, never dropped as falsy', () => {
  const draft = patchDraftLine(draftFromResult(result()), 501, { result_value: "0" });
  assert.equal(resultPayload(draft).lines[0].result_value, "0");
});

test("the Selam UAT values survive the round trip unchanged", () => {
  const draft = {
    interpretation:
      "Mild leukocytosis; hemoglobin and platelet count within supplied reference ranges.",
    remarks: "Routine CBC result.",
    lines: [
      {
        id: 501,
        result_value: "WBC 11.8, Hgb 14.2, Plt 265",
        unit: "mixed",
        reference_range: "WBC 4.0-10.0, Hgb 13-17, Plt 150-400",
        abnormal_flag: "normal",
        notes: "CBC result entered during Slice 3 UAT.",
      },
    ],
  };
  const payload = resultPayload(draft);
  assert.deepEqual(payload.lines[0], draft.lines[0]);
  assert.equal(payload.interpretation, draft.interpretation);
  assert.equal(payload.remarks, draft.remarks);
});

/* ------------------------------------------------------------------ *
 * 5. Wording
 * ------------------------------------------------------------------ */

test("a Normal flag is described as the default, and nothing else is", () => {
  const hint = abnormalFlagHint("normal");
  assert.ok(hint && hint.toLowerCase().includes("default"));
  for (const value of ["low", "high", "critical", "abnormal", null, undefined]) {
    assert.equal(abnormalFlagHint(value), null);
  }
});

test("the hint never claims the technician chose Normal", () => {
  const hint = abnormalFlagHint("normal") ?? "";
  for (const claim of ["confirmed", "assessed", "verified", "you selected", "chosen"]) {
    assert.ok(!hint.toLowerCase().includes(claim), claim);
  }
});

test("the specimen reads as words and a dash when absent", () => {
  assert.equal(sampleTypeLabel("blood"), "Blood");
  assert.equal(sampleTypeLabel(null), "—");
});

test("the subtitle is patient, chart number, request and test count", () => {
  assert.equal(
    resultModalSubtitle({
      request_code: "LABREQ0215",
      test_count: 1,
      patient: { name: "Selam Tesfaye", mrn: "HMS11834" },
    }),
    "Selam Tesfaye · HMS11834 · LABREQ0215 · 1 test",
  );
  assert.equal(
    resultModalSubtitle({
      request_code: "LABREQ0002",
      test_count: 4,
      patient: { name: "Abebe", mrn: null },
    }),
    "Abebe · LABREQ0002 · 4 tests",
  );
});

test("the status line never claims a save that did not happen", () => {
  const base = { readOnly: false, busy: null, changed: false, savedSinceOpen: false };
  assert.ok(!draftStatusText(base).includes("saved"));
  assert.ok(draftStatusText({ ...base, changed: true }).startsWith("Unsaved changes"));
  assert.ok(draftStatusText({ ...base, savedSinceOpen: true }).startsWith("Draft saved"));
  // Typing after a save is unsaved again, not "saved".
  assert.ok(
    draftStatusText({ ...base, savedSinceOpen: true, changed: true }).startsWith(
      "Unsaved changes",
    ),
  );
  assert.equal(draftStatusText({ ...base, busy: "save" }), "Saving draft…");
  assert.equal(draftStatusText({ ...base, busy: "enter" }), "Marking entered…");
  assert.equal(draftStatusText({ ...base, readOnly: true }), "Read only");
});

test("a refusal shows the server's own sentence, with a safe fallback", () => {
  const server =
    "Laboratory result LABRES0090 is inconsistent with request LABREQ0215:\n- no result value entered for: Complete Blood Count";
  assert.equal(resultErrorMessage(server, "fallback"), server);
  assert.equal(resultErrorMessage("", "fallback"), "fallback");
  assert.equal(resultErrorMessage("   ", "fallback"), "fallback");
  assert.equal(resultErrorMessage(null, "fallback"), "fallback");
});

test("no result wording invents financial vocabulary", () => {
  const texts = [
    abnormalFlagHint("normal") ?? "",
    draftStatusText({ readOnly: false, busy: null, changed: true, savedSinceOpen: false }),
    draftStatusText({ readOnly: false, busy: "enter", changed: false, savedSinceOpen: true }),
    resultErrorMessage(null, "The result could not be marked entered."),
  ];
  for (const text of texts) {
    for (const banned of [
      "ETB", "amount", "balance", "outstanding", "payer", "invoice",
      "receipt", "birr", "charge", "cleared", "paid",
    ]) {
      assert.ok(!text.toLowerCase().includes(banned.toLowerCase()), `${banned} in "${text}"`);
    }
  }
});

test("a stale screen reconciles after a result conflict, not after a transport failure", () => {
  for (const code of [
    "lab_result_entry_not_available",
    "lab_result_ambiguous",
    "lab_result_already_final",
    "lab_result_cancelled",
    "lab_result_not_editable",
    "lab_result_not_found",
  ]) {
    assert.ok(shouldReconcileAfterResult(code), code);
  }
  for (const code of [
    "lab_result_incomplete",
    "lab_result_save_failed",
    "lab_result_enter_failed",
    "lab_result_enter_response_failed",
    null,
    undefined,
  ]) {
    assert.ok(!shouldReconcileAfterResult(code), String(code));
  }
});

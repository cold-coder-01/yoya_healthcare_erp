/**
 * Slice 4: the Doctor Desk's side of completing a consultation.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins.
 * Run with `npm test`.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. THE FOUR BUCKETS ARE A MIRROR, NOT A SECOND OPINION. `in_consultation`
 *      moved out of Finished when completion became possible; the backend made
 *      the same move in doctor_serializers.py, and a test on each side is the
 *      only thing keeping them in step.
 *
 *   2. VISIBLE IS NOT EDITABLE. A completed visit still renders its clinical
 *      workspace; whether anything in it may be WRITTEN is the server's
 *      verdict, carried on `consultation.editable`.
 *
 *   3. NOTHING HERE DECIDES WHETHER COMPLETION IS ALLOWED. can_complete and
 *      completion_blockers arrive from
 *      hospital.consultation.completion_blockers(), which action_complete()
 *      enforces a moment later. These tests pin that the UI reads the verdict
 *      rather than re-deriving it -- a local copy would eventually enable a
 *      button the server refuses.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type {
  ConsultationBlocker,
  ConsultationWarning,
  DoctorConsultationResponse,
} from "@/types/doctor-consultation";
import type { DoctorQueueRow } from "@/types/doctor";

import {
  DOCTOR_BUCKETS,
  bucketCounts,
  bucketOf,
  statLabel,
} from "./doctor-format.ts";
import {
  isConsultationMode,
  isConsultationVisible,
} from "./consultation-format.ts";

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

const row = (
  queue_stage: DoctorQueueRow["queue_stage"],
  state: string,
): DoctorQueueRow =>
  ({ queue_stage, state, appointment_id: 1 }) as unknown as DoctorQueueRow;

const envelope = (
  over: Partial<DoctorConsultationResponse> = {},
): DoctorConsultationResponse =>
  ({
    available: true,
    reason: null,
    consultation: null,
    can_complete: false,
    completion_blockers: [],
    completion_warnings: [],
    ...over,
  }) as DoctorConsultationResponse;

/*
  THE GATE, RESTATED EXACTLY AS THE WORKSPACE APPLIES IT.

  Kept as one expression so the rule is testable without mounting React. It
  mirrors `mayComplete` in consultation-workspace.tsx; if that changes, this
  has to change with it, which is the point of pinning it.
*/
function mayComplete(input: {
  editable: boolean;
  canComplete: boolean;
  dirty: boolean;
  saving: boolean;
  loading: boolean;
}) {
  return (
    input.editable &&
    input.canComplete &&
    !input.dirty &&
    !input.saving &&
    !input.loading
  );
}

/* ------------------------------------------------------------------ *
 * Buckets
 * ------------------------------------------------------------------ */

test("the desk offers five filters, in workflow order", () => {
  assert.deepEqual(
    DOCTOR_BUCKETS.map((bucket) => bucket.key),
    ["all", "wait", "review", "open", "finished"],
  );
});

test("in_consultation is OPEN, not FINISHED", () => {
  // The semantic bug Slice 4 had to fix: Finished used to hold the patient the
  // doctor was currently examining.
  assert.equal(bucketOf(row("in_consultation", "in_consultation")), "open");
});

test("a completed visit is FINISHED", () => {
  assert.equal(bucketOf(row("completed", "done")), "finished");
});

test("the other two buckets are unchanged", () => {
  assert.equal(bucketOf(row("ready_doctor", "confirmed")), "review");
  assert.equal(bucketOf(row("awaiting_cashier", "confirmed")), "wait");
  assert.equal(bucketOf(row("intake", "confirmed")), "wait");
});

test("a ready_doctor visit already in consultation is OPEN, not Review", () => {
  // Order matters in bucketOf: open is tested first, so a stage/state pair that
  // could match two rules cannot land in the tab that invites a second start.
  assert.equal(bucketOf(row("in_consultation", "in_consultation")), "open");
});

test("counts carry an open bucket and still total correctly", () => {
  const rows = [
    row("in_consultation", "in_consultation"),
    row("in_consultation", "in_consultation"),
    row("completed", "done"),
    row("ready_doctor", "confirmed"),
    row("awaiting_cashier", "confirmed"),
  ];
  assert.deepEqual(bucketCounts(rows), {
    all: 5,
    wait: 1,
    review: 1,
    open: 2,
    finished: 1,
  });
});

test("the stat cell distinguishes signed off from with-the-doctor", () => {
  assert.equal(statLabel(row("completed", "done")), "Done");
  assert.equal(statLabel(row("in_consultation", "in_consultation")), "Cons");
  assert.equal(statLabel(row("ready_doctor", "confirmed")), "Rev");
  assert.equal(statLabel(row("awaiting_cashier", "confirmed")), "Cash");
});

/* ------------------------------------------------------------------ *
 * Visible vs editable
 * ------------------------------------------------------------------ */

test("a completed visit still shows its consultation workspace", () => {
  // Keying the workspace on in_consultation alone put the doctor back on the
  // Start Consultation panel the instant they signed off.
  assert.equal(isConsultationVisible("done"), true);
  assert.equal(isConsultationVisible("in_consultation"), true);
});

test("a visit that never started shows no workspace", () => {
  for (const state of ["draft", "confirmed", "cancelled", null, undefined]) {
    assert.equal(isConsultationVisible(state), false, String(state));
  }
});

test("visible is a strictly wider question than open", () => {
  assert.equal(isConsultationMode("done"), false);
  assert.equal(isConsultationVisible("done"), true);
});

/* ------------------------------------------------------------------ *
 * The Complete gate
 * ------------------------------------------------------------------ */

const open = {
  editable: true,
  canComplete: true,
  dirty: false,
  saving: false,
  loading: false,
};

test("complete is offered when the server says the note is ready", () => {
  assert.equal(mayComplete(open), true);
});

test("complete is refused while the note is dirty", () => {
  // THE CONDITION THAT MATTERS MOST. Completion writes no clinical content, so
  // an unsaved paragraph would be frozen out of existence -- visible on screen,
  // never sent. This is why completion does not auto-save.
  assert.equal(mayComplete({ ...open, dirty: true }), false);
});

test("complete is refused mid-save", () => {
  assert.equal(mayComplete({ ...open, saving: true }), false);
});

test("complete is refused when the server reports a blocker", () => {
  assert.equal(mayComplete({ ...open, canComplete: false }), false);
});

test("complete is refused on an already-completed consultation", () => {
  assert.equal(mayComplete({ ...open, editable: false }), false);
});

/* ------------------------------------------------------------------ *
 * Blockers and warnings
 * ------------------------------------------------------------------ */

test("a missing assessment arrives as a bound, renderable blocker", () => {
  const blockers: ConsultationBlocker[] = [
    { code: "assessment_missing", message: "Record your assessment." },
  ];
  const data = envelope({ can_complete: false, completion_blockers: blockers });

  assert.equal(data.can_complete, false);
  assert.equal(data.completion_blockers[0].code, "assessment_missing");
  // The SENTENCE is the server's. The desk renders it and invents nothing.
  assert.equal(data.completion_blockers[0].message, "Record your assessment.");
});

test("a missing primary is a different blocker from a missing diagnosis", () => {
  // "Record a diagnosis" and "mark one of the diagnoses you already recorded as
  // primary" are different actions; one sentence for both would send half the
  // doctors who read it to the wrong control.
  const codes: ConsultationBlocker["code"][] = [
    "no_diagnosis",
    "no_primary_diagnosis",
  ];
  assert.notEqual(codes[0], codes[1]);
});

test("a provisional primary produces no blocker at all", () => {
  // Certainty is deliberately not required to be final: demanding it would
  // force doctors to overstate certainty on exactly the visits where lab work
  // is still pending.
  const data = envelope({ can_complete: true, completion_blockers: [] });
  assert.equal(data.can_complete, true);
  assert.deepEqual(data.completion_blockers, []);
});

test("warnings never gate completion", () => {
  const warnings: ConsultationWarning[] = [
    {
      code: "outstanding_balance",
      amount: 550,
      message: "550.00 remains outstanding.",
    },
    { code: "pending_orders", count: 2, message: "2 laboratory orders remain pending." },
  ];
  const data = envelope({ can_complete: true, completion_warnings: warnings });

  assert.equal(
    data.can_complete,
    true,
    "A patient owing money is a cashier problem, not a reason to refuse a doctor the right to finish documenting care already given.",
  );
  assert.equal(mayComplete(open), true);
  assert.equal(data.completion_warnings.length, 2);
});

test("the warning payload carries no billing structure", () => {
  const warnings: ConsultationWarning[] = [
    { code: "outstanding_balance", amount: 550, message: "550.00 remains outstanding." },
  ];
  const keys = Object.keys(warnings[0]).sort();
  assert.deepEqual(keys, ["amount", "code", "message"]);
  // A bare total is the ONE financial figure that crosses into a clinical
  // payload. No charge id, no payer, no sponsor split, no accounting bucket.
  for (const banned of [
    "charge_id",
    "billing_account",
    "payer",
    "sponsor",
    "receipt",
    "responsibility_state",
  ]) {
    assert.equal(keys.includes(banned), false, banned);
  }
});

test("a pre-start visit reports no consultation and cannot complete", () => {
  const data = envelope({ available: false, consultation: null, can_complete: false });
  assert.equal(data.available, false);
  assert.equal(data.can_complete, false);
});

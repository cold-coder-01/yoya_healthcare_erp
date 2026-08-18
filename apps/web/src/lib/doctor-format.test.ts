/**
 * Doctor Desk display and gating logic.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins, so these
 * add NO dependency to the project. Imports are relative rather than aliased
 * for the same reason -- they resolve under any runner.
 *
 * Run with `npm test`.
 *
 * THE PROPERTY THESE TESTS EXIST FOR: readiness and the Review bucket are the
 * AUTHORITATIVE stage, hospital.appointment.front_desk_stage, and nothing else.
 * The previous implementation reconstructed both from triage status plus the
 * consultation-scoped billing flag, which files a patient who is triage-complete
 * but still owes money at the desk under Review and offers Start Consultation
 * for them. front_desk_stage answers that correctly because it consults
 * encounter-WIDE clearance; several tests below pin exactly that case.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  bloodPressureText,
  bucketCounts,
  bucketOf,
  compactGender,
  doctorLabel,
  hasAnyVital,
  statLabel,
  vitalText,
  visitReadiness,
  // Explicit .ts extension: Node's ESM resolver does not add one, and
  // reception-roles.test.ts (the working precedent in this project) imports the
  // same way. Without it `npm test` cannot resolve the module at all.
} from "./doctor-format.ts";
import type { DoctorQueueRow, DoctorVitals } from "../types/doctor.ts";

const EMPTY_VITALS: DoctorVitals = {
  weight: null,
  height: null,
  temperature: null,
  heart_rate: null,
  respiratory_rate: null,
  systolic_bp: null,
  diastolic_bp: null,
  spo2: null,
  rbs: null,
  head_circumference: null,
  bmi: null,
  bmi_state: null,
  pain_level: null,
};

function row(overrides: Partial<DoctorQueueRow> = {}): DoctorQueueRow {
  return {
    appointment_id: 1,
    appointment_code: "APP001",
    appointment_date: "2026-08-17T08:00:00",
    state: "confirmed",
    patient: { id: 1, name: "Test Patient", mrn: "1003446", age: 30, gender: "female" },
    department: { id: 1, name: "Medical" },
    doctor: { id: 1, name: "Dr Test" },
    encounter: null,
    queue_stage: "ready_doctor",
    visit_type: "routine",
    triage_status: "completed",
    triage_priority: "routine",
    chief_complaint: null,
    urgent: false,
    clearance: { blocked: false, state: "cleared", reason: null },
    can_start_consultation: true,
    ...overrides,
  };
}

/** A ready visit: every gate satisfied. */
function readyInput(overrides: Partial<Parameters<typeof visitReadiness>[0]> = {}) {
  return {
    state: "confirmed",
    queueStage: "ready_doctor" as const,
    canStart: true,
    clearanceReason: null,
    ...overrides,
  };
}

/* ------------------------------------------------------------------ *
 * The gates
 * ------------------------------------------------------------------ */

test("a confirmed visit at ready_doctor is ready", () => {
  const readiness = visitReadiness(readyInput());
  assert.equal(readiness.ready, true);
  assert.equal(readiness.gate, null);
  assert.equal(readiness.reason, null);
});

test("ONLY ready_doctor is ready -- no earlier stage ever is", () => {
  for (const stage of ["new", "intake", "triage", "awaiting_cashier"] as const) {
    const readiness = visitReadiness(readyInput({ queueStage: stage }));
    assert.equal(readiness.ready, false, `stage=${stage} must not be ready`);
    assert.ok(readiness.reason && readiness.reason.length > 0);
  }
});

test("awaiting_cashier is never ready even when triage is complete", () => {
  // THE REGRESSION. The old rule read triage_status === "completed" and a
  // clearance flag scoped to the consultation charge, and called this ready.
  // front_desk_stage says awaiting_cashier because the patient still owes at
  // the desk, and that verdict is final.
  const readiness = visitReadiness(
    readyInput({
      queueStage: "awaiting_cashier",
      clearanceReason: "Financial clearance is still pending at the front desk.",
    }),
  );
  assert.equal(readiness.ready, false);
  assert.equal(readiness.gate, "clearance");
  assert.equal(
    readiness.reason,
    "Financial clearance is still pending at the front desk.",
  );
});

test("awaiting_cashier still blocks when Odoo sent no reason", () => {
  const readiness = visitReadiness(
    readyInput({ queueStage: "awaiting_cashier" }),
  );
  assert.equal(readiness.ready, false);
  assert.equal(readiness.gate, "clearance");
  assert.ok(readiness.reason && readiness.reason.length > 0);
});

test("a stage before the cashier names the triage gate, not the money", () => {
  for (const stage of ["new", "intake", "triage"] as const) {
    const readiness = visitReadiness(readyInput({ queueStage: stage }));
    assert.equal(readiness.gate, "stage");
    assert.ok(/triage/i.test(readiness.reason ?? ""), `stage=${stage}`);
  }
});

test("a visit that is not confirmed can never be ready, whatever the stage says", () => {
  for (const state of ["draft", "in_consultation", "done", "cancelled", null]) {
    const readiness = visitReadiness(readyInput({ state }));
    assert.equal(readiness.ready, false, `state=${state} must not be ready`);
    assert.equal(readiness.gate, "state");
  }
});

test("a ready stage the server will not act on is refused, and says why", () => {
  // The browser cannot see whether this user is the assigned doctor. The server
  // can, and its verdict is ANDed in rather than guessed at.
  const readiness = visitReadiness(readyInput({ canStart: false }));
  assert.equal(readiness.ready, false);
  assert.equal(readiness.gate, "assignment");
  assert.ok(/assigned/i.test(readiness.reason ?? ""));
});

test("readiness never claims ready on a finished visit reached by stage alone", () => {
  for (const stage of ["in_consultation", "completed", "cancelled"] as const) {
    const readiness = visitReadiness(readyInput({ queueStage: stage }));
    assert.equal(readiness.ready, false, `stage=${stage}`);
    assert.equal(readiness.gate, "state");
  }
});

/* ------------------------------------------------------------------ *
 * Buckets
 * ------------------------------------------------------------------ */

test("buckets are driven by the authoritative stage", () => {
  assert.equal(bucketOf(row({ queue_stage: "new" })), "wait");
  assert.equal(bucketOf(row({ queue_stage: "intake" })), "wait");
  assert.equal(bucketOf(row({ queue_stage: "triage" })), "wait");
  assert.equal(bucketOf(row({ queue_stage: "awaiting_cashier" })), "wait");
  assert.equal(bucketOf(row({ queue_stage: "ready_doctor" })), "review");
  assert.equal(
    bucketOf(row({ queue_stage: "in_consultation", state: "in_consultation" })),
    "finished",
  );
  assert.equal(bucketOf(row({ queue_stage: "completed", state: "done" })), "finished");
});

test("Review requires ready_doctor -- completed triage alone does not earn it", () => {
  // The exact row the old rule mis-filed: triage done, money still owed.
  const atCashier = row({
    queue_stage: "awaiting_cashier",
    triage_status: "completed",
    clearance: { blocked: true, state: "pending", reason: "Pending at the desk." },
  });
  assert.equal(bucketOf(atCashier), "wait");
  assert.notEqual(bucketOf(atCashier), "review");
});

test("Review also requires the visit to still be confirmed", () => {
  assert.equal(
    bucketOf(row({ queue_stage: "ready_doctor", state: "in_consultation" })),
    "wait",
  );
});

test("a started consultation counts as finished even if triage never completed", () => {
  assert.equal(
    bucketOf(
      row({
        queue_stage: "in_consultation",
        state: "in_consultation",
        triage_status: "waiting",
      }),
    ),
    "finished",
  );
});

test("bucket counts sum to the total", () => {
  const rows = [
    row({ appointment_id: 1, queue_stage: "triage" }),
    row({ appointment_id: 2, queue_stage: "ready_doctor" }),
    row({ appointment_id: 3, queue_stage: "completed", state: "done" }),
    row({ appointment_id: 4, queue_stage: "ready_doctor" }),
    row({ appointment_id: 5, queue_stage: "awaiting_cashier" }),
  ];
  const counts = bucketCounts(rows);
  assert.equal(counts.all, 5);
  assert.equal(counts.wait, 2);
  assert.equal(counts.review, 2);
  assert.equal(counts.finished, 1);
  assert.equal(counts.wait + counts.review + counts.finished, counts.all);
});

test("stat labels stay within the vendor column's short register", () => {
  assert.equal(statLabel(row({ queue_stage: "intake" })), "Wait");
  assert.equal(statLabel(row({ queue_stage: "triage" })), "Wait");
  // The desk still has them: worth distinguishing, still not workable.
  assert.equal(statLabel(row({ queue_stage: "awaiting_cashier" })), "Cash");
  assert.equal(statLabel(row({ queue_stage: "ready_doctor" })), "Rev");
  assert.equal(
    statLabel(row({ queue_stage: "in_consultation", state: "in_consultation" })),
    "Cons",
  );
  assert.equal(statLabel(row({ queue_stage: "completed", state: "done" })), "Done");
});

/* ------------------------------------------------------------------ *
 * Display
 * ------------------------------------------------------------------ */

test("a missing vital renders as an em dash, never as zero", () => {
  assert.equal(vitalText(null, "°C"), "—");
  assert.equal(vitalText(undefined), "—");
  assert.equal(vitalText(0, "°C"), "0 °C");
});

test("blood pressure needs both halves", () => {
  assert.equal(bloodPressureText({ ...EMPTY_VITALS, systolic_bp: 120 }), "—");
  assert.equal(bloodPressureText({ ...EMPTY_VITALS, diastolic_bp: 80 }), "—");
  assert.equal(
    bloodPressureText({ ...EMPTY_VITALS, systolic_bp: 120, diastolic_bp: 80 }),
    "120/80",
  );
});

test("hasAnyVital ignores the non-numeric bmi_state so an empty grid says so", () => {
  assert.equal(hasAnyVital(EMPTY_VITALS), false);
  assert.equal(hasAnyVital({ ...EMPTY_VITALS, bmi_state: "normal" }), false);
  assert.equal(hasAnyVital({ ...EMPTY_VITALS, temperature: 37 }), true);
});

test("payer labels cover the sponsorship categories and nothing commercial", () => {
  assert.equal(doctorLabel("self_pay"), "Self Pay");
  assert.equal(doctorLabel("insurance"), "Insurance");
  assert.equal(doctorLabel("credit"), "Credit");
});

test("stage labels render the authoritative vocabulary", () => {
  assert.equal(doctorLabel("ready_doctor"), "Ready");
  assert.equal(doctorLabel("awaiting_cashier"), "Cashier");
  assert.equal(doctorLabel("triage"), "Triage");
  assert.equal(doctorLabel("intake"), "Intake");
});

test("unknown selection values degrade to a readable label, not a crash", () => {
  assert.equal(doctorLabel("some_new_state"), "Some New State");
  assert.equal(doctorLabel(null), "-");
  assert.equal(doctorLabel(undefined, "n/a"), "n/a");
});

test("gender compacts for the dense queue column", () => {
  assert.equal(compactGender("male"), "M");
  assert.equal(compactGender("female"), "F");
  assert.equal(compactGender(null), "-");
});

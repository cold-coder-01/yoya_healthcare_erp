/**
 * Doctor Desk display and gating logic.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins, so these
 * add NO dependency to the project. Imports are relative rather than aliased
 * for the same reason -- they resolve under any runner.
 *
 * Run: see the note in src/lib/doctor-adapt.test.ts.
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
} from "./doctor-format";
import type { DoctorQueueRow, DoctorVitals } from "../types/doctor";

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
    queue_stage: null,
    visit_type: null,
    triage_status: "completed",
    triage_priority: "routine",
    chief_complaint: null,
    urgent: false,
    clearance: { blocked: false, message: null },
    ...overrides,
  };
}

/* ------------------------------------------------------------------ *
 * The gates
 * ------------------------------------------------------------------ */

test("a confirmed, triaged, cleared visit is ready", () => {
  const readiness = visitReadiness({
    state: "confirmed",
    triageStatus: "completed",
    clearanceBlocked: false,
    clearanceMessage: null,
  });
  assert.equal(readiness.ready, true);
  assert.equal(readiness.gate, null);
});

test("incomplete triage blocks the button and names the triage gate", () => {
  for (const status of ["not_started", "waiting", "in_progress", "cancelled"] as const) {
    const readiness = visitReadiness({
      state: "confirmed",
      triageStatus: status,
      clearanceBlocked: false,
      clearanceMessage: null,
    });
    assert.equal(readiness.ready, false, `triage=${status} must not be ready`);
    assert.equal(readiness.gate, "triage");
  }
});

test("blocked clearance blocks the button and surfaces Odoo's reason", () => {
  const readiness = visitReadiness({
    state: "confirmed",
    triageStatus: "completed",
    clearanceBlocked: true,
    clearanceMessage: "Outstanding consultation charge.",
  });
  assert.equal(readiness.ready, false);
  assert.equal(readiness.gate, "clearance");
  assert.equal(readiness.reason, "Outstanding consultation charge.");
});

test("clearance still blocks when Odoo sent no reason", () => {
  const readiness = visitReadiness({
    state: "confirmed",
    triageStatus: "completed",
    clearanceBlocked: true,
    clearanceMessage: null,
  });
  assert.equal(readiness.ready, false);
  assert.equal(readiness.gate, "clearance");
  assert.ok(readiness.reason && readiness.reason.length > 0);
});

test("triage is checked before clearance, so an untriaged patient is never sent to the cashier", () => {
  const readiness = visitReadiness({
    state: "confirmed",
    triageStatus: "waiting",
    clearanceBlocked: true,
    clearanceMessage: "Outstanding balance.",
  });
  assert.equal(readiness.gate, "triage");
});

test("a visit that is not confirmed can never be ready, whatever else is true", () => {
  for (const state of ["draft", "in_consultation", "done", "cancelled", null]) {
    const readiness = visitReadiness({
      state,
      triageStatus: "completed",
      clearanceBlocked: false,
      clearanceMessage: null,
    });
    assert.equal(readiness.ready, false, `state=${state} must not be ready`);
    assert.equal(readiness.gate, "state");
  }
});

/* ------------------------------------------------------------------ *
 * Buckets
 * ------------------------------------------------------------------ */

test("buckets map triage and visit state the way the worklist strip claims", () => {
  assert.equal(bucketOf(row({ triage_status: "waiting" })), "wait");
  assert.equal(bucketOf(row({ triage_status: "in_progress" })), "wait");
  assert.equal(bucketOf(row({ triage_status: "not_started" })), "wait");
  assert.equal(bucketOf(row({ triage_status: "completed" })), "review");
  assert.equal(bucketOf(row({ state: "in_consultation" })), "finished");
  assert.equal(bucketOf(row({ state: "done" })), "finished");
});

test("a started consultation counts as finished even if triage never completed", () => {
  assert.equal(
    bucketOf(row({ state: "in_consultation", triage_status: "waiting" })),
    "finished",
  );
});

test("bucket counts sum to the total", () => {
  const rows = [
    row({ appointment_id: 1, triage_status: "waiting" }),
    row({ appointment_id: 2, triage_status: "completed" }),
    row({ appointment_id: 3, state: "done" }),
    row({ appointment_id: 4, triage_status: "completed" }),
  ];
  const counts = bucketCounts(rows);
  assert.equal(counts.all, 4);
  assert.equal(counts.wait, 1);
  assert.equal(counts.review, 2);
  assert.equal(counts.finished, 1);
  assert.equal(counts.wait + counts.review + counts.finished, counts.all);
});

test("stat labels stay within the vendor column's short register", () => {
  assert.equal(statLabel(row({ triage_status: "waiting" })), "Wait");
  assert.equal(statLabel(row({ triage_status: "completed" })), "Rev");
  assert.equal(statLabel(row({ state: "in_consultation" })), "Cons");
  assert.equal(statLabel(row({ state: "done" })), "Done");
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

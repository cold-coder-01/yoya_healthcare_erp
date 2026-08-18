/**
 * The doctor payload adapter: mapping, ordering, and what must never leak.
 *
 * `node:test` + `node:assert` are Node built-ins, so this adds NO dependency.
 * Imports are relative so they resolve under any runner.
 *
 * RUNNING THESE
 * Node 20 (this machine) cannot strip TypeScript, and the project has no test
 * runner installed. `npm run lint` and `npx tsc --noEmit` validate them
 * statically today. To execute them, either Node >= 22.6
 * (`node --experimental-strip-types --test src/lib/*.test.ts`) or a single dev
 * dependency such as tsx or vitest is required. That dependency was NOT added
 * here, per the instruction not to alter dependencies to make tests run.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { PENDING_FIELDS, adaptQueue, adaptVisit } from "./doctor-adapt";
import type { EvaluationDetail, EvaluationQueueRow } from "../types/clinical";

function sourceRow(overrides: Partial<EvaluationQueueRow> = {}): EvaluationQueueRow {
  return {
    id: 1,
    appointment_code: "APP001",
    appointment_date: "2026-08-17T08:00:00",
    state: "confirmed",
    reason: null,
    doctor_id: { id: 7, name: "Dr Mesfin Tasew" },
    department_id: { id: 3, name: "Medical" },
    patient: {
      id: 11,
      name: "Baby Momina Haji",
      identification_code: "1003446",
      age: 1,
      gender: "female",
      date_of_birth: "2026-08-07",
    },
    encounter: { id: 5, name: "ENC0001", state: "checked_in", encounter_type: "opd" },
    evaluation: {
      id: 21,
      state: "done",
      status: "completed",
      triage_priority: "routine",
      assigned_nurse_id: null,
      started_at: "2026-08-17T07:40:00",
      completed_at: "2026-08-17T07:55:00",
    },
    billing_blocked: false,
    billing_clearance_message: null,
    ...overrides,
  };
}

function sourceDetail(): EvaluationDetail {
  return {
    appointment: {
      id: 1,
      appointment_code: "APP001",
      appointment_date: "2026-08-17T08:00:00",
      state: "confirmed",
      reason: "Fever",
      doctor_id: { id: 7, name: "Dr Mesfin Tasew" },
      department_id: { id: 3, name: "Medical" },
      billing_blocked: true,
      billing_clearance_message: "Consultation charge is unpaid.",
    },
    patient: {
      id: 11,
      name: "Baby Momina Haji",
      identification_code: "1003446",
      age: 1,
      gender: "female",
      date_of_birth: "2026-08-07",
      phone: null,
      mobile: null,
      blood_group: "o_positive",
      disease_history: "None",
      past_medical_history: "Nil",
      medical_alerts: [{ id: 2, name: "Penicillin reaction", severity: "high" }],
    },
    previous_vitals: null,
    encounter: { id: 5, name: "ENC0001", state: "checked_in", encounter_type: "opd" },
    evaluation: {
      id: 21,
      name: "EVAL0001",
      state: "done",
      status: "completed",
      patient_id: { id: 11, name: "Baby Momina Haji" },
      appointment_id: { id: 1, name: "APP001" },
      physician_id: null,
      encounter_id: { id: 5, name: "ENC0001" },
      assigned_nurse_id: null,
      triage_priority: "urgent",
      chief_complaint: "Fever for two days",
      triage_notes: "Alert, feeding well",
      pain_level: "2",
      pain_note: null,
      bmi: 16.2,
      bmi_state: "normal",
      evaluation_date: "2026-08-17T07:40:00",
      started_at: "2026-08-17T07:40:00",
      completed_at: "2026-08-17T07:55:00",
      weight: 4.2,
      height: 55,
      temperature: 38.4,
      heart_rate: 130,
      respiratory_rate: 40,
      systolic_bp: 90,
      diastolic_bp: 60,
      spo2: 98,
      rbs: null,
      head_circumference: 37,
    },
  };
}

/* ------------------------------------------------------------------ *
 * The security property: no money, no commercial payer
 * ------------------------------------------------------------------ */

/** Every key that would mean money or a commercial payer relationship. */
const FORBIDDEN_KEYS = [
  "amount",
  "required_amount",
  "paid_amount",
  "received_amount",
  "outstanding",
  "outstanding_amount",
  "balance",
  "price",
  "total",
  "receipt",
  "receipt_id",
  "payer_id",
  "payer_name",
  "patient_payer",
  "agreement",
  "agreement_id",
  "agreement_name",
  "agreement_number",
  "membership_number",
  "policy_number",
  "employee_id_number",
  "member_reference",
  "responsibility",
  "sponsor_share",
  "patient_share",
];

function collectKeys(value: unknown, into: Set<string> = new Set()): Set<string> {
  if (Array.isArray(value)) {
    for (const item of value) collectKeys(item, into);
    return into;
  }
  if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      into.add(key);
      collectKeys(child, into);
    }
  }
  return into;
}

test("no queue payload key names money or a commercial payer", () => {
  const keys = collectKeys(adaptQueue({
    date: "2026-08-17",
    states: ["confirmed"],
    count: 1,
    truncated: false,
    queue: [sourceRow()],
  }));

  for (const forbidden of FORBIDDEN_KEYS) {
    assert.equal(keys.has(forbidden), false, `queue payload must not carry "${forbidden}"`);
  }
});

test("no visit payload key names money or a commercial payer", () => {
  const keys = collectKeys(adaptVisit(sourceDetail()));

  for (const forbidden of FORBIDDEN_KEYS) {
    assert.equal(keys.has(forbidden), false, `visit payload must not carry "${forbidden}"`);
  }
});

test("clearance is a verdict and a sentence, and carries nothing else", () => {
  const visit = adaptVisit(sourceDetail());
  assert.deepEqual(Object.keys(visit.clearance).sort(), ["blocked", "message"]);
  assert.equal(visit.clearance.blocked, true);
  assert.equal(visit.clearance.message, "Consultation charge is unpaid.");
});

/* ------------------------------------------------------------------ *
 * Honesty about what the backend cannot supply
 * ------------------------------------------------------------------ */

test("the three unavailable fields are null, never guessed", () => {
  const queue = adaptQueue({
    date: "2026-08-17",
    states: ["confirmed"],
    count: 1,
    truncated: false,
    // Triage done and billing clear: the exact shape from which a stage of
    // "ready_doctor" would be tempting to infer. It must still be null,
    // because front_desk_stage uses encounter-WIDE clearance while
    // billing_blocked is scoped to the consultation charge alone.
    queue: [sourceRow({ billing_blocked: false })],
  });

  assert.equal(queue.rows[0].queue_stage, null);
  assert.equal(queue.rows[0].visit_type, null);
  assert.equal(adaptVisit(sourceDetail()).payer_type, null);
});

test("pending fields are reported identically by both routes", () => {
  const queue = adaptQueue({
    date: "2026-08-17",
    states: [],
    count: 0,
    truncated: false,
    queue: [],
  });
  assert.deepEqual(queue.meta.pending_fields, PENDING_FIELDS);
  assert.deepEqual(adaptVisit(sourceDetail()).meta.pending_fields, PENDING_FIELDS);
});

/* ------------------------------------------------------------------ *
 * Mapping
 * ------------------------------------------------------------------ */

test("identity maps onto the vendor's chart-number vocabulary", () => {
  const [mapped] = adaptQueue({
    date: "2026-08-17",
    states: [],
    count: 1,
    truncated: false,
    queue: [sourceRow()],
  }).rows;

  assert.equal(mapped.appointment_id, 1);
  assert.equal(mapped.patient.mrn, "1003446");
  assert.equal(mapped.patient.name, "Baby Momina Haji");
  assert.equal(mapped.doctor?.name, "Dr Mesfin Tasew");
  assert.equal(mapped.department?.name, "Medical");
});

test("urgency is derived from triage priority, matching URGENT_PRIORITIES", () => {
  const build = (priority: string | null) =>
    adaptQueue({
      date: "2026-08-17",
      states: [],
      count: 1,
      truncated: false,
      queue: [
        sourceRow({
          evaluation: { ...sourceRow().evaluation, triage_priority: priority },
        }),
      ],
    }).rows[0];

  assert.equal(build("routine").urgent, false);
  assert.equal(build("urgent").urgent, true);
  assert.equal(build("emergency").urgent, true);
  assert.equal(build(null).urgent, false);
});

test("medical alerts keep their severity and are never renamed to allergies", () => {
  const visit = adaptVisit(sourceDetail());
  assert.equal(visit.medical_alerts.length, 1);
  assert.equal(visit.medical_alerts[0].name, "Penicillin reaction");
  assert.equal(visit.medical_alerts[0].severity, "high");
  assert.equal("allergies" in visit, false);
});

test("triage vitals and chief complaint survive the mapping", () => {
  const visit = adaptVisit(sourceDetail());
  assert.equal(visit.triage.chief_complaint, "Fever for two days");
  assert.equal(visit.triage.priority, "urgent");
  assert.equal(visit.triage.vitals.temperature, 38.4);
  assert.equal(visit.triage.vitals.spo2, 98);
  assert.equal(visit.triage.vitals.rbs, null);
  assert.equal(visit.triage.vitals.bmi_state, "normal");
});

test("a visit with no evaluation degrades to not_started rather than throwing", () => {
  const detail = sourceDetail();
  detail.evaluation = null;
  const visit = adaptVisit(detail);
  assert.equal(visit.triage.status, "not_started");
  assert.equal(visit.triage.evaluation_id, null);
  assert.equal(visit.triage.vitals.temperature, null);
});

/* ------------------------------------------------------------------ *
 * Ordering
 * ------------------------------------------------------------------ */

test("triage-complete and cleared visits sort above the rest", () => {
  const queue = adaptQueue({
    date: "2026-08-17",
    states: [],
    count: 4,
    truncated: false,
    queue: [
      // Untriaged, arrived first.
      sourceRow({
        id: 10,
        appointment_date: "2026-08-17T07:00:00",
        evaluation: { ...sourceRow().evaluation, status: "waiting", state: "draft" },
      }),
      // Triaged but not cleared.
      sourceRow({ id: 11, appointment_date: "2026-08-17T07:10:00", billing_blocked: true }),
      // Triaged, cleared, routine.
      sourceRow({ id: 12, appointment_date: "2026-08-17T09:00:00" }),
      // Triaged, cleared, urgent -- latest arrival, must still lead.
      sourceRow({
        id: 13,
        appointment_date: "2026-08-17T10:00:00",
        evaluation: { ...sourceRow().evaluation, triage_priority: "emergency" },
      }),
    ],
  });

  assert.deepEqual(
    queue.rows.map((row) => row.appointment_id),
    [13, 12, 11, 10],
  );
});

test("ties fall back to arrival time, so equal patients keep queue order", () => {
  const queue = adaptQueue({
    date: "2026-08-17",
    states: [],
    count: 2,
    truncated: false,
    queue: [
      sourceRow({ id: 20, appointment_date: "2026-08-17T11:00:00" }),
      sourceRow({ id: 21, appointment_date: "2026-08-17T08:30:00" }),
    ],
  });

  assert.deepEqual(
    queue.rows.map((row) => row.appointment_id),
    [21, 20],
  );
});

test("an empty queue adapts to an empty payload, not a crash", () => {
  const queue = adaptQueue({
    date: "2026-08-17",
    states: [],
    count: 0,
    truncated: false,
    queue: [],
  });
  assert.deepEqual(queue.rows, []);
  assert.equal(queue.meta.count, 0);
  assert.equal(queue.meta.truncated, false);
});

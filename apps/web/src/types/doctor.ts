/**
 * THE Doctor Desk wire contract.
 *
 * These shapes are emitted DIRECTLY by yoya_emr_api's dedicated doctor
 * controller (controllers/doctor.py + services/doctor_serializers.py). The BFF
 * routes under app/api/doctor/* forward them unchanged; there is no adapter and
 * no field is reshaped in JavaScript.
 *
 * WHAT CHANGED, AND WHY IT MATTERS
 * `queue_stage` is now AUTHORITATIVE. It is hospital.appointment.front_desk_stage,
 * serialized unchanged by the Odoo serializer, and it is the ONLY thing that may
 * be read as "this patient may be seen now". It used to be permanently null and
 * reported through a `pending_fields` list, which forced the workstation to
 * approximate readiness from triage status plus a billing flag -- an
 * approximation that could label a patient Ready while they still owed money at
 * the desk, because front_desk_stage derives from ENCOUNTER-WIDE clearance while
 * `billing_blocked` is scoped to the consultation charge alone.
 *
 * `visit_type` and `payer_type` are likewise real values now. `pending_fields`
 * is gone entirely rather than kept as an always-empty array.
 *
 * WHAT IS ABSENT IS AS DELIBERATE AS WHAT IS PRESENT.
 * There is no amount, balance, receipt, sponsor-responsibility, agreement or
 * membership field anywhere in this file, and no named payer. A doctor gets a
 * clearance VERDICT, a categorical state and a fixed operational sentence drawn
 * from a server-side allowlist -- never hospital.billing.engine's own message,
 * which embeds figures and, when a visit is fully sponsored, the sponsor's name.
 * Adding such a field here is the change that would need review.
 */

import type { ApiEnvelope, Many2OneValue } from "./clinical";

export type { ApiEnvelope, Many2OneValue };

/**
 * hospital.appointment.front_desk_stage, in workflow order.
 *
 * Mirrors FRONT_DESK_STAGES in
 * yoya_reception_bridge/models/hospital_appointment.py, which is the single
 * derivation both this desk and the Front Desk read.
 */
export type DoctorQueueStage =
  | "new"
  | "intake"
  | "triage"
  | "awaiting_cashier"
  | "ready_doctor"
  | "in_consultation"
  | "completed"
  | "cancelled";

/**
 * The operational clearance indicator: a verdict, a category and a sentence.
 *
 * `blocked` is hospital.appointment._is_payment_blocking() -- the model's own
 * predicate, and the SAME input front_desk_stage consults to decide
 * awaiting_cashier vs ready_doctor, so this can never disagree with the
 * `queue_stage` beside it.
 *
 * `state` is hospital.encounter.reception_clearance_state: a Selection KEY
 * (not_required | pending | cleared | credit_authorized | sponsor_cleared |
 * emergency_bypass). It carries no amount and names no payer.
 *
 * `reason` comes from DOCTOR_CLEARANCE_REASONS, a closed set of literal strings
 * in doctor_serializers.py with no interpolation anywhere in it.
 */
export type DoctorClearance = {
  blocked: boolean;
  state: string | null;
  reason: string | null;
};

export type DoctorPatientIdentity = {
  id: number;
  name: string;
  /** hospital.patient.identification_code -- the chart number on the vendor UI. */
  mrn: string | null;
  age: string | number | null;
  gender: string | null;
};

/** Triage progress, derived from the evaluation. Drives the queue's Stat column. */
export type DoctorTriageStatus =
  | "not_started"
  | "waiting"
  | "in_progress"
  | "completed"
  | "cancelled";

export type DoctorEncounter = {
  id: number;
  name: string;
  state: string | null;
  encounter_type: string | null;
};

export type DoctorQueueRow = {
  appointment_id: number;
  appointment_code: string | null;
  appointment_date: string | null;
  /** hospital.appointment.state: confirmed | in_consultation | done. */
  state: string | null;
  patient: DoctorPatientIdentity;
  department: Many2OneValue;
  doctor: Many2OneValue;
  encounter: DoctorEncounter | null;

  /** AUTHORITATIVE. hospital.appointment.front_desk_stage, unchanged. */
  queue_stage: DoctorQueueStage;
  /** routine | emergency | follow_up | referral. */
  visit_type: string | null;

  triage_status: DoctorTriageStatus;
  triage_priority: string | null;
  chief_complaint: string | null;
  /** Priority indicator, not a stage. True for urgent/emergency or bypass. */
  urgent: boolean;

  clearance: DoctorClearance;

  /**
   * The server's own affordance verdict: may THIS user start THIS consultation
   * right now. It already folds in assignment, appointment state and the
   * authoritative stage.
   *
   * STILL NOT AUTHORIZATION. hospital.appointment.action_start_consultation()
   * re-runs every gate and is the only thing that decides.
   */
  can_start_consultation: boolean;
};

/**
 * Whether this row may be worked right now, and if not, why.
 *
 * Computed in the browser for AFFORDANCE ONLY -- to grey a button and explain
 * it before the doctor clicks. Every condition is enforced again, independently,
 * by hospital.appointment.action_start_consultation() at the model layer.
 */
export type DoctorReadiness = {
  ready: boolean;
  /** Operator-facing reason the visit is not workable. Null when ready. */
  reason: string | null;
  /** Which gate is not satisfied. Null when ready. */
  gate: "stage" | "clearance" | "state" | "assignment" | null;
};

export type DoctorVitals = {
  weight: number | null;
  height: number | null;
  temperature: number | null;
  heart_rate: number | null;
  respiratory_rate: number | null;
  systolic_bp: number | null;
  diastolic_bp: number | null;
  spo2: number | null;
  rbs: number | null;
  head_circumference: number | null;
  bmi: number | null;
  bmi_state: string | null;
  pain_level: string | null;
};

/**
 * Medical alerts. Named exactly as the schema names them.
 *
 * hospital.patient has NO authoritative allergy field, so this is never
 * labelled "Allergies" anywhere in the UI even though the vendor system has an
 * Allergy row in the same position. Presenting alerts under an Allergies
 * heading would assert clinical safety information the record cannot support.
 */
export type DoctorMedicalAlert = {
  id: number;
  name: string;
  severity: string | null;
};

export type DoctorVisitDetail = {
  visit: {
    appointment_id: number;
    appointment_code: string | null;
    appointment_date: string | null;
    state: string | null;
    reason: string | null;
    department: Many2OneValue;
    doctor: Many2OneValue;
    /** AUTHORITATIVE. */
    queue_stage: DoctorQueueStage;
    visit_type: string | null;
  };
  patient: DoctorPatientIdentity & {
    date_of_birth: string | null;
    blood_group: string | null;
    phone: string | null;
    mobile: string | null;
    disease_history: string | null;
    past_medical_history: string | null;
  };
  medical_alerts: DoctorMedicalAlert[];
  encounter: DoctorEncounter | null;
  triage: {
    evaluation_id: number | null;
    status: DoctorTriageStatus;
    priority: string | null;
    chief_complaint: string | null;
    notes: string | null;
    started_at: string | null;
    completed_at: string | null;
    vitals: DoctorVitals;
  };
  previous_vitals: (DoctorVitals & { evaluation_id: number }) | null;
  /**
   * Sponsorship CATEGORY only -- self_pay | insurance | corporate | government
   * | ngo | other. hospital.encounter.payer_type carries no groups= and the
   * Doctor group already holds read on hospital.encounter, so exposing it
   * widened no ACL. Never a payer name, agreement or membership number: those
   * live on models a doctor has no ACL for and are not reachable from here.
   */
  payer_type: string | null;
  clearance: DoctorClearance;
  can_start_consultation: boolean;
};

export type DoctorBucketCounts = {
  all: number;
  wait: number;
  review: number;
  finished: number;
};

export type DoctorQueueResponse = {
  rows: DoctorQueueRow[];
  counts: DoctorBucketCounts;
  capabilities: DoctorCapabilities;
  filters: {
    date: string;
    department_id: number | null;
    q: string | null;
    limit: number;
  };
  meta: {
    row_count: number;
    truncated: boolean;
    states: string[];
    stages: DoctorQueueStage[];
  };
};

export type DoctorVisitResponse = DoctorVisitDetail;

export type DoctorCapabilities = {
  doctor_desk: boolean;
  start_consultation_role: boolean;
};

export type DoctorSession = {
  user: { id: number; name: string; login: string };
  company: Many2OneValue;
  doctor: Many2OneValue;
  /** True when the signed-in user holds group_hospital_doctor. */
  is_doctor: boolean;
  capabilities: DoctorCapabilities;
};

/** The visit as it stands AFTER the mutation, plus its new bucket. */
export type DoctorStartConsultationResponse = DoctorVisitDetail & {
  bucket: "wait" | "review" | "finished";
};

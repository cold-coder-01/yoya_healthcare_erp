/**
 * THE Doctor Desk wire contract.
 *
 * This file is the agreement between the workstation and whatever serves it.
 * Today the BFF builds these shapes by adapting the existing doctor-readable
 * clinical endpoints (see app/api/doctor/_adapter.ts). When the dedicated
 * Odoo controller lands it will emit these shapes directly and the adapter is
 * deleted -- no component changes, because nothing below describes the
 * transport.
 *
 * THREE FIELDS ARE DELIBERATELY NULLABLE-BY-BACKEND, not nullable-by-domain:
 *
 *     queue_stage    hospital.appointment.front_desk_stage
 *     visit_type     hospital.appointment.visit_type
 *     payer_type     hospital.encounter.payer_type
 *
 * All three exist and are readable by a doctor at the model layer, but no
 * endpoint a doctor may call returns them yet. They are typed `| null` and
 * every consumer renders a "pending" affordance rather than a wrong value.
 * `meta.pending_fields` names them at runtime so the UI never has to guess.
 *
 * WHAT IS ABSENT IS AS DELIBERATE AS WHAT IS PRESENT.
 * There is no amount, balance, receipt, sponsor-responsibility or agreement
 * field anywhere in this file, and no named payer. A doctor gets a clearance
 * VERDICT and its reason; money and commercial payer data belong to the
 * cashier and Front Desk surfaces and are not modelled here at all. Adding
 * such a field to this file is the change that would need review.
 */

import type { ApiEnvelope, Many2OneValue } from "./clinical";

export type { ApiEnvelope, Many2OneValue };

/** Fields the current backend cannot supply. Empty once the controller lands. */
export type DoctorPendingField = "queue_stage" | "visit_type" | "payer_type";

/**
 * The operational clearance indicator. A verdict and a sentence, nothing else.
 *
 * `blocked` is hospital.appointment.billing_blocked and `message` is
 * billing_clearance_message -- both compute_sudo on the Odoo side, so a doctor
 * reads the verdict without holding rights on any cash field and without the
 * API calling sudo() anywhere.
 */
export type DoctorClearance = {
  blocked: boolean;
  message: string | null;
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

export type DoctorQueueRow = {
  appointment_id: number;
  appointment_code: string | null;
  appointment_date: string | null;
  /** hospital.appointment.state: confirmed | in_consultation | ... */
  state: string | null;
  patient: DoctorPatientIdentity;
  department: Many2OneValue;
  doctor: Many2OneValue;
  encounter: { id: number; name: string; state: string | null } | null;

  /** Pending backend. front_desk_stage: the authoritative Ready-For-Doctor signal. */
  queue_stage: string | null;
  /** Pending backend. routine | emergency | follow_up. */
  visit_type: string | null;

  triage_status: DoctorTriageStatus;
  triage_priority: string | null;
  chief_complaint: string | null;
  /** Priority indicator, not a stage. True for urgent/emergency triage priority. */
  urgent: boolean;

  clearance: DoctorClearance;
};

/**
 * Whether this row may be worked right now, and if not, why.
 *
 * Computed in the browser for AFFORDANCE ONLY -- to grey a button and explain
 * it before the doctor clicks. It is not authorization. Every one of these
 * conditions is enforced again, independently, by
 * hospital.appointment.action_start_consultation() at the model layer, which
 * is the only thing that decides whether a consultation actually starts.
 */
export type DoctorReadiness = {
  ready: boolean;
  /** Operator-facing reason the desk is not ready. Null when ready. */
  reason: string | null;
  /** Which gate is not satisfied. Null when ready. */
  gate: "triage" | "clearance" | "state" | null;
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
    /** Pending backend. */
    queue_stage: string | null;
    /** Pending backend. */
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
  encounter: {
    id: number;
    name: string;
    state: string | null;
    encounter_type: string | null;
  } | null;
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
   * Pending backend. Sponsorship CATEGORY only -- self_pay | insurance | credit.
   * Never a payer name, agreement or membership: those models have no Doctor
   * ACL and the commercial relationship is not a clinician's concern.
   */
  payer_type: string | null;
  clearance: DoctorClearance;
};

export type DoctorQueueMeta = {
  date: string;
  count: number;
  truncated: boolean;
  /** Fields this backend could not supply. See the module note. */
  pending_fields: DoctorPendingField[];
};

export type DoctorQueueResponse = {
  rows: DoctorQueueRow[];
  meta: DoctorQueueMeta;
};

export type DoctorVisitResponse = DoctorVisitDetail & {
  meta: { pending_fields: DoctorPendingField[] };
};

export type DoctorSession = {
  user: { id: number; name: string; login: string };
  company: Many2OneValue;
  doctor: Many2OneValue;
  /** True when the signed-in user holds group_hospital_doctor. */
  is_doctor: boolean;
  groups: Record<string, boolean> | string[];
};

export type DoctorStartConsultationResponse = {
  appointment: { id: number; state: string | null } | null;
  encounter: { id: number; name: string; state: string | null } | null;
};

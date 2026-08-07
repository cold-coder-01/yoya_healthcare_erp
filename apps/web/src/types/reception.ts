/**
 * Types mirroring yoya_emr_api's reception serializers.
 *
 * Selection-key fields (visit_type, card_status, clearance state, queue stage)
 * are typed as `string | null`, NOT as unions. They cross a network boundary,
 * so a value outside the expected set is a genuine runtime possibility and a
 * union here would be a type-level lie. lib/reception-format.ts maps them to
 * labels with a humanised fallback.
 */
import type { ApiErrorShape } from "./clinical";
import type { ReceptionRoles } from "@/lib/reception-roles";

export type ApiEnvelope<T> =
  | {
      success: true;
      data: T;
    }
  | {
      success: false;
      error: ApiErrorShape;
    };

/** hospital.department / hospital.doctor many2one, as serialized by m2o_value. */
export type ReferenceRef = {
  id: number;
  name: string;
};

export type ReceptionSession = {
  user: {
    id: number;
    name: string;
    login: string;
  };
  company: {
    id: number;
    name: string;
    currency: string;
  };
  /**
   * Single source of truth for the role shape. Notably contains NO `nurse`
   * flag -- role_flags() in reception_scope.py exposes exactly these six.
   */
  roles: ReceptionRoles;
  capabilities: {
    create_visit: boolean;
    create_patient_through_workflow: boolean;
    send_to_triage: boolean;
    emergency_bypass: boolean;
    payer_authorization: boolean;
    record_payment: boolean;
    record_payment_api_enabled: boolean;
  };
};

export type ReceptionPatientSearchResult = {
  id: number;
  identification_code: string | null;
  name: string;
  date_of_birth: string | null;
  /** hospital.patient.age is a stored Integer compute -- always a number. */
  age: number | null;
  gender: string | null;
  phone: string | null;
  mobile: string | null;
  blood_group: string | null;
  has_existing_first_card: boolean;
  latest_card_status: string | null;
};

export type ReceptionVisitPreview = {
  visit_type: string;
  patient: ReferenceRef | null;
  department: ReferenceRef | null;
  doctor: ReferenceRef | null;
  card: {
    required: boolean;
    reason: string;
    existing_issue_id: number | null;
    /** null when no card is required -- the resolver is not called. */
    service: ReferenceRef | null;
    price: number;
  };
  consultation: {
    /**
     * Always present: get_default_consultation_service() raises rather than
     * returning nothing, so preview_visit cannot succeed without it.
     */
    service: ReferenceRef;
    price: number;
  };
  total: number;
  currency: string;
};

export type ReceptionQueueItem = {
  id: number;
  appointment_code: string | null;
  appointment_date: string | null;
  patient: {
    id: number;
    name: string;
    identification_code: string | null;
  };
  visit_type: string | null;
  department: ReferenceRef | null;
  doctor: ReferenceRef | null;
  /** hospital.patient.card.issue.state, or null when no card exists. */
  card_status: string | null;
  reception_clearance: ReceptionClearance;
  clinical_queue_stage: string | null;
  emergency: boolean;
  permitted_actions: {
    send_to_triage: boolean;
    open: boolean;
  };
};

/**
 * Encounter-wide reception clearance, from
 * hospital.encounter._reception_clearance_summary().
 *
 * Distinct from the appointment's consultation-only billing_blocked signal,
 * which must never drive a reception decision.
 */
export type ReceptionClearance = {
  required: number;
  received: number;
  outstanding: number;
  ok: boolean;
  /** not_required | pending | cleared | credit_authorized | emergency_bypass */
  state: string | null;
  message: string | null;
};

export type ReceptionQueueResponse = {
  date: string;
  count: number;
  limit: number;
  truncated: boolean;
  queue: ReceptionQueueItem[];
};

export type Department = {
  id: number;
  name: string;
  code: string | null;
};

export type Doctor = {
  id: number;
  name: string;
  department: ReferenceRef | null;
  /** True when hospital.doctor.user_id is set. Not an availability signal. */
  user_linked: boolean;
  active: boolean;
};

/* ------------------------------------------------------------------ *
 * Visit detail (serialize_visit_detail)
 * ------------------------------------------------------------------ */

/** serialize_appointment_reception */
export type ReceptionAppointment = {
  id: number;
  appointment_code: string | null;
  appointment_date: string | null;
  state: string | null;
  visit_type: string | null;
  reason: string | null;
  doctor: ReferenceRef | null;
  department: ReferenceRef | null;
  triage_destination: ReferenceRef | null;
  reception_workflow_managed: boolean;
  clinical_queue_stage: string | null;
  registered_by: ReferenceRef | null;
  registered_at: string | null;
};

/** serialize_encounter_reception. Null until the encounter is opened. */
export type ReceptionEncounter = {
  id: number;
  name: string;
  state: string | null;
  encounter_type: string | null;
  payer_type: string | null;
  payer: ReferenceRef | null;
} | null;

/** serialize_card_issue. Null when the patient has no card issuance. */
export type ReceptionCardIssue = {
  id: number;
  name: string;
  state: string;
  issue_reason: string;
  card_number: string | null;
  service: ReferenceRef | null;
  charge_line_id: number | null;
  charge_amount: number | null;
  charge_payment_state: string | null;
  waiver_reason: string | null;
  issued_at: string | null;
} | null;

/** serialize_charge_line */
export type ReceptionChargeLine = {
  id: number;
  name: string;
  description: string;
  service: ReferenceRef | null;
  amount: number | null;
  received: number | null;
  outstanding: number | null;
  charge_state: string | null;
  payment_state: string | null;
  authorization_state: string | null;
  billing_basis: string | null;
  /** patient_card | consultation | <source_model> | other */
  source_category: string;
  source_event: string | null;
};

/**
 * Consultation-only clearance. Reported separately and must NEVER drive a
 * reception decision -- hospital_billing scopes billing_blocked to the
 * consultation charge alone.
 */
export type ConsultationClearance = {
  required: number;
  outstanding: number;
  blocked: boolean;
  message: string | null;
  scope_note: string;
};

/** serialize_emergency_summary. Null when there is no encounter. */
export type ReceptionEmergency = {
  emergency_bypass: boolean;
  reason: string | null;
  authorized_by: ReferenceRef | null;
  authorized_at: string | null;
  screened_by: ReferenceRef | null;
  screened_at: string | null;
  highest_danger_severity: string | null;
} | null;

/** serialize_danger_sign */
export type ReceptionDangerSign = {
  id: number;
  name: string;
  code: string | null;
  severity: string;
  requires_immediate_triage: boolean;
};

export type ReceptionPermittedActions = {
  send_to_triage: boolean;
  emergency_bypass: boolean;
  authorize_payer: boolean;
  /** Always false: the payment API is not enabled. */
  record_payment: boolean;
  blocked_reason: string | null;
};

export type ReceptionVisitDetail = {
  patient: ReceptionPatientSearchResult;
  appointment: ReceptionAppointment;
  encounter: ReceptionEncounter;
  card_issue: ReceptionCardIssue;
  reception_clearance: ReceptionClearance;
  consultation_clearance: ConsultationClearance;
  charge_lines: ReceptionChargeLine[];
  emergency: ReceptionEmergency;
  /**
   * Danger signs ALREADY attached to the encounter -- not a catalogue of
   * selectable options. There is no danger-sign reference endpoint yet, so
   * this is empty before a bypass is recorded.
   */
  danger_signs: ReceptionDangerSign[];
  permitted_actions: ReceptionPermittedActions;
};

/* ------------------------------------------------------------------ *
 * Visit creation
 * ------------------------------------------------------------------ */

/**
 * The fields the reception API will actually ACCEPT when registering a
 * patient. Single source of truth: the runtime filter in new-visit-client and
 * the NewPatientValues type below are both derived from this list, so they
 * cannot drift apart.
 *
 * This is the EFFECTIVE set, which is narrower than either allowlist alone:
 *
 *   effective = (PATIENT_VALUE_FIELDS - PATIENT_FORBIDDEN_FIELDS)
 *               ∩ PATIENT_REGISTRATION_FIELDS
 *
 * `state` is deliberately EXCLUDED. hospital.patient.state is a Char address
 * field (region/province) and hospital.reception.workflow's own allowlist
 * permits it, but yoya_emr_api/controllers/reception.py lists it in BOTH
 * PATIENT_VALUE_FIELDS and PATIENT_FORBIDDEN_FIELDS -- and the forbidden
 * check runs first, so any request carrying it is rejected outright with
 * "These fields cannot be supplied when registering a patient: state."
 * Sending it can never succeed until that server-side contradiction is
 * resolved, which is out of scope here.
 *
 * `identification_code` is absent for a different reason: the MRN is
 * sequence-assigned by Odoo and must never be client-supplied.
 */
export const PATIENT_VALUE_KEYS = [
  "name",
  "date_of_birth",
  "gender",
  "phone",
  "mobile",
  "address",
  "city",
  "zip_code",
  "country",
  "emergency_contact_name",
  "emergency_contact_phone",
  "blood_group",
] as const;

export type NewPatientValueKey = (typeof PATIENT_VALUE_KEYS)[number];

/** `name` is the only required field; everything else is optional. */
export type NewPatientValues = { name: string } & Partial<
  Record<Exclude<NewPatientValueKey, "name">, string>
>;

/** Exactly one of patient_id / patient_values, never both. */
export type CreateVisitPayload = {
  patient_id?: number;
  patient_values?: NewPatientValues;
  visit_type: string;
  department_id: number;
  doctor_id?: number;
  appointment_date?: string;
  reason?: string;
  triage_destination_id?: number;
};

/**
 * Extra fields the API attaches to a 409 reception_clearance_required error.
 * `normalizeError` in the BFF preserves unknown keys, so these survive.
 */
export type ClearanceErrorDetails = {
  required_amount?: number;
  received_amount?: number;
  outstanding_amount?: number;
  clearance_state?: string;
  clearance_message?: string;
};

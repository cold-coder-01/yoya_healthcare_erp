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

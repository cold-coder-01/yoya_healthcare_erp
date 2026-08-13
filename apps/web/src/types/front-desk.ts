import type { ApiEnvelope, ReferenceRef } from "@/types/reception";

export type { ApiEnvelope };

export type FrontDeskClearance = {
  required: number;
  received: number;
  outstanding: number;
  ok: boolean;
  state: string | null;
  message: string | null;
};

export type FrontDeskPermittedActions = {
  record_triage: boolean;
  complete_triage: boolean;
  record_payment: boolean;
  send_to_cashier: boolean;
  start_consultation: boolean;
  emergency_bypass: boolean;
  authorize_payer: boolean;
  blocked_reason: string | null;
};

export type FrontDeskQueueRow = {
  appointment_id: number;
  appointment_code: string | null;
  encounter_id: number | null;
  patient_id: number;
  mrn: string | null;
  patient_name: string;
  age: number | null;
  gender: string | null;
  arrived_at: string | null;
  department: ReferenceRef | null;
  doctor: ReferenceRef | null;
  visit_type: string | null;
  appointment_state: string;
  encounter_state: string | null;
  evaluation_id: number | null;
  triage_state: string | null;
  triage_priority: string | null;
  clearance: FrontDeskClearance;
  financial_clearance_state: string | null;
  operational_funding_state: string | null;
  emergency: boolean;
  queue_stage: string;
  urgent: boolean;
  permitted_actions: FrontDeskPermittedActions;
};

export type FrontDeskCounts = {
  today: number;
  intake: number;
  triage: number;
  awaiting_cashier: number;
  ready_doctor: number;
  urgent: number;
  in_consultation: number;
  completed: number;
  cancelled: number;
};

export type FrontDeskWorklist = {
  rows: FrontDeskQueueRow[];
  counts: FrontDeskCounts;
  capabilities: Record<string, boolean>;
  filters: {
    date: string;
    stage: string[];
    department_id: number | null;
    doctor_id: number | null;
    q: string | null;
    limit: number;
  };
  meta: {
    row_count: number;
    window_count: number;
    truncated: boolean;
    stages: string[];
  };
};

export type FrontDeskVitals = {
  weight: number;
  height: number;
  temperature: number;
  heart_rate: number;
  respiratory_rate: number;
  systolic_bp: number;
  diastolic_bp: number;
  spo2: number;
  rbs: number;
  head_circumference: number;
  bmi: number;
  bmi_state: string | null;
  pain_level: string | null;
  pain_note: string | null;
};

export type FrontDeskVisit = {
  row: FrontDeskQueueRow;
  patient: {
    id: number;
    mrn: string | null;
    name: string;
    date_of_birth: string | null;
    age: number | null;
    gender: string | null;
    phone: string | null;
    mobile: string | null;
    blood_group: string | null;
  };
  visit: {
    appointment_id: number;
    appointment_code: string | null;
    appointment_date: string | null;
    arrived_at: string | null;
    state: string;
    visit_type: string | null;
    reason: string | null;
    department: ReferenceRef | null;
    doctor: ReferenceRef | null;
    triage_destination: ReferenceRef | null;
    reception_workflow_managed: boolean;
  };
  encounter: {
    id: number;
    name: string;
    state: string;
    encounter_type: string | null;
    payer_type: string | null;
    emergency_bypass: boolean;
    emergency_bypass_reason: string | null;
  } | null;
  evaluation: {
    id: number;
    name: string;
    state: string;
    evaluation_date: string | null;
    started_at: string | null;
    completed_at: string | null;
    assigned_nurse: ReferenceRef | null;
    chief_complaint: string | null;
    triage_priority: string | null;
    triage_notes: string | null;
    vitals: FrontDeskVitals;
  } | null;
  clearance: FrontDeskClearance;
  permitted_actions: FrontDeskPermittedActions;
};

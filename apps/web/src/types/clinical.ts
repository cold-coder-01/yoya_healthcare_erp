export type Many2OneValue = {
  id: number;
  name: string;
} | null;

export type ApiErrorShape = {
  code: string;
  message: string;
  details?: unknown;
  billing_blocked?: boolean;
  billing_clearance_message?: string | null;
  [key: string]: unknown;
};

export type ApiEnvelope<T> =
  | {
      success: true;
      data: T;
    }
  | {
      success: false;
      error: ApiErrorShape;
    };

export type ClinicalSession = {
  user: {
    id: number;
    name: string;
    login: string;
  };
  company: Many2OneValue;
  groups: string[];
  doctor: Many2OneValue;
  permitted_departments: Many2OneValue[];
};

export type PatientIdentity = {
  id: number;
  name: string;
  identification_code: string | null;
  age: string | number | null;
  gender: string | null;
  date_of_birth: string | null;
};

export type PatientDetail = PatientIdentity & {
  phone: string | null;
  mobile: string | null;
  blood_group: string | null;
  disease_history: string | null;
  past_medical_history: string | null;
  medical_alerts: Array<{
    id: number;
    name: string;
    severity: string | null;
  }>;
};

export type EncounterSummary = {
  id: number;
  name: string;
  state: string | null;
  encounter_type: string | null;
} | null;

export type AppointmentCore = {
  id: number;
  appointment_code: string | null;
  appointment_date: string | null;
  state: string | null;
  reason: string | null;
  doctor_id: Many2OneValue;
  department_id: Many2OneValue;
  billing_blocked?: boolean;
  billing_clearance_message?: string | null;
};

export type PreviousVitals = {
  evaluation_id: number;
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
} | null;

export type ClinicalEvaluation = {
  id: number;
  name: string;
  state: string | null;
  status: string | null;
  patient_id: Many2OneValue;
  appointment_id: Many2OneValue;
  physician_id: Many2OneValue;
  encounter_id: Many2OneValue;
  assigned_nurse_id: Many2OneValue;
  triage_priority: string | null;
  chief_complaint: string | null;
  triage_notes: string | null;
  pain_level: string | null;
  pain_note: string | null;
  bmi: number | null;
  bmi_state: string | null;
  evaluation_date: string | null;
  started_at: string | null;
  completed_at: string | null;
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
} | null;

export type EvaluationQueueRow = AppointmentCore & {
  patient: PatientIdentity;
  encounter: EncounterSummary;
  evaluation: {
    id: number | null;
    state: string | null;
    status: string | null;
    triage_priority: string | null;
    assigned_nurse_id: Many2OneValue;
    started_at: string | null;
    completed_at: string | null;
  };
  billing_blocked: boolean;
  billing_clearance_message: string | null;
};

export type EvaluationQueueResponse = {
  date: string;
  states: string[];
  count: number;
  truncated: boolean;
  queue: EvaluationQueueRow[];
};

export type EvaluationDetail = {
  appointment: AppointmentCore & {
    billing_blocked: boolean;
    billing_clearance_message: string | null;
  };
  patient: PatientDetail;
  previous_vitals: PreviousVitals;
  encounter: EncounterSummary;
  evaluation: ClinicalEvaluation;
};

export type EvaluationSavePayload = {
  physician_id?: number | null;
  encounter_id?: number | null;
  assigned_nurse_id?: number | null;
  triage_priority?: string | null;
  chief_complaint?: string | null;
  triage_notes?: string | null;
  weight?: number | null;
  height?: number | null;
  temperature?: number | null;
  heart_rate?: number | null;
  respiratory_rate?: number | null;
  systolic_bp?: number | null;
  diastolic_bp?: number | null;
  spo2?: number | null;
  rbs?: number | null;
  head_circumference?: number | null;
  pain_level?: string | null;
  pain_note?: string | null;
};

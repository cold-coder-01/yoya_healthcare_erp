/**
 * Adapts the existing doctor-readable clinical payloads into the Doctor Desk
 * contract in types/doctor.ts.
 *
 * WHY THIS FILE EXISTS, AND WHEN IT DIES
 * The Doctor Desk needs a doctor-shaped read that no endpoint currently
 * provides. Rather than let the workstation consume triage-shaped payloads and
 * grow the mapping through a dozen components, the whole mapping lives here.
 * When yoya_emr_api gains its dedicated doctor controller, the routes stop
 * calling these functions and forward the controller's payload straight
 * through -- this file is deleted and nothing in components/doctor changes.
 *
 * IT ADAPTS, IT DOES NOT DECIDE.
 * Nothing here invents a workflow fact. Where the source payload cannot answer
 * a question, the field is null and its name is reported in `pending_fields`
 * so the UI can say "not available" instead of showing a default that looks
 * like data. In particular this file NEVER derives `queue_stage` from the
 * triage status and the billing flag: `front_desk_stage` is computed on the
 * appointment from encounter-WIDE clearance, while `billing_blocked` is scoped
 * to the consultation charge alone. Reproducing it from a narrower input would
 * silently over-promise "Ready For Doctor" for a patient who still owes money
 * at the desk, which is the exact failure the Odoo comment warns against.
 *
 * It lives in lib/ and NOT behind `server-only` on purpose: it is a pure
 * data transform with no session, no I/O and no secrets, which makes it
 * directly unit-testable. The BFF routes that call it are server-only, and
 * they are what actually holds the Odoo session.
 */
import type {
  EvaluationDetail,
  EvaluationQueueResponse,
  EvaluationQueueRow,
} from "@/types/clinical";
import type {
  DoctorMedicalAlert,
  DoctorPendingField,
  DoctorQueueResponse,
  DoctorQueueRow,
  DoctorTriageStatus,
  DoctorVisitResponse,
  DoctorVitals,
} from "@/types/doctor";

/**
 * Fields the clinical endpoints cannot supply. Kept as a single constant so a
 * future controller migration is one deletion, and so the queue and the visit
 * routes can never report different pending sets.
 */
export const PENDING_FIELDS: DoctorPendingField[] = [
  "queue_stage",
  "visit_type",
  "payer_type",
];

/** Mirrors yoya_emr_api.services.front_desk_serializers.URGENT_PRIORITIES. */
const URGENT_PRIORITIES = new Set(["urgent", "emergency"]);

const TRIAGE_STATUSES = new Set<DoctorTriageStatus>([
  "not_started",
  "waiting",
  "in_progress",
  "completed",
  "cancelled",
]);

function triageStatus(value: string | null | undefined): DoctorTriageStatus {
  return TRIAGE_STATUSES.has(value as DoctorTriageStatus)
    ? (value as DoctorTriageStatus)
    : "not_started";
}

function vitalsFrom(source: Partial<DoctorVitals> | null | undefined): DoctorVitals {
  return {
    weight: source?.weight ?? null,
    height: source?.height ?? null,
    temperature: source?.temperature ?? null,
    heart_rate: source?.heart_rate ?? null,
    respiratory_rate: source?.respiratory_rate ?? null,
    systolic_bp: source?.systolic_bp ?? null,
    diastolic_bp: source?.diastolic_bp ?? null,
    spo2: source?.spo2 ?? null,
    rbs: source?.rbs ?? null,
    head_circumference: source?.head_circumference ?? null,
    bmi: source?.bmi ?? null,
    bmi_state: source?.bmi_state ?? null,
    pain_level: source?.pain_level ?? null,
  };
}

function adaptQueueRow(row: EvaluationQueueRow): DoctorQueueRow {
  const priority = row.evaluation?.triage_priority ?? null;

  return {
    appointment_id: row.id,
    appointment_code: row.appointment_code,
    appointment_date: row.appointment_date,
    state: row.state,
    patient: {
      id: row.patient.id,
      name: row.patient.name,
      mrn: row.patient.identification_code,
      age: row.patient.age,
      gender: row.patient.gender,
    },
    department: row.department_id,
    doctor: row.doctor_id,
    encounter: row.encounter
      ? { id: row.encounter.id, name: row.encounter.name, state: row.encounter.state }
      : null,

    // Not derivable from this source. See the module note.
    queue_stage: null,
    visit_type: null,

    triage_status: triageStatus(row.evaluation?.status),
    triage_priority: priority,
    // The queue endpoint carries no chief complaint; the detail endpoint does.
    chief_complaint: null,
    urgent: priority ? URGENT_PRIORITIES.has(priority) : false,

    clearance: {
      blocked: Boolean(row.billing_blocked),
      message: row.billing_clearance_message ?? null,
    },
  };
}

/**
 * Queue ordering.
 *
 * The brief asks the queue to prioritise visits that are triage-complete and
 * financially cleared. Without `front_desk_stage` this ordering is the closest
 * HONEST approximation, built only from facts this payload actually carries:
 * completed triage first, then unblocked billing, then urgency, then arrival
 * time. It is a SORT, never a claim -- no row is labelled Ready For Doctor,
 * and the start button still defers entirely to Odoo.
 */
function queueRank(row: DoctorQueueRow) {
  const triageDone = row.triage_status === "completed" ? 0 : 1;
  const cleared = row.clearance.blocked ? 1 : 0;
  const urgent = row.urgent ? 0 : 1;
  return { triageDone, cleared, urgent };
}

function compareRows(a: DoctorQueueRow, b: DoctorQueueRow) {
  const left = queueRank(a);
  const right = queueRank(b);

  if (left.triageDone !== right.triageDone) return left.triageDone - right.triageDone;
  if (left.cleared !== right.cleared) return left.cleared - right.cleared;
  if (left.urgent !== right.urgent) return left.urgent - right.urgent;

  const at = a.appointment_date ?? "";
  const bt = b.appointment_date ?? "";
  if (at !== bt) return at < bt ? -1 : 1;
  return a.appointment_id - b.appointment_id;
}

export function adaptQueue(source: EvaluationQueueResponse): DoctorQueueResponse {
  const rows = (source.queue ?? []).map(adaptQueueRow).sort(compareRows);

  return {
    rows,
    meta: {
      date: source.date,
      count: rows.length,
      truncated: Boolean(source.truncated),
      pending_fields: PENDING_FIELDS,
    },
  };
}

export function adaptVisit(source: EvaluationDetail): DoctorVisitResponse {
  const { appointment, patient, evaluation, encounter, previous_vitals: previous } =
    source;

  const alerts: DoctorMedicalAlert[] = (patient.medical_alerts ?? []).map((alert) => ({
    id: alert.id,
    name: alert.name,
    severity: alert.severity ?? null,
  }));

  return {
    visit: {
      appointment_id: appointment.id,
      appointment_code: appointment.appointment_code,
      appointment_date: appointment.appointment_date,
      state: appointment.state,
      reason: appointment.reason,
      department: appointment.department_id,
      doctor: appointment.doctor_id,
      queue_stage: null,
      visit_type: null,
    },
    patient: {
      id: patient.id,
      name: patient.name,
      mrn: patient.identification_code,
      age: patient.age,
      gender: patient.gender,
      date_of_birth: patient.date_of_birth,
      blood_group: patient.blood_group,
      phone: patient.phone,
      mobile: patient.mobile,
      disease_history: patient.disease_history,
      past_medical_history: patient.past_medical_history,
    },
    medical_alerts: alerts,
    encounter: encounter
      ? {
          id: encounter.id,
          name: encounter.name,
          state: encounter.state,
          encounter_type: encounter.encounter_type,
        }
      : null,
    triage: {
      evaluation_id: evaluation?.id ?? null,
      status: triageStatus(evaluation?.status),
      priority: evaluation?.triage_priority ?? null,
      chief_complaint: evaluation?.chief_complaint ?? null,
      notes: evaluation?.triage_notes ?? null,
      started_at: evaluation?.started_at ?? null,
      completed_at: evaluation?.completed_at ?? null,
      vitals: vitalsFrom(evaluation),
    },
    previous_vitals: previous
      ? { ...vitalsFrom(previous), evaluation_id: previous.evaluation_id }
      : null,
    payer_type: null,
    clearance: {
      blocked: Boolean(appointment.billing_blocked),
      message: appointment.billing_clearance_message ?? null,
    },
    meta: { pending_fields: PENDING_FIELDS },
  };
}

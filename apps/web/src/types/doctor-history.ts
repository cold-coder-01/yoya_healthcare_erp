/**
 * Longitudinal clinical history contracts.
 *
 * EVERY FIELD BELOW EXISTS IN THE SLICE 9A PAYLOAD. Nothing here is invented,
 * widened "just in case", or carried over from a sibling contract that happens
 * to look similar. Where History returns the same shape as the Results tab, the
 * Results type is REUSED rather than restated, so the two surfaces cannot drift
 * apart about what a released result means.
 *
 * WHAT IS DELIBERATELY ABSENT, AND WHY IT IS ABSENT BY CONSTRUCTION
 * ----------------------------------------------------------------
 * There is no payer, insurance, charge, payment, receipt, cashier, accounting,
 * fiscal, stock or batch field anywhere in this file, because the server emits
 * none. Two absences are worth naming individually, because the obvious reuse
 * would reintroduce them:
 *
 *   `billing_blocked` is STRIPPED by the server from every history laboratory
 *   and radiology row. LaboratoryReview and RadiologyReview both declare it, so
 *   they are reused through Omit<> rather than directly. A history row that
 *   typechecked as a Results row would let a future edit render a billing
 *   signal on a clinical history screen and nothing would object.
 *
 *   The consultation note drops `version` and `editable`. Both are
 *   live-workflow affordances: `version` exists so a save can detect a
 *   concurrent write, and History has no save. Keeping them would invite a
 *   client to attempt a write the model refuses.
 *
 * THERE IS NO patient_id, AND THERE IS NO appointment_code.
 * The patient is derived server-side from the current visit and is never named
 * by the browser. The episode is identified by `encounter_code`; Slice 9A
 * withholds the appointment record itself, so no appointment code exists to
 * render. `appointment_id` IS present, and is used for exactly one thing:
 * addressing the detail and image routes.
 */
import type {
  DoctorDiagnosis,
} from "./doctor-diagnosis";
import type { DoctorPrescription } from "./doctor-medication";
import type {
  ConsultationNarrativeField,
  ConsultationState,
} from "./doctor-consultation";
import type {
  LaboratoryReview,
  RadiologyReview,
} from "./doctor-results";

/**
 * hospital.encounter.state, as History reports it.
 *
 * Widened to string rather than a closed union: the server sends the raw state
 * alongside a `status_label` it has already resolved, and the screen renders
 * the label. Pinning a union here would make a new encounter state a
 * typecheck failure on a screen that would have displayed it correctly.
 */
export type HistoryVisitStatus = string;

/** Minimal patient identity. No raw ORM id: the server does not send one. */
export type HistoryPatient = {
  identification_code: string | null;
  name: string | null;
  age: number | null;
  gender: string | null;
};

/**
 * The compact clinical-content indicators on an episode row.
 *
 * COUNTS, NEVER CONTENT. `abnormal_results` and `critical_results` are derived
 * server-side from the laboratory's own `abnormal_flag`; the row shows how many
 * there are and never what they were, which is what opening the episode is for.
 */
export type HistoryVisitCounts = {
  diagnoses: number;
  laboratory: number;
  radiology: number;
  medications: number;
  images: number;
  abnormal_results: number;
  critical_results: number;
};

/** The headline diagnosis on an episode row. Disease name and code only. */
export type HistoryPrimaryDiagnosis = {
  name: string | null;
  code: string | null;
  certainty: string | null;
  severity: string | null;
};

/** One prior episode, as the History worklist scans it. */
export type HistoryVisit = {
  /**
   * The ONLY use of this value is addressing
   * /history/[historicalAppointmentId] and its image route. It is never shown.
   */
  appointment_id: number | null;
  encounter_id: number;
  encounter_code: string | null;
  encounter_type: string | null;
  encounter_type_label: string | null;
  /** Date only (YYYY-MM-DD). The episode's opened_at, day-resolved. */
  date: string | null;
  /** Display name only. No id, no login, no email. */
  doctor: string | null;
  department: string | null;
  chief_complaint: string | null;
  primary_diagnosis: HistoryPrimaryDiagnosis | null;
  status: HistoryVisitStatus | null;
  status_label: string | null;
  counts: HistoryVisitCounts;
};

/** The history summary envelope. */
export type DoctorHistoryResponse = {
  patient: HistoryPatient | null;
  visits: HistoryVisit[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

/* ------------------------------------------------------------------ *
 * Detail
 * ------------------------------------------------------------------ */

/** Identity and provenance for the opened episode. */
export type HistoryVisitHeader = {
  appointment_id: number | null;
  encounter_id: number;
  encounter_code: string | null;
  consultation_code: string | null;
  encounter_type: string | null;
  encounter_type_label: string | null;
  date: string | null;
  opened_at: string | null;
  completed_at: string | null;
  doctor: string | null;
  department: string | null;
  status: HistoryVisitStatus | null;
  status_label: string | null;
};

/**
 * The triage snapshot.
 *
 * EVERY VITAL IS `number | null`, AND null MEANS NOT RECORDED. The underlying
 * columns are plain Floats with no null sentinel, so the server converts the
 * unrecorded zero to null before it leaves Odoo. The screen must therefore
 * never coalesce a null to 0 -- doing so would invent a measurement that would
 * be physiologically impossible in a living patient.
 */
export type HistoryTriage = {
  chief_complaint: string | null;
  triage_priority: string | null;
  triage_priority_label: string | null;
  recorded_at: string | null;
  systolic_bp: number | null;
  diastolic_bp: number | null;
  heart_rate: number | null;
  temperature: number | null;
  respiratory_rate: number | null;
  spo2: number | null;
  bmi: number | null;
  bmi_state: string | null;
  weight: number | null;
  height: number | null;
  pain_level: string | null;
};

/**
 * The physician's narrative for a prior episode.
 *
 * DoctorConsultation minus its live-workflow affordances; see the module
 * header. The narrative fields themselves are reused from the consultation
 * contract so a field added to the note cannot be silently missing here.
 */
export type HistoryNote = {
  id: number;
  name: string;
  state: ConsultationState;
  started_at: string | null;
  completed_at: string | null;
} & Record<ConsultationNarrativeField, string | null>;

/**
 * Prior laboratory and radiology, reusing the Results contracts MINUS the one
 * key the server strips. See the module header.
 */
export type HistoryLaboratoryReview = Omit<LaboratoryReview, "billing_blocked">;
export type HistoryRadiologyReview = Omit<RadiologyReview, "billing_blocked">;

/**
 * A prior prescription.
 *
 * `editable` and `cancellable` are both forced false by the server. They are
 * kept in the type rather than omitted precisely so the read-only contract test
 * can assert they are false: a field that is absent cannot be checked.
 */
export type HistoryPrescription = DoctorPrescription;

/** A prior diagnosis. `editable` is false for every row. */
export type HistoryDiagnosis = DoctorDiagnosis;

/** One prior episode, in full. */
export type DoctorHistoryDetailResponse = {
  visit: HistoryVisitHeader;
  triage: HistoryTriage | null;
  note: HistoryNote | null;
  diagnoses: HistoryDiagnosis[];
  medications: HistoryPrescription[];
  laboratory: HistoryLaboratoryReview[];
  radiology: HistoryRadiologyReview[];
};

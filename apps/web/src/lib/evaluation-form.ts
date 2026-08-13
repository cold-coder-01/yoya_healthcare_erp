/**
 * Shared triage/evaluation form logic.
 *
 * Extracted from app/triage/[appointmentId]/evaluation-client.tsx so the Front
 * Desk workstation and the legacy /triage screen build the SAME save payload.
 * Only the form logic moved; the legacy component keeps its own layout, its own
 * billing banner and its own start-consultation flow.
 *
 * Everything here is deliberately shape-agnostic about where the evaluation
 * came from: /triage reads a flat ClinicalEvaluation, the front desk reads a
 * FrontDeskVisit whose vitals are nested, and both normalise into
 * EvaluationFormSource before calling formFromEvaluation().
 */
import type { EvaluationSavePayload } from "@/types/clinical";

export type FormState = {
  weight: string;
  height: string;
  temperature: string;
  heart_rate: string;
  respiratory_rate: string;
  systolic_bp: string;
  diastolic_bp: string;
  spo2: string;
  rbs: string;
  head_circumference: string;
  pain_level: string;
  pain_note: string;
  triage_priority: string;
  chief_complaint: string;
  triage_notes: string;
  physician_id: string;
  // assigned_nurse_id is deliberately NOT part of the form: the server derives
  // it from env.user. See buildPayload().
};

export const EMPTY_FORM: FormState = {
  weight: "",
  height: "",
  temperature: "",
  heart_rate: "",
  respiratory_rate: "",
  systolic_bp: "",
  diastolic_bp: "",
  spo2: "",
  rbs: "",
  head_circumference: "",
  pain_level: "",
  pain_note: "",
  triage_priority: "",
  chief_complaint: "",
  triage_notes: "",
  physician_id: "",
};

export const NUMERIC_FIELDS = [
  "weight",
  "height",
  "temperature",
  "heart_rate",
  "respiratory_rate",
  "systolic_bp",
  "diastolic_bp",
  "spo2",
  "rbs",
  "head_circumference",
] as const;

export type NumericField = (typeof NUMERIC_FIELDS)[number];

/**
 * Backend ranges, mirrored from yoya_clinical_bridge VITAL_RANGES.
 *
 * UX only. Odoo re-checks every one of these in _check_vital_sign_ranges and
 * remains the authority; these just let the nurse catch a slipped decimal
 * before a round trip. rbs and head_circumference are absent here because the
 * model does not range-check them either.
 */
export const VITAL_RANGES: Partial<
  Record<NumericField, { min: number; max: number }>
> = {
  temperature: { min: 25, max: 45 },
  heart_rate: { min: 20, max: 300 },
  respiratory_rate: { min: 4, max: 80 },
  systolic_bp: { min: 40, max: 300 },
  diastolic_bp: { min: 20, max: 200 },
  spo2: { min: 0, max: 100 },
  weight: { min: 0, max: 500 },
  height: { min: 0, max: 250 },
};

export function valueToString(value: string | number | null | undefined) {
  return value === null || value === undefined ? "" : String(value);
}

/** The flat shape formFromEvaluation reads. Both callers normalise into it. */
export type EvaluationFormSource = {
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
  triage_priority?: string | null;
  chief_complaint?: string | null;
  triage_notes?: string | null;
  physician_id?: { id: number } | null;
};

/**
 * `fallbackPhysicianId` is the appointment's own doctor. It seeds the picker
 * for an evaluation that has not recorded a physician yet -- typically a brand
 * new one -- so the clinician who is actually booked for the visit is
 * pre-selected instead of the field opening empty.
 */
export function formFromEvaluation(
  evaluation: EvaluationFormSource | null | undefined,
  fallbackPhysicianId?: number | null,
): FormState {
  if (!evaluation) {
    return {
      ...EMPTY_FORM,
      physician_id: valueToString(fallbackPhysicianId ?? undefined),
    };
  }

  return {
    weight: valueToString(evaluation.weight),
    height: valueToString(evaluation.height),
    temperature: valueToString(evaluation.temperature),
    heart_rate: valueToString(evaluation.heart_rate),
    respiratory_rate: valueToString(evaluation.respiratory_rate),
    systolic_bp: valueToString(evaluation.systolic_bp),
    diastolic_bp: valueToString(evaluation.diastolic_bp),
    spo2: valueToString(evaluation.spo2),
    rbs: valueToString(evaluation.rbs),
    head_circumference: valueToString(evaluation.head_circumference),
    pain_level: valueToString(evaluation.pain_level),
    pain_note: valueToString(evaluation.pain_note),
    triage_priority: valueToString(evaluation.triage_priority),
    chief_complaint: valueToString(evaluation.chief_complaint),
    triage_notes: valueToString(evaluation.triage_notes),
    physician_id: valueToString(
      evaluation.physician_id?.id ?? fallbackPhysicianId ?? undefined,
    ),
  };
}

/**
 * A Float field reads 0.0 from Odoo when it was never recorded, and the front
 * desk serializer forwards that as a literal 0. Showing "0" in a vitals box
 * claims a measurement that was never taken, so an unrecorded numeric is
 * normalised back to an empty input.
 *
 * Deliberately NOT applied to pain_level, which is a Selection whose "0" means
 * a real answer: no pain.
 */
export function blankIfUnrecorded(value: number | null | undefined) {
  return value === null || value === undefined || value === 0 ? null : value;
}

export type BuildPayloadOptions = {
  /**
   * Whether physician_id belongs in this save.
   *
   * /triage sends it: the picker is part of its form. The FRONT DESK does not.
   * There, the doctor is assigned through
   * POST /api/front-desk/visits/<id>/doctor, which is the only path that also
   * syncs appointment.doctor_id and encounter.primary_doctor_id. Letting the
   * save payload write physician_id as well would give the doctor two write
   * paths, and the evaluation-only one would silently leave the appointment and
   * encounter pointing at the previous doctor.
   */
  includePhysician?: boolean;
};

export function buildPayload(
  form: FormState,
  options: BuildPayloadOptions = {},
): EvaluationSavePayload {
  const { includePhysician = true } = options;

  const payload: EvaluationSavePayload = {
    pain_level: form.pain_level || null,
    pain_note: form.pain_note || null,

    chief_complaint: form.chief_complaint || null,
    triage_notes: form.triage_notes || null,
    // assigned_nurse_id is intentionally OMITTED, not sent as null.
    //
    // hospital.patient.evaluation.assigned_nurse_id carries
    // `default=lambda self: self.env.user`, and Odoo applies a default only
    // when the key is ABSENT from vals. Sending an explicit null therefore
    // suppressed that default and created evaluations with no nurse assigned
    // -- which, for an appointment-less evaluation, is the only thing the
    // nurse record rule matches on. The server derives the identity from the
    // authenticated session; the browser must not supply it.
  };

  if (includePhysician) {
    payload.physician_id = form.physician_id ? Number(form.physician_id) : null;
  }

  if (form.triage_priority) {
    payload.triage_priority = form.triage_priority;
  }

  for (const field of NUMERIC_FIELDS) {
    const raw = form[field].trim();
    if (raw) {
      payload[field] = Number(raw);
    }
  }

  return payload;
}

/**
 * THE Radiology Desk wire contract.
 *
 * Emitted directly by yoya_emr_api's rad_desk serializer; the BFF forwards it
 * unchanged, with no adapter and no reshaping.
 *
 * WHY THIS IS NOT types/doctor-radiology.ts OR types/doctor-results.ts. Those
 * are the DOCTOR's contracts: a clinician sees a report only once released, and
 * the whole imaging workflow collapses to "pending" or "available". The imaging
 * department works the stages in between -- what to schedule, what awaits a
 * report, what awaits validation -- and reads unreleased report text because
 * writing it is its job. Sharing a type would force one screen to lie.
 *
 * NOTHING PRICED APPEARS IN THIS FILE. The ONE billing-derived value is
 * `billing_blocked`, a boolean. No amount, payer, invoice, receipt or charge,
 * and never the cashier-facing clearance message.
 *
 * TWO ACTIONS (Slice 2): schedule a study and start an exam. Both are bodiless
 * POSTs; the server re-checks everything under a row lock and answers with the
 * request re-serialized AFTER the transition. No other action exists.
 */
import type { ApiEnvelope, Many2OneValue } from "./clinical";

export type { ApiEnvelope, Many2OneValue };

/** hospital.radiology.request.state: all six authoritative values. */
export const RAD_REQUEST_STATES = [
  "draft",
  "requested",
  "scheduled",
  "in_progress",
  "completed",
  "cancelled",
] as const;
export type RadRequestState = (typeof RAD_REQUEST_STATES)[number];

/** hospital.radiology.result.state: all five authoritative values. */
export const RAD_RESULT_STATES = [
  "draft",
  "entered",
  "validated",
  "released",
  "cancelled",
] as const;

/**
 * The desk's lanes, DERIVED server-side and never sent back to Odoo.
 *
 * `draft` is not a lane anyone may ask for; it only appears on a detail payload
 * for a draft request opened by id, so it is typed separately below.
 */
export const RAD_DESK_LANES = [
  "awaiting_clearance",
  "to_schedule",
  "ready_to_start",
  "awaiting_report",
  "awaiting_validation",
  "awaiting_release",
  "anomaly",
  "completed",
  "cancelled",
] as const;
export type RadDeskLane = (typeof RAD_DESK_LANES)[number];

/** The default queue. Mirrors RAD_DESK_ACTIVE_LANES in the Odoo serializer. */
export const RAD_DESK_ACTIVE_LANES = [
  "awaiting_clearance",
  "to_schedule",
  "ready_to_start",
  "awaiting_report",
  "awaiting_validation",
  "awaiting_release",
  "anomaly",
] as const;

/** Why a request is in the anomaly lane. */
export type RadAnomalyReason =
  | "result_conflict"
  | "released_not_completed"
  | "unknown_state";

export type RadPatientIdentity = {
  id: number;
  name: string;
  /** hospital.patient.identification_code -- the chart number. */
  mrn: string | null;
  age: number | null;
  gender: string | null;
};

export type RadExamRef = { id: number; name: string; code: string | null };

/** What a queue row says about the one operational report. Never its text. */
export type RadResultSummary = {
  id: number;
  name: string;
  state: string;
  state_label: string | null;
  result_date: string | null;
  /** Display name only. */
  radiologist: string | null;
  /** Whether anyone wrote anything -- a released report can be empty. */
  has_report: boolean;
  image_count: number;
};

/** One Radiology Desk queue row. */
export type RadQueueRow = {
  id: number;
  request_code: string;
  /** The authoritative state, alongside the derived lane. */
  state: string;
  lane: string;
  /** The server's own wording. The client styles by `lane`, displays this. */
  lane_label: string;
  anomaly_reason: RadAnomalyReason | null;
  priority: string | null;
  priority_label: string;
  request_date: string | null;
  created_at: string | null;
  /** A boolean verdict from hospital_billing. Never an amount. */
  billing_blocked: boolean;
  active: boolean;
  patient: RadPatientIdentity | null;
  ordering_physician: Many2OneValue;
  exam_count: number;
  first_exam: RadExamRef | null;
  modality: string | null;
  modality_label: string | null;
  body_part: string | null;
  exams_summary: string | null;
  /** Null when there is no operational report AND when there is a conflict. */
  result: RadResultSummary | null;
  /** More than one active, non-cancelled report: none is authoritative. */
  result_conflict: boolean;
  result_count: number;
};

export type RadOrderedExam = {
  request_line_id: number;
  exam_id: number;
  code: string | null;
  name: string;
  modality: string | null;
  modality_label: string | null;
  body_part: string | null;
  contrast_required: boolean;
  special_instruction: string | null;
  state: string;
  sequence: number;
};

export type RadResultLine = {
  id: number;
  request_line_id: number | null;
  exam: RadExamRef;
  modality: string | null;
  modality_label: string | null;
  body_part: string | null;
  contrast_used: boolean;
  result_summary: string | null;
  notes: string | null;
};

/** METADATA ONLY. There is no URL and no byte field, by design. */
export type RadImageMetadata = {
  id: number;
  name: string;
  caption: string | null;
  image_type: string;
  image_type_label: string | null;
  filename: string;
  mimetype: string | null;
  file_size: number;
  uploaded_by: string | null;
  uploaded_at: string | null;
  sequence: number;
};

/** The one operational report, with its text, for the desk that writes it. */
export type RadOperationalResult = RadResultSummary & {
  findings: string | null;
  impression: string | null;
  recommendations: string | null;
  lines: RadResultLine[];
  images: RadImageMetadata[];
};

/** The detail panel's shape: the queue row plus the request in full. */
export type RadRequestDetail = Omit<RadQueueRow, "result"> & {
  clinical_indication: string | null;
  instructions: string | null;
  completed_at: string | null;
  /** Whether the Doctor Desk placed this order. Nothing else about it. */
  ordered_from_consultation: boolean;
  exams: RadOrderedExam[];
  cancelled_exam_count: number;
  result: RadOperationalResult | null;
  /** The server's sentence for an anomaly, or null. */
  review_message: string | null;
};

export type RadDeskCapabilities = {
  radiology_desk: boolean;
  /** The ROLE may attempt the act. Whether one request may is its own lane. */
  schedule_study: boolean;
  start_exam: boolean;
};

/**
 * What POST .../schedule and .../start return: the request as it stands after
 * the transition. The same shape as the detail read, so the panel renders it
 * directly.
 */
export type RadTransitionResponse = RadRequestResponse;

/** The two transitions this desk performs. */
export type RadTransitionKind = "schedule" | "start";

export type RadDeskRoles = {
  radiology_technician: boolean;
  radiologist: boolean;
  manager: boolean;
  system_admin: boolean;
};

/**
 * Lane counts over the whole filter scope -- date, search and modality -- and
 * NOT the page or the selected lane (`meta.summary_filters` says which).
 *
 * The live work lanes are `number | null`: past the server's scan cap they come
 * back null with `meta.summary_exact === false`. Render a dash, never a guess.
 */
export type RadWorklistSummary = {
  active: number | null;
  awaiting_clearance: number | null;
  to_schedule: number | null;
  ready_to_start: number | null;
  awaiting_report: number | null;
  awaiting_validation: number | null;
  awaiting_release: number | null;
  anomaly: number | null;
  completed: number;
  cancelled: number;
  work_total: number;
};

export type RadModalityOption = { value: string; label: string };

export type RadWorklistResponse = {
  rows: RadQueueRow[];
  summary: RadWorklistSummary;
  filters: {
    date: string | null;
    status: string[];
    q: string | null;
    modality: string | null;
    limit: number;
  };
  meta: {
    /** Rows on THIS page -- not a lane total. */
    row_count: number;
    truncated: boolean;
    lanes: string[];
    default_lanes: string[];
    summary_exact: boolean;
    summary_filters: string[];
    modalities: RadModalityOption[];
  };
  capabilities: RadDeskCapabilities;
};

export type RadRequestResponse = {
  request: RadRequestDetail;
  capabilities: RadDeskCapabilities;
};

export type RadSessionResponse = {
  user: { id: number; name: string };
  company: Many2OneValue;
  roles: RadDeskRoles;
  capabilities: RadDeskCapabilities;
};

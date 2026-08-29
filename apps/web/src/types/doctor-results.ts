/**
 * The Doctor Results contract.
 *
 * MIRRORS yoya_emr_api/services/result_serializers.py EXACTLY. Every field
 * here is one the serializer emits; nothing is invented on this side, and
 * nothing clinical is derived from anything else.
 *
 * WHAT IS ABSENT IS PART OF THE CONTRACT. There is no `reviewed`, `seen`,
 * `acknowledged` or `signed` anywhere below, because no model records that a
 * doctor has read a result -- a field here would let the desk assert something
 * no record supports. There is likewise no `can_*` or `editable` flag: the
 * Results tab performs no act, so there is nothing to permit.
 */

/**
 * The Results-level status. THREE keys, each backed by real state.
 *
 * `available` means a RELEASED result exists -- never that the request reached
 * `completed`. A released result can sit on a request still marked in_progress,
 * and the server decides this, not the client.
 */
export type ResultStatus = "pending" | "available" | "cancelled";

/**
 * The laboratory's own abnormality judgement, passed through verbatim.
 *
 * The client NEVER computes this. `value` and `reference_range` are free text
 * on the model ("< 0.01", "Negative", "70/100"), so deriving high/low here
 * would be a second opinion with no clinician behind it.
 */
export type AbnormalFlag = "normal" | "low" | "high" | "critical" | "abnormal";

/** An ordered test still waiting on the bench. */
export type PendingTest = {
  id: number;
  test_id: number;
  name: string;
  code: string | null;
  sample_type: string | null;
};

/** One reported test inside a released laboratory result. */
export type ResultLine = {
  id: number;
  test_id: number;
  name: string;
  code: string | null;
  sample_type: string | null;
  /** VERBATIM from the bench. Never parsed as a number. */
  value: string | null;
  unit: string | null;
  /** VERBATIM. "70/100" is a real shipped value; it is not an interval. */
  reference_range: string | null;
  abnormal_flag: AbnormalFlag | null;
  abnormal_flag_label: string | null;
  notes: string | null;
  /**
   * The ordered test this reports on. `null` with `ordered: false` means the
   * server could not resolve the linkage without guessing -- the value is
   * still shown, the claim about WHICH order it answers is withheld.
   */
  request_line_id: number | null;
  ordered: boolean;
};

export type LaboratoryResult = {
  id: number;
  result_code: string;
  result_date: string | null;
  interpretation: string | null;
  remarks: string | null;
  lines: ResultLine[];
};

export type LaboratoryReview = {
  request_id: number;
  request_code: string;
  priority: string | null;
  status: ResultStatus;
  status_label: string;
  /** The request's own workflow wording, as SECONDARY context only. */
  workflow_status: string | null;
  clinical_indication: string | null;
  diagnosis: { id: number; name: string; code: string | null } | null;
  ordered_at: string | null;
  created_at: string | null;
  /** A bare boolean, pending side only. Never an amount. */
  billing_blocked: boolean;
  result: LaboratoryResult | null;
  /** Older released results, counted rather than dropped. */
  superseded_count: number;
  pending_tests: PendingTest[];
};

export type PendingExam = {
  id: number;
  exam_id: number;
  name: string;
  code: string | null;
  modality: string | null;
  modality_label: string | null;
  body_part: string | null;
  contrast_required: boolean;
};

export type ReportedExam = {
  id: number;
  exam_id: number;
  name: string;
  code: string | null;
  modality: string | null;
  modality_label: string | null;
  body_part: string | null;
  contrast_used: boolean;
  summary: string | null;
  notes: string | null;
  request_line_id: number | null;
  ordered: boolean;
};

/**
 * One clinical file attached to a released radiology report.
 *
 * THERE IS NO URL FIELD, DELIBERATELY. The client builds its own BFF path from
 * `id`; a URL in the payload is exactly where an Odoo origin, a /web/content
 * path or an access token would reach the browser. `id` is the
 * hospital.radiology.image id -- never the ir.attachment id, which is a
 * database-wide file handle this contract has no business naming.
 */
export type RadiologyImage = {
  id: number;
  name: string;
  caption: string | null;
  /** What to RENDER, derived server-side from the sniffed mimetype. */
  kind: "image" | "pdf";
  mimetype: string | null;
  filename: string;
  file_size: number;
  sequence: number;
};

export type RadiologyResult = {
  id: number;
  result_code: string;
  result_date: string | null;
  /** Display name only. No id, no login, no email. */
  radiologist: string | null;
  findings: string | null;
  impression: string | null;
  recommendations: string | null;
  /**
   * False for a RELEASED report carrying no narrative and no line summary.
   * Radiology has no completeness constraint, so this state is reachable and
   * the database already holds one; the card must say so rather than render
   * blank space.
   */
  has_report: boolean;
  exams: ReportedExam[];
  /**
   * Attached imaging, in a deterministic (sequence, id) order so a lightbox's
   * "2 of 3" means the same thing on every load.
   *
   * OPTIONAL ON THE WIRE. A desk deployed against an Odoo that predates Slice
   * 8B receives a payload without these keys, and a Results tab that crashed
   * on a released report would be a worse failure than one that shows no
   * imaging section. Every read below goes through a helper that defaults.
   */
  images?: RadiologyImage[];
  image_count?: number;
};

export type RadiologyReview = {
  request_id: number;
  request_code: string;
  priority: string | null;
  status: ResultStatus;
  status_label: string;
  workflow_status: string | null;
  clinical_indication: string | null;
  diagnosis: { id: number; name: string; code: string | null } | null;
  ordered_at: string | null;
  created_at: string | null;
  billing_blocked: boolean;
  result: RadiologyResult | null;
  superseded_count: number;
  pending_exams: PendingExam[];
};

export type DoctorResultsResponse = {
  laboratory: LaboratoryReview[];
  radiology: RadiologyReview[];
};

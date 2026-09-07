/**
 * THE Doctor radiology wire contract.
 *
 * Emitted directly by yoya_emr_api's radiology serializer; the BFF forwards it
 * unchanged, with no adapter and no reshaping.
 *
 * NOTHING PRICED APPEARS HERE, for the same reason it does not in
 * doctor-laboratory: confirming an order raises one charge per study through
 * hospital_billing, and the only billing-derived value that crosses the
 * boundary is a status key. `awaiting_clearance` means the desk is waiting on
 * the patient or payer, and carries no amount, no payer and no allocation.
 *
 * NOR DOES ANY REPORT. A released radiology result is a narrative -- findings,
 * impression, recommendations -- and this slice exposes only `has_result`, a
 * boolean saying one exists. Reading it is a later slice with its own endpoint.
 *
 * `code` IS NOT VALIDATED as any coding standard. hospital.radiology.exam.code
 * is a free Char, so this layer does not assert a guarantee the schema does not
 * make.
 */
import type { ApiEnvelope } from "./doctor";

export type { ApiEnvelope };

/** hospital.radiology.request.priority. */
export const RAD_PRIORITIES = ["routine", "urgent", "stat"] as const;
export type RadPriority = (typeof RAD_PRIORITIES)[number];

/**
 * The clinical status vocabulary, DERIVED from real backend state by the
 * serializer -- never invented here.
 *
 * Six workflow states map to seven keys because TWO of them mean something
 * different to a doctor depending on whether the encounter has cleared
 * financially. Radiology raises its charges at confirmation but does not reach
 * its clearance gate until Mark In Progress, so the unpaid window spans both
 * `requested` and `scheduled` -- which is why `awaiting_clearance` can appear
 * in place of either `awaiting_scheduling` or `scheduled`.
 */
export const RAD_STATUSES = [
  "draft",
  "awaiting_clearance",
  "awaiting_scheduling",
  "scheduled",
  "in_progress",
  "result_available",
  "cancelled",
] as const;
export type RadStatus = (typeof RAD_STATUSES)[number];

/** hospital.radiology.exam.modality. */
export const RAD_MODALITIES = [
  "xray",
  "ultrasound",
  "ct",
  "mri",
  "fluoroscopy",
  "mammography",
  "other",
] as const;

export type RadExamOption = {
  id: number;
  name: string;
  code: string | null;
  modality: string | null;
  body_part: string | null;
  contrast_required: boolean;
};

export type OrderedRadExam = {
  /** The request LINE id. */
  id: number;
  exam_id: number;
  name: string;
  code: string | null;
  modality: string | null;
  body_part: string | null;
  contrast_required: boolean;
  special_instruction: string | null;
};

export type RadOrderDiagnosis = {
  id: number;
  name: string;
  code: string | null;
};

export type DoctorRadOrder = {
  id: number;
  request_code: string;
  exams: OrderedRadExam[];
  diagnosis: RadOrderDiagnosis | null;
  clinical_indication: string | null;
  instructions: string | null;
  priority: string | null;
  status: string;
  status_label: string;
  ordered_at: string | null;
  created_at: string | null;
  /** A report EXISTS. Its contents belong to a later slice. */
  has_result: boolean;
  /** Always false: the ordered set freezes when the request leaves draft. */
  editable: boolean;
  cancellable: boolean;
};

export type DoctorRadOrderResponse = {
  orders: DoctorRadOrder[];
  /** The consultation is open, so new orders may still be placed. */
  can_order: boolean;
};

export type RadCatalogueResponse = {
  exams: RadExamOption[];
  query: string | null;
  limit: number;
  truncated: boolean;
};

/** What the order form holds before submission. */
export type RadOrderForm = {
  priority: RadPriority;
  clinical_indication: string;
  instructions: string;
  diagnosis_id: number | null;
};

export type RadOrderRequest = {
  exams: number[];
  request_token: string;
  priority?: string;
  clinical_indication?: string;
  instructions?: string;
  diagnosis_id?: number;
};

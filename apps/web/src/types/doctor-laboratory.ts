/**
 * THE Doctor laboratory wire contract.
 *
 * Emitted directly by yoya_emr_api's laboratory serializer; the BFF forwards it
 * unchanged, with no adapter and no reshaping.
 *
 * A LAB ORDER IS THE MOST BILLING-ADJACENT THING A DOCTOR DOES, which is
 * exactly why nothing priced appears in this file. Confirming an order raises
 * one charge per test through hospital_billing, and the only billing-derived
 * value that crosses the boundary is a status key: `awaiting_clearance` means
 * the desk is waiting on the patient or payer, and carries no amount, no payer
 * and no allocation.
 *
 * `code` IS NOT VALIDATED as any coding standard. hospital.laboratory.test.code
 * is a free Char, so this layer does not assert a guarantee the schema does not
 * make.
 */
import type { ApiEnvelope } from "./doctor";

export type { ApiEnvelope };

/** hospital.laboratory.request.priority. */
export const LAB_PRIORITIES = ["routine", "urgent", "stat"] as const;
export type LabPriority = (typeof LAB_PRIORITIES)[number];

/**
 * The clinical status vocabulary, DERIVED from real backend state by the
 * serializer -- never invented here. Six workflow states map to seven keys
 * because `requested` means two different things to a doctor depending on
 * whether the encounter has cleared financially.
 */
export const LAB_STATUSES = [
  "draft",
  "awaiting_clearance",
  "ready_for_collection",
  "collected",
  "result_pending",
  "result_available",
  "cancelled",
] as const;
export type LabStatus = (typeof LAB_STATUSES)[number];

export type LabTestOption = {
  id: number;
  name: string;
  code: string | null;
  category: string | null;
  sample_type: string | null;
};

export type OrderedLabTest = {
  /** The request LINE id. */
  id: number;
  test_id: number;
  name: string;
  code: string | null;
  sample_type: string | null;
};

export type LabOrderDiagnosis = {
  id: number;
  name: string;
  code: string | null;
};

export type DoctorLabOrder = {
  id: number;
  request_code: string;
  tests: OrderedLabTest[];
  diagnosis: LabOrderDiagnosis | null;
  clinical_indication: string | null;
  priority: string | null;
  status: string;
  status_label: string;
  ordered_at: string | null;
  created_at: string | null;
  /** Always false: the ordered set freezes when the request leaves draft. */
  editable: boolean;
  cancellable: boolean;
};

export type DoctorLabOrderResponse = {
  orders: DoctorLabOrder[];
  /** The consultation is open, so new orders may still be placed. */
  can_order: boolean;
};

export type LabCatalogueResponse = {
  tests: LabTestOption[];
  query: string | null;
  limit: number;
  truncated: boolean;
};

/** What the order form holds before submission. */
export type LabOrderForm = {
  priority: LabPriority;
  clinical_notes: string;
  diagnosis_id: number | null;
};

export type LabOrderRequest = {
  tests: number[];
  request_token: string;
  priority?: string;
  clinical_notes?: string;
  diagnosis_id?: number;
};

/**
 * The ORDERS sub-sections. Only laboratory ships in this slice; the rest are
 * declared so the workstation's shape is visible, and are rendered as inert
 * text rather than as controls that would swallow a click.
 */
export const ORDER_KINDS = [
  { key: "laboratory", label: "Laboratory", live: true },
  { key: "radiology", label: "Radiology", live: false },
  { key: "medication", label: "Medication", live: false },
  { key: "procedure", label: "Procedure", live: false },
] as const;

export type OrderKind = (typeof ORDER_KINDS)[number]["key"];

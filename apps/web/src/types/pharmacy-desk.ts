/**
 * THE Pharmacy Desk wire contract (Pharmacy Slice 1, READ ONLY).
 *
 * Emitted directly by yoya_emr_api's pharmacy_desk_serializers; the BFF
 * forwards it unchanged, with no adapter and no reshaping.
 *
 * WHY THIS IS NOT types/doctor-medication.ts. That is the prescriber's view: one
 * coarse status per prescription. The counter works the stages in between --
 * what to prepare, what waits on clearance, what can be handed over, what is
 * stuck -- and needs per-line quantities the prescriber never sees.
 *
 * NOTHING PRICED APPEARS IN THIS FILE. The billing-derived values are booleans:
 * `billing_blocked` on the dispense, `billing_mapped` and `charge_linked` on a
 * line. Stock crosses as verdicts too: `inventory_mapped`, `stock_sufficient`.
 *
 * NO PHARMACIST PROVENANCE. The record does not reliably know who handed
 * medication over, so the contract does not claim it.
 */
import type { ApiEnvelope } from "./clinical";

export type { ApiEnvelope };

/** hospital.pharmacy.dispense.state: the five authoritative values. */
export const PHARMACY_DISPENSE_STATES = [
  "draft",
  "ready",
  "partial",
  "dispensed",
  "cancelled",
] as const;

/** The default queue: live work, in the order it flows. */
export const PHARMACY_DESK_ACTIVE_LANES = [
  "awaiting_preparation",
  "awaiting_clearance",
  "ready_to_validate",
  "partially_supplied",
  "blocked",
  "anomaly",
] as const;

/** Every lane a caller may ask for. */
export const PHARMACY_DESK_LANES = [
  "awaiting_preparation",
  "awaiting_clearance",
  "ready_to_validate",
  "partially_supplied",
  "blocked",
  "anomaly",
  "completed",
  "cancelled",
] as const;
export type PharmacyDeskLane = (typeof PHARMACY_DESK_LANES)[number];

export type PharmacyDeskRoles = {
  pharmacist: boolean;
  manager: boolean;
  system_admin: boolean;
};

/**
 * What the ROLE may attempt (Slice 2). Whether ONE dispense may be prepared or
 * validated is that record's own `can_prepare` / `can_validate`.
 */
export type PharmacyDeskCapabilities = {
  pharmacy_desk: boolean;
  prepare_dispense: boolean;
  validate_dispense: boolean;
};

export type PharmacySessionResponse = {
  user: { id: number; name: string };
  company: { id: number; name: string } | null;
  roles: PharmacyDeskRoles;
  capabilities: PharmacyDeskCapabilities;
  read_only: boolean;
};

export type PharmacyPatient = {
  id: number;
  name: string;
  mrn: string | null;
  age: number | null;
  gender: string | null;
};

export type PharmacyPrescriptionSummary = {
  id: number;
  code: string;
  state: string;
  state_label: string | null;
  date: string | null;
};

export type PharmacyPrescriber = { id: number; name: string };

export type PharmacyQueueRow = {
  id: number;
  dispense_code: string;
  /** The AUTHORITATIVE dispense state, beside the derived lane. */
  state: string;
  state_label: string | null;
  lane: string;
  lane_label: string;
  /** A fixed code: a blocker in `blocked`, an anomaly in `anomaly`. */
  reason: string | null;
  /** The server's fixed, amount-free sentence for `reason`. */
  reason_message: string | null;
  priority: string;
  priority_label: string;
  dispense_date: string | null;
  patient: PharmacyPatient | null;
  prescription: PharmacyPrescriptionSummary | null;
  prescriber: PharmacyPrescriber | null;
  /** A BOOLEAN verdict. Never an amount. */
  billing_blocked: boolean;
  stock_short: boolean;
  line_count: number;
  lines_complete: number;
  medicines_summary: string | null;
  /** Incremented once per successful Prepare or Validate. Sent back as expected_revision. */
  workflow_revision: number;
  /** Derived server-side from state, lane, mappings, billing and stock. */
  can_prepare: boolean;
  can_validate: boolean;
};

export type PharmacyMedicine = {
  id: number;
  name: string;
  code: string | null;
  strength: string | null;
  dosage_form: string | null;
  dosage_form_label: string | null;
};

export type PharmacyDispenseLine = {
  id: number;
  medicine: PharmacyMedicine | null;
  dosage: string | null;
  frequency: string | null;
  duration: string | null;
  route: string | null;
  instruction: string | null;
  prescribed_quantity: number;
  /** The pharmacist's CUMULATIVE intended quantity. */
  intended_quantity: number;
  /** Quantity whose delivery billing recorded. A quantity, not money. */
  delivered_quantity: number;
  consumed_quantity: number;
  remaining_quantity: number;
  pending_increment: number;
  /** The least cumulative intent the server accepts: what was already supplied. */
  minimum_intended_quantity: number;
  billing_mapped: boolean;
  charge_linked: boolean;
  inventory_mapped: boolean;
  stock_basis: "increment" | "remaining" | null;
  stock_sufficient: boolean | null;
};

export type PharmacyDispenseDetail = PharmacyQueueRow & {
  notes: string | null;
  ordered_from_consultation: boolean;
  unified_billing: boolean;
  pharmacy_store_configured: boolean;
  lines: PharmacyDispenseLine[];
};

/** Lane counts over the whole filter scope. `null` = could not be determined. */
export type PharmacyWorklistSummary = Record<PharmacyDeskLane | "active" | "total", number | null>;

export type PharmacyWorklistResponse = {
  rows: PharmacyQueueRow[];
  summary: PharmacyWorklistSummary;
  filters: {
    date: string | null;
    status: string[];
    q: string | null;
    limit: number;
  };
  meta: {
    row_count: number;
    truncated: boolean;
    summary_exact: boolean;
    lanes: string[];
    default_lanes: string[];
    summary_filters: string[];
  };
  capabilities: PharmacyDeskCapabilities;
};

export type PharmacyDispenseResponse = {
  dispense: PharmacyDispenseDetail;
  capabilities: PharmacyDeskCapabilities;
};


/* ------------------------------------------------------------------ *
 * Mutations (Slice 2)
 * ------------------------------------------------------------------ */

export type PharmacyPrepareRequest = {
  operation_token: string;
  expected_revision: number;
  lines: { line_id: number; intended_quantity: number }[];
};

export type PharmacyValidateRequest = {
  operation_token: string;
  expected_revision: number;
};

export type PharmacyMutationResponse = {
  dispense: PharmacyDispenseDetail;
  capabilities: PharmacyDeskCapabilities;
  workflow_revision: number;
  operation: { type: "prepare" | "validate"; token: string; replayed: boolean };
};

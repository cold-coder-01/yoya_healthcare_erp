/**
 * THE Laboratory Desk wire contract.
 *
 * Emitted directly by yoya_emr_api's lab_desk serializer; the BFF forwards it
 * unchanged, with no adapter and no reshaping.
 *
 * WHY THIS IS NOT types/doctor-laboratory.ts. That file is the DOCTOR's view
 * of an order they placed, and its vocabulary collapses the bench's own stages
 * (`in_progress` becomes `result_pending`, `completed` becomes
 * `result_available`) because a clinician wants to know whether a value has
 * come back. To a technician those are three different piles of work. Sharing
 * one type would force one of the two screens to lie, so the contracts are
 * separate and a server-side test pins each independently.
 *
 * NOTHING PRICED APPEARS IN THIS FILE. A laboratory order is billing-adjacent
 * by nature -- confirming one raises a charge per test -- and the ONE
 * billing-derived value that crosses the boundary is `billing_blocked`, a
 * boolean. No amount, no payer, no receipt, no allocation, and deliberately
 * not `billing_clearance_message`, which is written for a cashier and can name
 * sums.
 */
import type { ApiEnvelope, Many2OneValue } from "./clinical";

export type { ApiEnvelope, Many2OneValue };

/**
 * hospital.laboratory.request.state. THE AUTHORITATIVE VALUES, all six of
 * them, restated here so a client can reason about the workflow without
 * inferring it from the display status.
 */
export const LAB_REQUEST_STATES = [
  "draft",
  "requested",
  "sample_collected",
  "in_progress",
  "completed",
  "cancelled",
] as const;
export type LabRequestState = (typeof LAB_REQUEST_STATES)[number];

/**
 * The BENCH display vocabulary, DERIVED server-side from `state` plus
 * `billing_blocked` -- never invented here and never sent back to Odoo.
 *
 * Seven keys for six states: `requested` splits, because "the money is not
 * settled" and "draw the sample" are the two different things it means to a
 * technician.
 *
 * `awaiting_clearance` and `ready_for_collection` ARE NOT DATABASE STATES.
 * Writing either one back would be inventing a value the schema does not have.
 */
export const LAB_DESK_STATUSES = [
  "draft",
  "awaiting_clearance",
  "ready_for_collection",
  "sample_collected",
  "in_progress",
  "completed",
  "cancelled",
] as const;
export type LabDeskStatus = (typeof LAB_DESK_STATUSES)[number];

/**
 * The default queue: work actually in front of the bench.
 *
 * Mirrors LAB_DESK_ACTIVE_STATUSES in the Odoo serializer. The server sends
 * its own copy as `meta.default_statuses` on every worklist response, and a
 * contract test asserts the two agree, so this constant is the tab order and
 * never a second opinion about what the default is.
 */
export const LAB_DESK_ACTIVE_STATUSES = [
  "awaiting_clearance",
  "ready_for_collection",
  "sample_collected",
  "in_progress",
] as const;

/** hospital.laboratory.request.priority. */
export const LAB_PRIORITIES = ["routine", "urgent", "stat"] as const;
export type LabPriority = (typeof LAB_PRIORITIES)[number];

export type LabPatientIdentity = {
  id: number;
  name: string;
  /** hospital.patient.identification_code -- the chart number on the specimen. */
  mrn: string | null;
  age: number | null;
  gender: string | null;
};

export type LabOrderedTest = {
  /** The request LINE id. Result lines link to THIS, not to the test. */
  id: number;
  test_id: number;
  name: string;
  code: string | null;
  category: string | null;
  sample_type: string | null;
  special_instruction: string | null;
  sequence: number;
};

/** One bench queue row. */
export type LabQueueRow = {
  id: number;
  request_code: string;
  /** The authoritative state, alongside the derived status. Never inferred. */
  state: string;
  status: string;
  /** The server's own wording. The client styles by `status`, displays this. */
  status_label: string;
  priority: string | null;
  priority_label: string;
  request_date: string | null;
  created_at: string | null;
  /** A boolean verdict from hospital_billing. Never an amount. */
  billing_blocked: boolean;
  active: boolean;
  patient: LabPatientIdentity | null;
  ordering_physician: Many2OneValue;
  /** Resolved through the ENCOUNTER; the bench cannot read appointments. */
  encounter_code: string | null;
  department: Many2OneValue;
  test_count: number;
  tests_summary: string | null;
  result_count: number;
  released_count: number;
};

/** The detail panel's shape: the queue row plus what the bench acts on. */
export type LabRequestDetail = LabQueueRow & {
  tests: LabOrderedTest[];
  clinical_notes: string | null;
  instructions: string | null;
};

export type LabDeskCapabilities = {
  lab_desk: boolean;
};

/**
 * Lane counts over the whole date+search scope.
 *
 * NOT the rows on this page, and NOT the selected lane. The server counts
 * every request matching the two COMMON filters, so a badge is right before
 * the technician clicks anything, and selecting one lane never zeros the
 * others.
 *
 * `awaiting_clearance` and `ready_for_collection` are `number | null`: they
 * are the one split the server cannot do in SQL (it is hospital_billing's
 * `billing_blocked`, a per-encounter compute), so past a scan cap they come
 * back null with `meta.summary_exact === false`. Render a dash, never a guess.
 * `active_bench` is null for the same reason, since it includes both.
 */
export type LabWorklistSummary = {
  active_bench: number | null;
  draft: number;
  awaiting_clearance: number | null;
  ready_for_collection: number | null;
  sample_collected: number;
  in_progress: number;
  completed: number;
  cancelled: number;
  /** All `requested` rows in scope, before the clearance split. */
  requested_total: number;
};

export type LabWorklistResponse = {
  rows: LabQueueRow[];
  /** Scope-wide lane counts. See LabWorklistSummary. */
  summary: LabWorklistSummary;
  filters: {
    date: string | null;
    status: string[];
    q: string | null;
    limit: number;
  };
  meta: {
    /** Rows on THIS page -- not a lane total. */
    row_count: number;
    truncated: boolean;
    statuses: string[];
    default_statuses: string[];
    /** False when the awaiting/ready split exceeded the server's scan cap. */
    summary_exact: boolean;
  };
  capabilities: LabDeskCapabilities;
};

export type LabRequestResponse = {
  request: LabRequestDetail;
  capabilities: LabDeskCapabilities;
};

export type LabSessionResponse = {
  user: { id: number; name: string };
  company: Many2OneValue;
  capabilities: LabDeskCapabilities;
};

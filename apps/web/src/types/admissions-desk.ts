/**
 * THE Admissions Desk wire contract (Admissions Slice 1, READ ONLY).
 *
 * Emitted directly by yoya_emr_api's admissions_desk_serializers; the BFF
 * forwards it unchanged, with no adapter and no reshaping.
 *
 * NOTHING PRICED APPEARS IN THIS FILE. Billing crosses as `billing_blocked`
 * (boolean, or null when the admission has no visit to judge) and a clearance
 * Selection KEY. No rate, fee, charge, bill or payer.
 *
 * ONE WORKFLOW ACT (Slice 2): admit, which assigns the bed and confirms the
 * admission together. Transfer and discharge are present and always false;
 * there is no route behind them.
 */
import type { ApiEnvelope } from "./clinical";

export type { ApiEnvelope };

/** Every lane, in the order the server checks them. None is a database value. */
export const ADMISSION_LANES = [
  "needs_review",
  "awaiting_bed",
  "draft",
  "admitted",
  "transferred",
  "discharged",
  "cancelled",
] as const;

export type AdmissionLane = (typeof ADMISSION_LANES)[number];

/** The default queue: work still open on the ward. */
export const ADMISSION_ACTIVE_LANES = [
  "needs_review",
  "awaiting_bed",
  "draft",
  "admitted",
  "transferred",
] as const;

/** hospital.admission.state: the five authoritative values. */
export type AdmissionState = "draft" | "admitted" | "transferred" | "discharged" | "cancelled";

/** hospital.bed.state. */
export type BedState = "available" | "occupied" | "cleaning" | "maintenance" | "blocked";

export type NeedsReviewCode =
  | "unknown_state"
  | "ward_missing"
  | "room_missing"
  | "bed_missing"
  | "bed_not_occupied"
  | "bed_pointer_mismatch"
  | "bed_room_mismatch"
  | "room_ward_mismatch"
  | "bed_ward_mismatch"
  | "encounter_missing"
  | "encounter_patient_mismatch"
  | "encounter_company_mismatch"
  | "encounter_closed"
  | "encounter_not_inpatient";

export type NeedsReviewReason = { code: NeedsReviewCode; message: string };

export type BedFlagCode =
  | "occupied_without_admission"
  | "pointer_without_occupancy"
  | "admission_without_occupied_bed"
  | "pointer_mismatch"
  | "room_mismatch"
  | "ward_mismatch"
  | "active_admission_on_unavailable_bed"
  | "multiple_active_claims";

export type BedFlag = { code: BedFlagCode; message: string };

/**
 * hospital.encounter.reception_clearance_state, plus two desk-side values:
 * `not_applicable` (no visit to judge -- the legacy ADM00001 shape) and
 * `unavailable` (the verdict could not be read).
 */
export type ClearanceState =
  | "not_required"
  | "pending"
  | "cleared"
  | "credit_authorized"
  | "sponsor_cleared"
  | "emergency_bypass"
  | "not_applicable"
  | "unavailable";

export type NamedRef = { id: number; code: string | null; name: string | null };

export type AdmissionPatient = {
  id: number;
  name: string;
  mrn: string | null;
  age: number | null;
  gender: string | null;
};

export type LengthOfStay = { days: number; hours: number; ongoing: boolean };

export type AdmissionLocation = {
  ward: NamedRef | null;
  room: NamedRef | null;
  bed: (NamedRef & { state: BedState }) | null;
};

/** The visit as the CALLER may see it. `legacy`: no visit at all. */
export type AdmissionEncounter = {
  available: boolean;
  restricted: boolean;
  legacy: boolean;
  reference: string | null;
  state: string | null;
  state_label: string | null;
  type: string | null;
  type_label: string | null;
};

export type AdmissionWorklistRow = {
  id: number;
  reference: string;
  state: AdmissionState;
  state_label: string;
  /** Optimistic concurrency: echoed back as expected_revision by admit. */
  workflow_revision: number;
  /** AFFORDANCE ONLY: the role may admit and this record's lane allows it. */
  can_admit: boolean;
  lane: AdmissionLane;
  lane_label: string;
  review_reasons: NeedsReviewReason[];
  patient: AdmissionPatient | null;
  physician: { id: number; name: string } | null;
  location: AdmissionLocation;
  admitted_at: string | null;
  expected_discharge_at: string | null;
  discharged_at: string | null;
  length_of_stay: LengthOfStay | null;
  encounter: AdmissionEncounter;
  transfer_count: number;
  billing_blocked: boolean | null;
};

export type AdmissionLaneSummary = Record<AdmissionLane | "active" | "total", number | null>;

export type AdmissionWorklistResponse = {
  rows: AdmissionWorklistRow[];
  summary: AdmissionLaneSummary;
  filters: { lane: AdmissionLane[]; ward_id: number | null; q: string | null; limit: number };
  meta: {
    row_count: number;
    total_visible: number | null;
    truncated: boolean;
    summary_exact: boolean;
    lanes: AdmissionLane[];
    default_lanes: AdmissionLane[];
    summary_filters: string[];
  };
  capabilities: AdmissionDeskCapabilities;
};

export type CountAndLatest = { count: number; latest_at: string | null } | null;

export type AdmissionTransfer = {
  id: number;
  transferred_at: string | null;
  from: { ward: NamedRef | null; room: NamedRef | null; bed: NamedRef | null };
  to: { ward: NamedRef | null; room: NamedRef | null; bed: NamedRef | null };
  transferred_by: string | null;
  reason: string | null;
};

export type AdmissionClearance = {
  billing_blocked: boolean | null;
  clearance_state: ClearanceState;
  clearance_message: string | null;
  /** Current workflow POLICY, not a balance: false for every record in Slice 1. */
  admission_clearance_required: boolean;
  discharge_clearance_required: boolean;
};

export type AdmissionDetail = AdmissionWorklistRow & {
  admission_reason: string | null;
  diagnosis:
    | { id: number; name: string | null; code: string | null; restricted: false }
    | { id: null; name: null; code: null; restricted: true }
    | null;
  bed_ownership: {
    bed_state: BedState;
    bed_records_this_admission: boolean;
    consistent: boolean | null;
  } | null;
  transfers: AdmissionTransfer[];
  nursing: {
    rounds: CountAndLatest;
    notes: CountAndLatest;
    care_plans: CountAndLatest;
    medication_administrations: CountAndLatest;
  };
  linkages: {
    procedure_requests: CountAndLatest;
    inventory_movements: CountAndLatest;
    pending_laboratory_requests: number | null;
    pending_radiology_requests: number | null;
  };
  clearance: AdmissionClearance;
};

export type AdmissionDetailResponse = {
  admission: AdmissionDetail;
  capabilities: AdmissionDeskCapabilities;
};

export type BedRollup = {
  bed_count: number;
  inactive_bed_count: number;
  available_count: number;
  occupied_count: number;
  cleaning_count: number;
  maintenance_count: number;
  blocked_count: number;
  needs_review_count: number;
};

export type RoomSummary = NamedRef & BedRollup & { type: string | null; active: boolean };

export type WardSummary = NamedRef &
  BedRollup & {
    type: string | null;
    type_label: string | null;
    department: { id: number; name: string } | null;
    active: boolean;
    room_count: number;
    rooms: RoomSummary[];
  };

export type WardsResponse = { wards: WardSummary[]; capabilities: AdmissionDeskCapabilities };

/**
 * One bed. `admission` is present ONLY when the caller may read the admission
 * holding the bed; otherwise an occupied bed says `occupied` and nothing else.
 */
export type BedBoardRow = {
  id: number;
  code: string | null;
  name: string;
  type: string | null;
  state: BedState;
  state_label: string;
  active: boolean;
  occupied: boolean;
  ward: NamedRef | null;
  room: NamedRef | null;
  can_view_admission: boolean;
  admission: {
    id: number;
    reference: string;
    state: AdmissionState;
    state_label: string;
    patient: AdmissionPatient | null;
    length_of_stay: LengthOfStay | null;
  } | null;
  flags: BedFlag[];
  needs_review: boolean;
};

export type BedsResponse = {
  beds: BedBoardRow[];
  filters: { ward_id: number | null; room_id: number | null; state: string | null; q: string | null };
  meta: { row_count: number };
  capabilities: AdmissionDeskCapabilities;
};

export type AdmissionDeskCapabilities = {
  admissions_desk: boolean;
  view_worklist: boolean;
  view_bed_board: boolean;
  /** Slice 2: admit = assign bed + confirm, one act. Admitting roles only. */
  admit: boolean;
  assign_bed: boolean;
  /** Always false: no route exists behind these yet. */
  transfer: false;
  discharge: false;
};

/** POST /api/admissions/[id]/admit */
export type AdmissionAdmitRequest = {
  operation_token: string;
  expected_revision: number;
  bed_id: number;
};

export type AdmissionMutationOperation = {
  type: "admit" | "request";
  token: string;
  replayed: boolean;
};

export type AdmissionAdmitResponse = {
  admission: AdmissionDetail;
  capabilities: AdmissionDeskCapabilities;
  workflow_revision: number;
  operation: AdmissionMutationOperation;
};

/** The fixed error codes both desks' admission mutations speak. */
export type AdmissionMutationErrorCode =
  | "admission_not_found"
  | "admission_not_authorized"
  | "admission_invalid_payload"
  | "admission_revision_conflict"
  | "admission_operation_conflict"
  | "admission_invalid_state"
  | "admission_encounter_required"
  | "admission_encounter_mismatch"
  | "admission_company_mismatch"
  | "admission_bed_required"
  | "admission_bed_unavailable"
  | "admission_bed_conflict"
  | "admission_location_mismatch"
  | "admission_active_conflict"
  | "admission_integrity_error"
  | "admission_mutation_failed";

/* ------------------------------------------------------------------ *
 * Doctor Desk handoff (Slice 2)
 * ------------------------------------------------------------------ */

export type DoctorAdmissionStatus = "none" | "requested" | "admitted" | "discharged" | "cancelled";

export type DoctorAdmissionBlock = "not_authorized" | "visit_not_open" | "open_admission_exists";

/** The admission block on the Doctor Desk visit detail. Never an admissions
 *  desk: no lanes, no bed board, no transfer or discharge. */
export type DoctorAdmissionSummary = {
  status: DoctorAdmissionStatus;
  status_label: string;
  admission: {
    id: number;
    reference: string;
    state: AdmissionState;
    state_label: string;
    requested_at: string | null;
    admitted_at: string | null;
    location: AdmissionLocation;
    length_of_stay: LengthOfStay | null;
  } | null;
  can_request: boolean;
  request_blocked_reason: DoctorAdmissionBlock | null;
  request_blocked_message: string | null;
};

/** POST /api/doctor/visits/[appointmentId]/admission-request */
export type DoctorAdmissionRequest = { operation_token: string; reason: string };

export type DoctorAdmissionRequestResponse = {
  admission: DoctorAdmissionSummary;
  operation: AdmissionMutationOperation;
};

export type AdmissionDeskRoles = {
  receptionist: boolean;
  nurse: boolean;
  front_desk_nurse: boolean;
  doctor: boolean;
  manager: boolean;
  system_admin: boolean;
};

export type AdmissionDeskScope =
  | "all_wards"
  | "own_patients"
  | "permitted_departments"
  | "own_patients_and_permitted_departments"
  | "none";

export type AdmissionDeskSession = {
  user: { id: number; name: string };
  roles: AdmissionDeskRoles;
  role_labels: string[];
  desk_role: string | null;
  scope: AdmissionDeskScope;
  permitted_department_ids: number[] | null;
  permitted_ward_ids: number[] | null;
  capabilities: AdmissionDeskCapabilities;
  read_only: true;
};

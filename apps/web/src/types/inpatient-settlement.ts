/**
 * THE FINAL INPATIENT SETTLEMENT (Advance slice) -- shared by the Cashier and
 * the Admissions Desk.
 *
 * Every type MIRRORS a server payload. The settlement is computed once, in
 * hospital.admission._inpatient_financial_summary() -- the same authority the
 * discharge gate applies -- and nothing here adds, subtracts or compares
 * amounts to decide a state. `stages` are the parts of that ONE server
 * computation, each reported by the server with its own status.
 */

/** due / refund_due / even, or needs_review when the figures cannot be
 *  trusted. not_applicable: no stay yet (a request holding only an advance). */
export type SettlementState =
  | "due"
  | "refund_due"
  /** In care, not medically ready: funds held toward ongoing care. */
  | "credit"
  | "even"
  | "needs_review"
  | "not_applicable";

export type SettlementStageKey =
  | "admission"
  | "stay"
  | "procedures"
  | "pharmacy"
  | "laboratory"
  | "radiology"
  | "other"
  | "payer"
  | "payments"
  | "reconciliation";

export type SettlementStage = {
  key: SettlementStageKey | string;
  label: string;
  /** Reported by the server that computed it. Never a client timer. */
  status: "complete" | "review";
  amount: number;
};

export type InpatientSettlement = {
  state: SettlementState;
  financial_state: string;
  /** Opaque fingerprint of the figures. A settlement payment must send it
   *  back; the server refuses the payment if the figures moved. */
  quote: string;
  estimate_amount: number;
  advance_received: number;
  delivered_by_category: { key: string; label: string; amount: number }[];
  actual_delivered: number;
  payer_authorized: number;
  patient_responsibility: number;
  funds: {
    advance: number;
    other_payments: number;
    settlement_payments: number;
    total: number;
  };
  advance_applied: number;
  unapplied_credit: number;
  remaining_due: number;
  refundable_balance: number;
  /** funds - patient share: negative is owed, positive is credit. */
  settlement_difference: number;
  stay_unposted: number;
  pending_delivery: boolean;
  stages: SettlementStage[];
  review_reasons: { code: string; message: string }[];
};

/** GET /api/admissions/[id]/settlement -- the discharging clerk's window. */
export type AdmissionSettlementResponse = {
  admission: {
    id: number;
    reference: string;
    state: string;
    medical_discharge_ready: boolean;
  };
  patient: { id: number; name: string; identification_code: string | null };
  encounter: { id: number; name: string };
  currency: string | null;
  settlement: InpatientSettlement;
  /** The discharge gate's own verdict on these figures. */
  discharge_allowed: boolean;
  /** Pre-admission readiness WITH figures (this clerk-only route); null once
   *  the patient is in a bed. */
  admission_clearance: {
    state: string;
    cleared: boolean;
    message: string;
    estimate: number;
    advance_received: number;
    remaining: number;
  } | null;
};

/** Only medical readiness locks the estimate; collected advance does not. */
export type EstimateLockReason = "medically_ready";

/** One estimate revision, immutable on the server. `baseline`: copied from the
 *  stored estimate when history began, not a new revision. */
export type DoctorEstimateRevision = {
  revision: number;
  amount: number;
  reason: string | null;
  estimated_by: string | null;
  estimated_at: string | null;
  baseline: boolean;
};

/** GET/POST /api/doctor/visits/[appointmentId]/admission-estimate */
export type DoctorEstimateResponse = {
  admission: {
    id: number;
    reference: string;
    state: string;
    revision: number;
  };
  currency: string | null;
  estimate: {
    amount: number;
    reason: string | null;
    estimated_by: string | null;
    estimated_at: string | null;
    revision: number;
  };
  /** Every revision, oldest first. Optional so an older Odoo keeps rendering. */
  history?: DoctorEstimateRevision[];
  /** The server's lock: why the estimate can no longer be given or revised. */
  locked_reason?: EstimateLockReason | null;
  /** A FLAG, never a figure: more advance is due at the Cashier. */
  additional_advance_required?: boolean;
  /** False whenever `locked_reason` is set. */
  can_edit: boolean;
  operation?: { type: "estimate"; token: string; replayed: boolean };
};

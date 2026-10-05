/**
 * Accountant Desk payloads (yoya_emr_api controllers/accountant.py).
 *
 * Every figure is the server's settlement authority; the browser renders and
 * never decides what is refundable.
 */
import type { InpatientSettlement } from "./inpatient-settlement";

export type AccountantLane = "refund_due" | "needs_review" | "refunded";

export type AccountantCapabilities = {
  accountant_desk: boolean;
  record_refund: boolean;
};

export type AccountantSession = {
  user: { id: number; name: string; login: string };
  company: { id: number; name: string };
  capabilities: AccountantCapabilities;
};

export type AccountantIdentity = {
  admission: {
    id: number;
    name: string;
    state: string;
    medical_discharge_ready: boolean;
    admission_date: string | null;
    discharge_date: string | null;
    physician: string | null;
  };
  patient: { id: number; name: string; identification_code: string | null };
  encounter: { id: number; name: string; state: string | null };
  location: {
    ward: string | null;
    ward_code: string | null;
    room: string | null;
    bed: string | null;
    bed_code: string | null;
  };
};

export type AccountantRow = AccountantIdentity & {
  lane: AccountantLane;
  lane_label: string;
  financial_state: string;
  currency: string | null;
  refundable_balance: number;
  remaining_due: number;
  refunded_total: number;
  last_refund_at: string | null;
};

export type AccountantWorklist = {
  lanes: AccountantLane[];
  counts: Record<AccountantLane, number>;
  rows: AccountantRow[];
  truncated: boolean;
};

/** PAYMENT IN: a receipt on the visit's billing account. */
export type AccountantPaymentIn = {
  id: number;
  direction: "in";
  kind: "advance" | "settlement" | "payment";
  reference: string;
  amount: number;
  payment_method: string | null;
  payment_reference: string | null;
  actor: string | null;
  at: string | null;
  state: string;
  accounting_posted: boolean;
};

/** REFUND OUT: never a negative receipt or charge. */
export type AccountantRefundOut = {
  id: number;
  direction: "out";
  kind: "refund";
  reference: string;
  amount: number;
  reason: string | null;
  refundable_before: number | null;
  refundable_after: number | null;
  actor: string | null;
  at: string | null;
  state: "recorded";
  /** Always false today: no GL cash-out journal is posted yet. */
  accounting_posted: false;
  accounting_note: string;
};

export type AccountantRefundVerdict = {
  refund_due: boolean;
  may_record: boolean;
  /** The server's refundable balance -- the most a refund may be. */
  max_amount: number;
  reason: string | null;
};

export type AccountantDetail = AccountantIdentity & {
  lane: AccountantLane | null;
  lane_label: string | null;
  currency: string | null;
  settlement: InpatientSettlement;
  payments_in: AccountantPaymentIn[];
  refunds_out: AccountantRefundOut[];
  refunded_total: number;
  refund: AccountantRefundVerdict;
  replayed?: boolean;
};

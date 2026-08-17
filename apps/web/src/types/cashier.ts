import type { ApiEnvelope, ReferenceRef } from "@/types/reception";

export type { ApiEnvelope };

/**
 * The Cashier Desk contract.
 *
 * Every type here MIRRORS a server payload and adds nothing. There is no
 * derived field, no computed total and no client-side arithmetic anywhere in
 * the cashier workspace: the split between patient and sponsor is decided by
 * hospital_billing and serialized by yoya_emr_api, and a second implementation
 * in TypeScript is a second answer waiting to disagree.
 *
 * Deliberately ABSENT, permanently: limit_amount, member_limit_amount,
 * limit_scope, payment_terms_days, tariff_mode and every other payer
 * commercial term. Those sit behind PAYER_COMMERCIAL_READ, which excludes the
 * Cashier. Declaring them here would not make the server send them -- the
 * serializer is the control -- but it would advertise an intent this desk must
 * not have.
 */

/** Which operational lane a visit is in. Decided server-side. */
export type CashierLane = "collect" | "partial" | "blocked" | "cleared";

/**
 * MAY the cashier take money, and if not, why not.
 *
 * The verdict, not the ingredients. Never re-derive this from amounts: zero
 * outstanding because a visit is settled and zero outstanding because nobody
 * authorized the sponsor look identical in numbers and mean opposite things at
 * the window.
 */
export type CashierCollectability = {
  collectable: boolean;
  lane: CashierLane;
  reason: string | null;
  reason_code:
    | "no_billing_account"
    | "emergency_bypass"
    | "already_cleared"
    | "sponsor_authorization_pending"
    | "nothing_outstanding"
    | null;
};

export type ResponsibilityMode = "off" | "shadow" | "enforce";

/**
 * The authoritative financial block.
 *
 * `patient_outstanding` is the ONLY figure the cashier collects against, and it
 * is already mode-correct on the server: under off/shadow it is the legacy
 * gross requirement, under enforce it is the patient residual. The UI must
 * therefore never choose between `patient_responsibility` and
 * `amount_estimated` itself -- doing so under shadow would ask for the smaller
 * figure while the backend still demands the larger, and the visit would
 * silently never clear.
 */
export type CashierFinancial = {
  currency: string | null;
  responsibility_mode: ResponsibilityMode;
  /** True while the split is recorded but NOT driving the cash gate. */
  responsibility_advisory: boolean;
  amount_estimated: number;
  patient_responsibility: number;
  sponsor_responsibility: number;
  sponsor_authorized: number;
  /** Operational sponsor exposure. NOT an accounting receivable. */
  sponsor_outstanding: number;
  /** Real patient cash only. Never contains a sponsor figure. */
  patient_paid: number;
  patient_outstanding: number;
  responsibility_state: string | null;
  /**
   * STORED MIRROR, written only at consultation start. It is commonly stale
   * immediately after payment. Descriptive metadata only -- bind operational
   * decisions to `financially_cleared`.
   */
  financial_clearance_state: string | null;
  /** LIVE clearance. This is the operational truth. */
  financially_cleared: boolean;
};

export type CashierChargeLine = {
  id: number;
  name: string;
  description: string | null;
  service: ReferenceRef | null;
  amount: number;
  received: number;
  outstanding: number;
  charge_state: string | null;
  payment_state: string | null;
  operational_funding_state: string | null;
  sponsor_responsibility: number;
  sponsor_authorized: number;
  patient_responsibility: number;
  responsibility_state: string | null;
};

export type CashierWorklistRow = {
  appointment_id: number;
  appointment_code: string | null;
  appointment_date: string | null;
  stage: string | null;
  lane: CashierLane;
  patient: {
    id: number;
    name: string;
    identification_code: string | null;
  };
  encounter_name: string | null;
  patient_outstanding: number;
  patient_paid: number;
  responsibility_state: string | null;
  financially_cleared: boolean;
};

export type CashierCapabilities = {
  cashier_desk: boolean;
  record_payment: boolean;
  post_receipt_accounting: boolean;
  /** Reported so the desk can NAME the unblocking role, never to offer it. */
  authorize_sponsor: boolean;
};

export type CashierWorklist = {
  date: string;
  stages: string[];
  rows: CashierWorklistRow[];
  counts: Record<string, number>;
  lane_counts: Record<string, number>;
  truncated: boolean;
  capabilities: CashierCapabilities;
};

export type CashierReceiptAllocation = {
  id: number;
  charge_line_id: number;
  amount: number;
};

export type CashierReceipt = {
  id: number;
  name: string;
  amount: number;
  payment_method: string;
  payment_reference: string | null;
  note: string | null;
  received_at: string | null;
  received_by: ReferenceRef | null;
  state: string;
  intake_token: string | null;
  allocations: CashierReceiptAllocation[];
  allocated_total: number;
  accounting: {
    posted: boolean;
    fiscalized: boolean;
    has_accounting_move: boolean;
  };
};

export type CashierClearance = {
  required: number;
  received: number;
  outstanding: number;
  ok: boolean;
  state: string | null;
  message: string | null;
};

export type CashierVisitDetail = {
  appointment: {
    id: number;
    appointment_code: string | null;
    appointment_date: string | null;
    state: string;
    visit_type: string | null;
  };
  patient: {
    id: number;
    identification_code: string | null;
    name: string;
    date_of_birth: string | null;
    age: number | null;
    gender: string | null;
    phone: string | null;
    mobile: string | null;
  };
  encounter: {
    id: number;
    name: string;
    state: string;
    encounter_type: string | null;
    payer_type: string | null;
  } | null;
  billing_account: Record<string, unknown> | null;
  financial: CashierFinancial;
  collectability: CashierCollectability;
  charge_lines: CashierChargeLine[];
  clearance: CashierClearance;
  permitted_actions: {
    record_payment: boolean;
    post_receipt_accounting: boolean;
  };
};

/** A payment response is the canonical detail plus the receipt just created. */
export type CashierPaymentResult = CashierVisitDetail & {
  receipt: CashierReceipt;
};

/**
 * Payment methods, mirroring hospital_billing.charge_receipt.PAYMENT_METHODS.
 * `fiscal_terminal` exists on the server but is withheld from v1: selecting it
 * implies a device integration this desk does not have.
 */
export const CASHIER_PAYMENT_METHODS = [
  { key: "cash", label: "Cash", referenceRequired: false },
  { key: "card", label: "Card", referenceRequired: true },
  { key: "mobile_money", label: "Mobile Money", referenceRequired: true },
  { key: "bank_transfer", label: "Bank Transfer", referenceRequired: true },
  { key: "other", label: "Other", referenceRequired: true },
] as const;

export type CashierPaymentMethod =
  (typeof CASHIER_PAYMENT_METHODS)[number]["key"];

export type CashierPaymentBody = {
  amount: number;
  payment_method: CashierPaymentMethod;
  payment_reference?: string | null;
  note?: string | null;
  idempotency_key: string;
};

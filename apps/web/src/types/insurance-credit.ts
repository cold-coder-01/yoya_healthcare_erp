import type { ApiEnvelope } from "@/types/reception";

export type { ApiEnvelope };

/**
 * The Insurance/Credit Desk contract.
 *
 * Every type mirrors a server payload and adds nothing. The officer's decision
 * is validated against a figure the SERVER recomputes under a lock, so no
 * amount here is ever authoritative: `permitted_sponsor_amount` is what the
 * agreement allowed when this page rendered, and it may be stale by the time
 * the officer clicks. The server re-reads it and refuses rather than capping.
 *
 * Deliberately ABSENT, permanently: limit_amount, member_limit_amount,
 * payment_terms_days, tariff_mode, agreement/rule notes, and every
 * ACCOUNTING_READ figure. An officer authorizes exposure; they do not book it.
 */

export type ReviewStatus =
  | "no_payer"
  | "review_required"
  | "partially_authorized"
  | "authorized"
  | "not_covered"
  | "blocked";

export type OfficerChargeRow = {
  id: number;
  name: string;
  description: string | null;
  service: string | null;
  service_type: string | null;
  amount: number;
  /** What the agreement permits, evaluated live. */
  coverage_state: string;
  excluded: boolean;
  requires_authorization: boolean;
  calculated_sponsor_amount: number;
  permitted_sponsor_amount: number;
  /** null means unbounded, NOT exhausted. */
  limit_available: number | null;
  reason_code: string | null;
  reason: string;
  matched_rule: string | null;
  /** What has actually been decided. */
  authorized_sponsor_amount: number;
  patient_responsibility: number;
  responsibility_state: string | null;
  responsibility_id: number | null;
  responsibility_state_detail: string | null;
  needs_decision: boolean;
  /** Non-null once cash is taken or the charge is invoiced. */
  decision_frozen_reason: string | null;
};

export type OfficerEligibility = {
  id: number;
  reference: string | null;
  payer_name: string | null;
  agreement_number: string | null;
  member_reference: string | null;
  membership_number: string | null;
  policy_number: string | null;
  relationship_to_principal: string | null;
  effective_from: string | null;
  effective_to: string | null;
  is_valid_today: boolean;
  state: string;
};

export type OfficerCapabilities = {
  insurance_credit_desk: boolean;
  authorize_sponsor: boolean;
  /** False for a pure officer: taking cash is the cashier's duty. */
  record_payment: boolean;
};

export type OfficerVisitDetail = {
  appointment: {
    id: number;
    appointment_code: string | null;
    appointment_date: string | null;
    state: string;
  };
  patient: {
    id: number;
    name: string;
    identification_code: string | null;
    age: number | null;
    gender: string | null;
  };
  encounter: { id: number; name: string; state: string } | null;
  eligibility: OfficerEligibility | null;
  charges: OfficerChargeRow[];
  summary: {
    charge_count: number;
    pending_count: number;
    gross_amount: number;
    permitted_sponsor_total: number;
    authorized_sponsor_total: number;
    patient_responsibility_total: number;
    review_status: ReviewStatus;
  };
  capabilities: OfficerCapabilities;
};

export type OfficerWorklistRow = {
  appointment_id: number;
  appointment_code: string | null;
  appointment_date: string | null;
  stage: string | null;
  patient: { id: number; name: string; identification_code: string | null };
  payer_name: string | null;
  member_reference: string | null;
  pending_charge_count: number;
  permitted_sponsor_total: number;
  authorized_sponsor_total: number;
  patient_responsibility_total: number;
  review_status: ReviewStatus;
};

export type OfficerWorklist = {
  date: string;
  rows: OfficerWorklistRow[];
  counts: Record<string, number>;
  truncated: boolean;
  capabilities: OfficerCapabilities;
};

/** One charge decision. `amount` may be 0 — that is a denial, and needs a reason. */
export type AuthorizationDecision = {
  charge_id: number;
  amount: number;
  reason?: string;
  authorization_reference?: string;
};

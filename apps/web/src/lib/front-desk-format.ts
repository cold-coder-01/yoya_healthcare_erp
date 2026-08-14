const LABELS: Record<string, string> = {
  new: "Intake",
  intake: "Intake",
  triage: "Triage",
  awaiting_cashier: "Cashier",
  ready_doctor: "Ready",
  in_consultation: "In Consultation",
  completed: "Completed",
  cancelled: "Cancelled",
  routine: "Routine",
  urgent: "Urgent",
  emergency: "Emergency",
  draft: "In progress",
  done: "Complete",
  pending: "Pending",
  cleared: "Cleared",
  credit_authorized: "Authorized",
  emergency_bypass: "Emergency bypass",
  funded: "Funded",
  partially_funded: "Partially funded",
  unfunded: "Unfunded",
};

export function frontDeskLabel(value: string | null | undefined, fallback = "-") {
  if (!value) return fallback;
  return (
    LABELS[value] ??
    value
      .split("_")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
}

export function compactGender(value: string | null | undefined) {
  if (value === "male") return "M";
  if (value === "female") return "F";
  return value ? value.charAt(0).toUpperCase() : "-";
}

export function displayValue(
  value: string | number | null | undefined,
  suffix = "",
) {
  if (value === null || value === undefined || value === "") return "-";
  return `${value}${suffix}`;
}

/**
 * One dense line for a payer eligibility: who is responsible, and under which
 * membership. Identity only -- there is no monetary field on the type to show.
 *
 * The member reference falls back through the four identifier columns because
 * different payer types populate different ones: an insurer fills the policy
 * number, a corporate scheme the employee id.
 */
export function eligibilityLabel(
  eligibility: {
    payer_name: string | null;
    agreement_number: string | null;
    agreement_name: string | null;
    member_reference: string | null;
    membership_number: string | null;
    policy_number: string | null;
    employee_id_number: string | null;
  } | null,
  fallback = "Self Pay / No sponsor",
) {
  if (!eligibility) return fallback;
  const member =
    eligibility.member_reference ??
    eligibility.membership_number ??
    eligibility.policy_number ??
    eligibility.employee_id_number;
  const payer =
    eligibility.payer_name ??
    eligibility.agreement_name ??
    eligibility.agreement_number ??
    "Sponsor";
  return member ? `${payer} - ${member}` : payer;
}

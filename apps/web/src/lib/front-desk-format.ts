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

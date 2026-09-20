/**
 * Pharmacy Desk: display vocabulary, filtering and route construction.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * decides anything clinical or financial.
 *
 * THE RULES THIS FILE KEEPS:
 *
 *   * The browser FORMATS a lane, it never DERIVES one. Every lane arrives
 *     decided by the server's pharmacy_desk_lane(); the browser never looks at a
 *     dispense state or a quantity to work out where a record belongs.
 *   * The browser renders a billing VERDICT, never an amount -- there is none in
 *     the payload.
 *   * Every URL built here is a BFF path. The browser never addresses Odoo.
 */
// TYPE-ONLY, so node:test runs the format tests with no resolver.
import type {
  PharmacyDeskRoles,
  PharmacyDispenseDetail,
  PharmacyDispenseLine,
  PharmacyQueueRow,
  PharmacyWorklistSummary,
} from "@/types/pharmacy-desk";

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

/**
 * Runtime copy of the default lanes. DUPLICATED FROM types/pharmacy-desk.ts ON
 * PURPOSE so this file stays free of value imports; a test asserts they agree.
 */
export const PHARMACY_ACTIVE_LANE_ORDER: readonly string[] = [
  "awaiting_preparation",
  "awaiting_clearance",
  "ready_to_validate",
  "partially_supplied",
  "blocked",
  "anomaly",
];

export const PHARMACY_LANE_ORDER: readonly string[] = [
  ...PHARMACY_ACTIVE_LANE_ORDER,
  "completed",
  "cancelled",
];

/** The pseudo-lane for "all active work". Never sent to the server as-is. */
export const ACTIVE_LANE_KEY = "active";

const LANE_LABELS: Record<string, string> = {
  awaiting_preparation: "Awaiting preparation",
  awaiting_clearance: "Awaiting clearance",
  ready_to_validate: "Ready to validate",
  partially_supplied: "Partially supplied",
  blocked: "Blocked",
  anomaly: "Needs review",
  completed: "Completed",
  cancelled: "Cancelled",
};

/** TEXT, NOT COLOUR: every lane carries a code as well as a tone. */
const LANE_CODES: Record<string, string> = {
  awaiting_preparation: "PREP",
  awaiting_clearance: "CLR",
  ready_to_validate: "RDY",
  partially_supplied: "PART",
  blocked: "BLK",
  anomaly: "REV",
  completed: "DONE",
  cancelled: "CANC",
};

const PRIORITY_CODES: Record<string, string> = {
  routine: "RTN",
  urgent: "URG",
  emergency: "EMG",
};

export function pharmacyLaneLabel(lane: string | null | undefined) {
  if (!lane) return "—";
  if (lane === ACTIVE_LANE_KEY) return "All active";
  return LANE_LABELS[lane] ?? lane;
}

export function pharmacyLaneCode(lane: string | null | undefined) {
  if (!lane) return "—";
  if (lane === ACTIVE_LANE_KEY) return "ALL";
  return LANE_CODES[lane] ?? lane.slice(0, 4).toUpperCase();
}

export function pharmacyPriorityCode(priority: string | null | undefined) {
  if (!priority) return "RTN";
  return PRIORITY_CODES[priority] ?? priority.slice(0, 3).toUpperCase();
}

export function isUrgentPriority(priority: string | null | undefined) {
  return priority === "urgent" || priority === "emergency";
}

/** The lanes a strip entry asks the server for. */
export function laneStatuses(laneKey: string): readonly string[] {
  if (laneKey === ACTIVE_LANE_KEY) return PHARMACY_ACTIVE_LANE_ORDER;
  return PHARMACY_LANE_ORDER.includes(laneKey) ? [laneKey] : PHARMACY_ACTIVE_LANE_ORDER;
}

/* ------------------------------------------------------------------ *
 * Operational sentences -- amount-free, derived from the SERVER's lane
 * ------------------------------------------------------------------ */

/** The one sentence about money. It names no figure because none arrives. */
export function clearanceNotice(
  row: Pick<PharmacyQueueRow, "lane" | "billing_blocked">,
): string | null {
  if (row.lane === "awaiting_clearance") {
    return "Awaiting financial clearance. The intended quantity cannot be handed over until the patient is cleared at the cashier.";
  }
  return null;
}

/**
 * The review/blocker sentence. The SERVER's fixed wording wins; the fallbacks
 * exist only for a payload that carries none.
 */
export function reasonNotice(
  row: Pick<PharmacyQueueRow, "lane" | "reason" | "reason_message">,
): { tone: "red" | "amber"; text: string } | null {
  if (row.lane !== "anomaly" && row.lane !== "blocked") return null;
  const tone = row.lane === "anomaly" ? "red" : "amber";
  if (row.reason_message && row.reason_message.trim()) {
    return { tone, text: row.reason_message };
  }
  return {
    tone,
    text:
      row.lane === "anomaly"
        ? "This dispense needs review before workflow actions can continue."
        : "This dispense is blocked by configuration or stock.",
  };
}

/* ------------------------------------------------------------------ *
 * Display helpers
 * ------------------------------------------------------------------ */

/** Quantities render compactly: 10, 2.5, 0.125. Never money formatting. */
export function qtyLabel(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return String(Math.round(value * 1000) / 1000);
}

export function ageSexLabel(
  patient: { age: number | null; gender: string | null } | null | undefined,
) {
  if (!patient) return "—";
  const age = patient.age ? String(patient.age) : "—";
  const sex = patient.gender ? patient.gender.charAt(0).toUpperCase() : "—";
  return `${age} / ${sex}`;
}

export function orDash(value: string | null | undefined) {
  const trimmed = typeof value === "string" ? value.trim() : "";
  return trimmed ? trimmed : "—";
}

export function medicineLabel(line: Pick<PharmacyDispenseLine, "medicine">) {
  const medicine = line.medicine;
  if (!medicine) return "Unknown medicine";
  return medicine.strength ? `${medicine.name} ${medicine.strength}` : medicine.name;
}

/** "2 / 3 lines complete". */
export function lineProgressLabel(row: Pick<PharmacyQueueRow, "lines_complete" | "line_count">) {
  return `${row.lines_complete} / ${row.line_count}`;
}

/** Stock verdict for one line, in words. Never a stock figure. */
export function stockLabel(line: Pick<PharmacyDispenseLine, "inventory_mapped" | "stock_sufficient" | "stock_basis">) {
  if (!line.inventory_mapped) return "Not mapped";
  if (line.stock_basis === null || line.stock_sufficient === null) return "—";
  const basis = line.stock_basis === "increment" ? "for intended" : "for remaining";
  return line.stock_sufficient ? `In stock ${basis}` : `Short ${basis}`;
}

export function billingLabel(line: Pick<PharmacyDispenseLine, "billing_mapped">) {
  return line.billing_mapped ? "Mapped" : "Not mapped";
}

export function deskRoleLabel(roles: PharmacyDeskRoles | null | undefined) {
  if (!roles) return null;
  if (roles.pharmacist) return "Pharmacist";
  if (roles.system_admin) return "System Administrator";
  if (roles.manager) return "Manager";
  return null;
}

/* ------------------------------------------------------------------ *
 * Queue
 * ------------------------------------------------------------------ */

/** A badge from the SERVER's scope-wide summary. Nothing is counted here. */
export function laneCount(summary: PharmacyWorklistSummary | null, laneKey: string): number | null {
  if (!summary) return null;
  const key = laneKey === ACTIVE_LANE_KEY ? "active" : laneKey;
  const value = (summary as Record<string, number | null>)[key];
  return typeof value === "number" ? value : null;
}

export function laneCountLabel(summary: PharmacyWorklistSummary | null, laneKey: string) {
  const value = laneCount(summary, laneKey);
  return value === null ? "—" : String(value);
}

/** Local narrowing over rows the server already returned. Never widens. */
export function matchesSearch(row: PharmacyQueueRow, term: string) {
  const needle = term.trim().toLowerCase();
  if (!needle) return true;
  return [
    row.dispense_code,
    row.prescription?.code ?? "",
    row.patient?.name ?? "",
    row.patient?.mrn ?? "",
    row.prescriber?.name ?? "",
    row.medicines_summary ?? "",
  ]
    .join(" ")
    .toLowerCase()
    .includes(needle);
}

/** The selection in force, DERIVED: falls to the top when it disappears. */
export function resolveSelection(rows: PharmacyQueueRow[], selectedId: number | null) {
  if (selectedId !== null && rows.some((row) => row.id === selectedId)) return selectedId;
  return rows[0]?.id ?? null;
}

export function visibleDetail(detail: PharmacyDispenseDetail | null, activeId: number | null) {
  if (!detail || activeId === null) return null;
  return detail.id === activeId ? detail : null;
}

/** Loading is DERIVED, never stored: an aborted fetch cannot leave it stuck. */
export function detailIsLoading(
  activeId: number | null,
  loadingFlag: boolean,
  visible: PharmacyDispenseDetail | null,
) {
  if (activeId === null) return false;
  if (visible !== null) return false;
  return loadingFlag;
}

/* ------------------------------------------------------------------ *
 * Routes -- BFF paths only
 * ------------------------------------------------------------------ */

export const PHARMACY_SESSION_PATH = "/api/pharmacy/session";

export function worklistPath(params: {
  status?: readonly string[];
  date?: string | null;
  q?: string | null;
  limit?: number | null;
}) {
  const query = new URLSearchParams();
  if (params.status && params.status.length > 0) query.set("status", params.status.join(","));
  if (params.date) query.set("date", params.date);
  if (params.q && params.q.trim()) query.set("q", params.q.trim());
  if (params.limit) query.set("limit", String(params.limit));
  const queryString = query.toString();
  return queryString ? `/api/pharmacy/worklist?${queryString}` : "/api/pharmacy/worklist";
}

export function dispensePath(dispenseId: number) {
  return `/api/pharmacy/dispenses/${dispenseId}`;
}

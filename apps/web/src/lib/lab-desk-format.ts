/**
 * Laboratory Desk: display vocabulary, filtering and route construction.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * decides anything clinical or financial.
 *
 * THE ONE RULE THIS FILE EXISTS TO KEEP: the browser FORMATS a clearance
 * verdict, it never REACHES one. `awaiting_clearance` arrives already decided
 * by hospital_billing's own compute, delivered as a boolean. There is no
 * balance, no amount and no payer in this module because there is none in the
 * payload, and a client that could compute "cleared" from figures would be a
 * second, drifting definition of a decision the billing engine owns.
 */
// TYPE-ONLY, and it has to stay that way -- TypeScript erases these, which is
// what lets node:test run lab-desk-format.test.ts with no resolver and no
// transform.
import type {
  LabDeskStatus,
  LabQueueRow,
  LabRequestDetail,
} from "@/types/lab-desk";

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

/**
 * Runtime copy of the bench statuses, in workflow order.
 *
 * DUPLICATED FROM types/lab-desk.ts ON PURPOSE, for the reason
 * laboratory-format.ts states: this copy is the runtime one, and it must stay
 * free of value imports so node:test can run the format tests with no
 * resolver. A contract test asserts the two lists agree.
 */
export const LAB_DESK_STATUS_ORDER: readonly string[] = [
  "draft",
  "awaiting_clearance",
  "ready_for_collection",
  "sample_collected",
  "in_progress",
  "completed",
  "cancelled",
];

/** The default queue: work actually in front of the bench. */
export const LAB_DESK_ACTIVE_STATUS_ORDER: readonly string[] = [
  "awaiting_clearance",
  "ready_for_collection",
  "sample_collected",
  "in_progress",
];

/**
 * Fallback labels for the status keys the SERVER produces.
 *
 * The server's own `status_label` is what the UI displays; these exist so an
 * unrecognised key still renders as words, and so the client has a stable
 * styling key. They must never disagree with the server's wording.
 */
const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  awaiting_clearance: "Awaiting clearance",
  ready_for_collection: "Ready for collection",
  sample_collected: "Sample collected",
  in_progress: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
};

/**
 * Short codes for the dense queue column.
 *
 * Text, not colour. Every status carries this code AND a tone, so a technician
 * who cannot distinguish the tones reads the same fact from the letters.
 */
const STATUS_CODES: Record<string, string> = {
  draft: "DRF",
  awaiting_clearance: "PAY",
  ready_for_collection: "DRAW",
  sample_collected: "COLL",
  in_progress: "PROC",
  completed: "DONE",
  cancelled: "CANC",
};

const PRIORITY_LABELS: Record<string, string> = {
  routine: "Routine",
  urgent: "Urgent",
  stat: "STAT",
};

/** Short codes for the priority cell. Again text, never colour alone. */
const PRIORITY_CODES: Record<string, string> = {
  routine: "RTN",
  urgent: "URG",
  stat: "STAT",
};

export function labStatusLabel(status: string | null | undefined) {
  if (!status) return "—";
  return STATUS_LABELS[status] ?? status;
}

export function labStatusCode(status: string | null | undefined) {
  if (!status) return "—";
  return STATUS_CODES[status] ?? status.slice(0, 4).toUpperCase();
}

export function labPriorityLabel(priority: string | null | undefined) {
  if (!priority) return "Routine";
  return PRIORITY_LABELS[priority] ?? priority;
}

export function labPriorityCode(priority: string | null | undefined) {
  if (!priority) return "RTN";
  return PRIORITY_CODES[priority] ?? priority.slice(0, 4).toUpperCase();
}

/** Urgent and STAT get a marker; routine does not. Presentation only. */
export function isUrgentPriority(priority: string | null | undefined) {
  return priority === "urgent" || priority === "stat";
}

/** A status that no longer moves. Used to stop offering work on it. */
export function isTerminalStatus(status: string | null | undefined) {
  return status === "completed" || status === "cancelled";
}

/**
 * The one operational sentence the panel shows about money.
 *
 * DELIBERATELY AMOUNT-FREE, and it is derived from the boolean the server
 * sent, not from anything financial. A bench operator is told THAT the request
 * is not cleared and who settles it; how much, from whom, and against which
 * receipt are the cashier's business and never reach this screen.
 *
 * Returns null when there is nothing operational to say, so the caller renders
 * no banner rather than an empty one.
 */
export function clearanceNotice(status: string | null | undefined): string | null {
  if (status === "awaiting_clearance") {
    return "Awaiting financial clearance. The patient settles this at the cashier before the sample can be drawn.";
  }
  if (status === "ready_for_collection") {
    return "Ready for collection.";
  }
  return null;
}

/* ------------------------------------------------------------------ *
 * Display helpers
 * ------------------------------------------------------------------ */

/** "34 / F", or a dash for either half the record does not carry. */
export function ageSexLabel(
  patient: { age: number | null; gender: string | null } | null | undefined,
) {
  if (!patient) return "—";
  // age is an Integer on hospital.patient and computes to 0 when there is no
  // date of birth, which is "not recorded" rather than a newborn.
  const age = patient.age ? String(patient.age) : "—";
  const sex = patient.gender ? patient.gender.charAt(0).toUpperCase() : "—";
  return `${age} / ${sex}`;
}

/** Test name with its catalogue code, when there is one. */
export function testLabel(test: { name: string; code: string | null }) {
  return test.code ? `${test.name} (${test.code})` : test.name;
}

/** "1 test" / "3 tests". */
export function testCountLabel(count: number) {
  return count === 1 ? "1 test" : `${count} tests`;
}

/** Any value the record does not carry renders as an em dash, never blank. */
export function orDash(value: string | null | undefined) {
  const trimmed = typeof value === "string" ? value.trim() : "";
  return trimmed ? trimmed : "—";
}

/* ------------------------------------------------------------------ *
 * Queue
 * ------------------------------------------------------------------ */

/**
 * Counts for the status tabs, over the rows the server actually sent.
 *
 * The server sends its own `counts` describing the same rows; this exists for
 * the client-side text filter, which narrows what is on screen without
 * refetching. Both are derived from the same array, so they cannot disagree
 * about a population.
 */
export function statusCounts(rows: LabQueueRow[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const status of LAB_DESK_STATUS_ORDER) counts[status] = 0;
  for (const row of rows) {
    counts[row.status] = (counts[row.status] ?? 0) + 1;
  }
  return counts;
}

/**
 * Local text filtering over rows Odoo already scoped and returned.
 *
 * This NARROWS a visible list and can never widen it. The server-side `q`
 * parameter is the real search; this is the instant feedback while typing, and
 * it deliberately matches the same three fields the server searches -- request
 * code, patient name, chart number -- so a term that filters here would also
 * have found the row upstream.
 */
export function matchesSearch(row: LabQueueRow, term: string) {
  const needle = term.trim().toLowerCase();
  if (!needle) return true;
  const haystack = [
    row.request_code,
    row.patient?.name ?? "",
    row.patient?.mrn ?? "",
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(needle);
}

/**
 * The selection actually in force.
 *
 * DERIVED, never synchronised. When a filter or a refresh removes the selected
 * request, this falls to the top of what is visible during the same render --
 * an effect that wrote the selection back would render one frame showing a
 * request that is no longer in the queue.
 *
 * Returns null for an empty queue, which is what clears the detail panel
 * safely rather than leaving the previous request on screen.
 */
export function resolveSelection(
  rows: LabQueueRow[],
  selectedId: number | null,
): number | null {
  if (selectedId !== null && rows.some((row) => row.id === selectedId)) {
    return selectedId;
  }
  return rows[0]?.id ?? null;
}

/* ------------------------------------------------------------------ *
 * Routes
 * ------------------------------------------------------------------ */

/**
 * The BFF worklist URL.
 *
 * The browser talks to /api/laboratory/* and nothing else: it holds no Odoo
 * session and knows no Odoo URL. Empty values are omitted rather than sent as
 * `q=`, which upstream would read as a search for the empty string.
 */
export function worklistPath(params: {
  status?: readonly string[];
  date?: string | null;
  q?: string | null;
  limit?: number | null;
}) {
  const query = new URLSearchParams();
  if (params.status && params.status.length > 0) {
    query.set("status", params.status.join(","));
  }
  if (params.date) query.set("date", params.date);
  if (params.q && params.q.trim()) query.set("q", params.q.trim());
  if (params.limit) query.set("limit", String(params.limit));
  const queryString = query.toString();
  return queryString
    ? `/api/laboratory/worklist?${queryString}`
    : "/api/laboratory/worklist";
}

export function requestPath(requestId: number) {
  return `/api/laboratory/requests/${requestId}`;
}

/**
 * A detail payload is only shown against the row it belongs to.
 *
 * What stops the panel rendering the previous request for a frame after the
 * selection moves.
 */
export function detailMatchesSelection(
  detail: LabRequestDetail | null,
  selectedId: number | null,
) {
  return detail !== null && selectedId !== null && detail.id === selectedId;
}

/** Narrowing helper for the styling maps, which are keyed by known status. */
export function isKnownStatus(value: string): value is LabDeskStatus {
  return LAB_DESK_STATUS_ORDER.includes(value);
}

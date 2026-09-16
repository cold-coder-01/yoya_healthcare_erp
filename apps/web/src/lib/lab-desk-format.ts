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
  LabWorklistSummary,
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
 * The badge for one lane, read from the SERVER'S scope-wide summary.
 *
 * THE DEFECT THIS REPLACES. The client used to recount the rows it had on
 * screen -- rows already narrowed to the selected lane, to the search box, and
 * to one bounded page. Every lane the technician had not clicked therefore
 * read 0, and clicking it "discovered" the real number. A badge you have to
 * click to make true is worse than no badge.
 *
 * NOTHING IS DERIVED HERE. The client neither counts nor classifies; it reads
 * the number the server computed with the same lab_desk_status() the rows use.
 * In particular the browser never splits `requested` into awaiting/ready --
 * that is hospital_billing's verdict and it stays server-side.
 *
 * Returns `null` when the count is genuinely unknown (the server could not do
 * the clearance split within its scan cap), so the caller renders a dash
 * rather than a zero that would read as "there is no work here".
 */
export function laneCount(
  summary: LabWorklistSummary | null,
  laneKey: string,
): number | null {
  if (!summary) return null;
  const value = (summary as unknown as Record<string, unknown>)[
    laneKey === "active" ? "active_bench" : laneKey
  ];
  return typeof value === "number" ? value : null;
}

/** "50", or an em dash when the server could not determine the count. */
export function laneCountLabel(
  summary: LabWorklistSummary | null,
  laneKey: string,
): string {
  const value = laneCount(summary, laneKey);
  return value === null ? "—" : String(value);
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

/** The BFF collect route. The browser never posts anywhere else. */
export function collectPath(requestId: number) {
  return `/api/laboratory/requests/${requestId}/collect`;
}

/** The BFF start-processing route. */
export function startProcessingPath(requestId: number) {
  return `/api/laboratory/requests/${requestId}/start-processing`;
}

/**
 * May the bench be offered a Collect control for this row?
 *
 * DERIVED FROM THE SERVER'S DERIVED STATUS, AND FROM NOTHING ELSE. The one
 * status that means "the sample may be drawn now" is `ready_for_collection`,
 * which the server computes as `state == "requested" AND NOT billing_blocked`.
 *
 * This function deliberately takes a STATUS KEY, not a row and not a boolean
 * pair. It cannot look at `billing_blocked`, an amount or a balance even by
 * accident, because none of that is in scope here -- which is the structural
 * version of "the browser never decides whether the patient has paid".
 *
 * AND IT IS AN AFFORDANCE, NEVER A PERMISSION. The server re-checks clearance
 * and the state machine inside action_mark_sample_collected() on every call;
 * a button rendered from stale data still gets a clean refusal.
 */
export function canCollect(status: string | null | undefined): boolean {
  return status === "ready_for_collection";
}

/**
 * May the bench be offered a Start processing control for this row?
 *
 * THE SAME SHAPE AS canCollect, AND FOR THE SAME REASONS. One status and one
 * only: `sample_collected`, which is exactly the source state
 * hospital.laboratory.request.action_mark_in_progress() accepts. It takes a
 * STATUS KEY, so it cannot consult a financial value or a raw state even by
 * accident.
 *
 * AN AFFORDANCE, NEVER A PERMISSION. The server re-runs the state machine on
 * every call; a button drawn from stale data still gets a clean refusal.
 */
export function canStartProcessing(status: string | null | undefined): boolean {
  return status === "sample_collected";
}

/**
 * The message shown when a collection is refused.
 *
 * The server's own sentence is preferred -- it is the only thing that says
 * WHY, and the Laboratory API has already sanitised the one refusal whose
 * wording could carry an amount. The fallback exists for transport failures,
 * where there is no server sentence at all.
 */
export function collectErrorMessage(
  serverMessage: string | null | undefined,
  fallback = "The sample could not be marked collected.",
) {
  const trimmed = typeof serverMessage === "string" ? serverMessage.trim() : "";
  return trimmed ? trimmed : fallback;
}

/**
 * The same rule for starting processing.
 *
 * Shares collectErrorMessage's body deliberately -- one definition of "prefer
 * the server's sentence, fall back to safe wording" -- with its own default so
 * a transport failure names the action that did not happen.
 */
export function startProcessingErrorMessage(
  serverMessage: string | null | undefined,
  fallback = "Processing could not be started.",
) {
  return collectErrorMessage(serverMessage, fallback);
}

/**
 * Error codes after which the desk must RE-READ rather than trust its screen.
 *
 * All three mean the browser was looking at a request whose real state has
 * moved on: the workflow refused it, money now blocks it, or it is no longer
 * reachable. Leaving the stale row on screen would invite the technician to
 * press Collect again against a request that has already changed.
 */
const RECONCILE_CODES = new Set([
  "invalid_workflow_state",
  "lab_not_financially_cleared",
  "lab_request_not_found",
]);

export function shouldReconcileAfter(code: string | null | undefined): boolean {
  return typeof code === "string" && RECONCILE_CODES.has(code);
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

/**
 * Which request the detail panel may render.
 *
 * TWO WAYS IN, and the second is what makes collection usable.
 *
 *   activeId     the current queue selection
 *   justActedId  a request the technician has just acted on -- collected --
 *                which has therefore LEFT the lane they are looking at
 *
 * Without the second, collecting a sample makes the request vanish from the
 * Ready lane and takes the detail panel with it, so the technician gets no
 * confirmation that the thing they clicked actually happened. Pinning it keeps
 * the answer on screen: the panel now reads Sample collected and the Collect
 * button is gone, because the status moved.
 */
export function visibleDetail(
  detail: LabRequestDetail | null,
  activeId: number | null,
  justActedId: number | null = null,
): LabRequestDetail | null {
  if (!detail) return null;
  if (activeId !== null && detail.id === activeId) return detail;
  if (justActedId !== null && detail.id === justActedId) return detail;
  return null;
}

/**
 * Whether the detail panel should show its loading state.
 *
 * DERIVED, NEVER STORED, and that is the whole point of this function.
 *
 * THE UAT DEFECT IT FIXES. The panel used to consume the raw `detailLoading`
 * flag. Collecting a sample moved the request out of the Ready lane, which
 * emptied the queue, which made the active selection null -- and the flag was
 * left true by two paths that cannot clear it: an aborted fetch skips its own
 * `finally`, and the "nothing selected" branch of the effect returns without
 * touching state (it must, or it cascades a render). The desk sat on
 * "Loading request…" indefinitely.
 *
 * Deriving makes that impossible rather than merely unlikely: with nothing
 * selected there is nothing to load, and a request already on screen is never
 * loading from an empty state.
 */
export function detailIsLoading(
  activeId: number | null,
  loadingFlag: boolean,
  visible: LabRequestDetail | null,
): boolean {
  if (activeId === null) return false;
  if (visible !== null) return false;
  return loadingFlag;
}

/** Narrowing helper for the styling maps, which are keyed by known status. */
export function isKnownStatus(value: string): value is LabDeskStatus {
  return LAB_DESK_STATUS_ORDER.includes(value);
}

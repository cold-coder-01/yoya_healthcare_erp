/**
 * Radiology Desk: display vocabulary, filtering and route construction.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * decides anything clinical or financial.
 *
 * THE RULES THIS FILE KEEPS:
 *
 *   * The browser FORMATS a lane, it never DERIVES one. Every lane arrives
 *     already decided by the server's rad_desk_lane(); in particular the
 *     browser never looks at a report state or a clearance flag to work out
 *     where a request belongs.
 *   * The browser FORMATS a clearance verdict, it never REACHES one. There is
 *     no amount here because there is none in the payload.
 *   * Every URL built here is a BFF path. The browser never addresses Odoo.
 *   * An action button is an AFFORDANCE, never a permission (Slice 2). It is
 *     drawn from the server's own lane and flags; the server re-checks the same
 *     policy under a row lock on every call.
 */
// TYPE-ONLY, and it has to stay that way: TypeScript erases these imports,
// which is what lets node:test run rad-desk-format.test.ts with no resolver.
import type {
  RadDeskRoles,
  RadQueueRow,
  RadRequestDetail,
  RadTransitionKind,
  RadWorklistSummary,
} from "@/types/rad-desk";

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

/**
 * Runtime copy of the default lanes, in workflow order.
 *
 * DUPLICATED FROM types/rad-desk.ts ON PURPOSE, for the reason
 * lab-desk-format.ts states: this copy must stay free of value imports so the
 * format tests need no resolver. A test asserts the two lists agree.
 */
export const RAD_DESK_ACTIVE_LANE_ORDER: readonly string[] = [
  "awaiting_clearance",
  "to_schedule",
  "ready_to_start",
  "awaiting_report",
  "awaiting_validation",
  "awaiting_release",
  "anomaly",
];

/** Every lane a user may pick, in the order the strip shows them. */
export const RAD_DESK_LANE_ORDER: readonly string[] = [
  ...RAD_DESK_ACTIVE_LANE_ORDER,
  "completed",
  "cancelled",
];

/** The pseudo-lane for "all active work". Never sent to the server as-is. */
export const ACTIVE_LANE_KEY = "active";

/**
 * Fallback labels for the lane keys the SERVER produces. The server's own
 * `lane_label` is what the UI displays; these exist so an unrecognised key
 * still renders as words and so the strip has a label before any row loads.
 */
const LANE_LABELS: Record<string, string> = {
  awaiting_clearance: "Awaiting clearance",
  to_schedule: "To schedule",
  ready_to_start: "Ready to start",
  awaiting_report: "Awaiting report",
  awaiting_validation: "Awaiting validation",
  awaiting_release: "Awaiting release",
  anomaly: "Anomaly",
  completed: "Completed",
  cancelled: "Cancelled",
  draft: "Draft",
};

/**
 * Short codes for the dense queue column. TEXT, NOT COLOUR: every lane carries
 * this code as well as a tone, so the fact survives any colour vision.
 */
const LANE_CODES: Record<string, string> = {
  awaiting_clearance: "CLR",
  to_schedule: "SCHD",
  ready_to_start: "RDY",
  awaiting_report: "RPT",
  awaiting_validation: "VAL",
  awaiting_release: "REL",
  anomaly: "REV",
  completed: "DONE",
  cancelled: "CANC",
  draft: "DRF",
};

const PRIORITY_LABELS: Record<string, string> = {
  routine: "Routine",
  urgent: "Urgent",
  stat: "STAT",
};

const PRIORITY_CODES: Record<string, string> = {
  routine: "RTN",
  urgent: "URG",
  stat: "STAT",
};

export function radLaneLabel(lane: string | null | undefined) {
  if (!lane) return "—";
  if (lane === ACTIVE_LANE_KEY) return "All active";
  return LANE_LABELS[lane] ?? lane;
}

export function radLaneCode(lane: string | null | undefined) {
  if (!lane) return "—";
  if (lane === ACTIVE_LANE_KEY) return "ALL";
  return LANE_CODES[lane] ?? lane.slice(0, 4).toUpperCase();
}

export function radPriorityLabel(priority: string | null | undefined) {
  if (!priority) return "Routine";
  return PRIORITY_LABELS[priority] ?? priority;
}

export function radPriorityCode(priority: string | null | undefined) {
  if (!priority) return "RTN";
  return PRIORITY_CODES[priority] ?? priority.slice(0, 4).toUpperCase();
}

/** Urgent and STAT get a marker; routine does not. Presentation only. */
export function isUrgentPriority(priority: string | null | undefined) {
  return priority === "urgent" || priority === "stat";
}

export function isKnownLane(value: string): boolean {
  return RAD_DESK_LANE_ORDER.includes(value) || value === "draft";
}

/** The lanes a strip entry asks the server for. */
export function laneStatuses(laneKey: string): readonly string[] {
  if (laneKey === ACTIVE_LANE_KEY) return RAD_DESK_ACTIVE_LANE_ORDER;
  return RAD_DESK_LANE_ORDER.includes(laneKey)
    ? [laneKey]
    : RAD_DESK_ACTIVE_LANE_ORDER;
}

/**
 * The one operational sentence about money. AMOUNT-FREE, derived from the
 * server's lane, and null when there is nothing to say.
 *
 * It names no figure, no payer and no invoice because none reaches the browser.
 * The clearance gate itself is Mark In Progress on the server; this only tells
 * the department why a study cannot be started yet.
 */
export function clearanceNotice(lane: string | null | undefined): string | null {
  if (lane === "awaiting_clearance") {
    return "Awaiting financial clearance. The study cannot be started until the patient is cleared at the cashier.";
  }
  return null;
}

/**
 * The sentence shown for an anomaly. The SERVER's wording wins; the fallbacks
 * exist only for a payload that carries none, and are equally generic -- no
 * internal ids, model names or record codes.
 */
export function reviewMessage(
  detail: Pick<RadRequestDetail, "lane" | "anomaly_reason" | "review_message" | "result_conflict">,
): string | null {
  if (detail.review_message && detail.review_message.trim()) {
    return detail.review_message;
  }
  if (detail.result_conflict || detail.anomaly_reason === "result_conflict") {
    return "Multiple active radiology reports were found for this request. Review is required before workflow actions can continue.";
  }
  if (detail.lane === "anomaly") {
    return "This request needs review before workflow actions can continue.";
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
  const age = patient.age ? String(patient.age) : "—";
  const sex = patient.gender ? patient.gender.charAt(0).toUpperCase() : "—";
  return `${age} / ${sex}`;
}

/** Any value the record does not carry renders as an em dash, never blank. */
export function orDash(value: string | null | undefined) {
  const trimmed = typeof value === "string" ? value.trim() : "";
  return trimmed ? trimmed : "—";
}

/** "CT Brain (CT-BRAIN)", or just the name. */
export function examLabel(exam: { name: string; code: string | null } | null | undefined) {
  if (!exam) return "—";
  return exam.code ? `${exam.name} (${exam.code})` : exam.name;
}

/** The queue's exam cell: first study, plus "+2" when there are more. */
export function examCellLabel(row: Pick<RadQueueRow, "first_exam" | "exam_count">) {
  if (!row.first_exam) return "No active study";
  const extra = row.exam_count > 1 ? ` +${row.exam_count - 1}` : "";
  return `${row.first_exam.name}${extra}`;
}

export function studyCountLabel(count: number) {
  return count === 1 ? "1 study" : `${count} studies`;
}

/** "32.1 KB". Metadata display only -- nothing here touches the bytes. */
export function fileSizeLabel(bytes: number | null | undefined) {
  if (!bytes || bytes <= 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The header's role line, from the desk's own four flags. */
export function deskRoleLabel(roles: RadDeskRoles | null | undefined) {
  if (!roles) return null;
  const parts: string[] = [];
  if (roles.radiology_technician) parts.push("Radiology Technician");
  if (roles.radiologist) parts.push("Radiologist");
  if (parts.length === 0 && roles.system_admin) parts.push("System Administrator");
  if (parts.length === 0 && roles.manager) parts.push("Manager");
  return parts.length ? parts.join(" · ") : null;
}

/* ------------------------------------------------------------------ *
 * Lane strip scrolling
 * ------------------------------------------------------------------ */

/**
 * Where the lane strip should scroll so ONE tab is fully visible.
 *
 * THE UAT DEFECT THIS FIXES. The strip holds ten tabs (about 1500px) and scrolls
 * horizontally below that width. Its scroll offset was browser-owned state the
 * desk never managed: once it had been scrolled to reach Completed or Cancelled
 * -- by scrollbar, trackpad or keyboard focus -- selecting a lane did not bring
 * it back, the in-app Refresh kept it, and Back restored it. The desk then
 * opened with the first tabs cut mid-word ("…ng clearance").
 *
 * The FIRST tab always resolves to 0, so "All active" is never partly hidden.
 * Any other tab moves the strip the least distance that shows it whole, with a
 * small gutter so its border is not flush against the edge -- except that when
 * scrolling back LEFT, a tab that fits from the very start resolves to 0, so the
 * tabs before it are not left cut mid-word either. Pure arithmetic;
 * the component only reads the geometry and writes the one number back.
 */
export function laneStripScrollFor(geometry: {
  scrollLeft: number;
  clientWidth: number;
  scrollWidth: number;
  /** The tab's left edge in the strip's CONTENT coordinates. */
  tabLeft: number;
  tabWidth: number;
  isFirst: boolean;
  gutter?: number;
}): number {
  const { scrollLeft, clientWidth, scrollWidth, tabLeft, tabWidth, isFirst } = geometry;
  const gutter = geometry.gutter ?? 8;
  const maxScroll = Math.max(0, scrollWidth - clientWidth);
  if (isFirst) return 0;
  const start = tabLeft - gutter;
  const end = tabLeft + tabWidth + gutter;
  if (start < scrollLeft) {
    if (end <= clientWidth) return 0;
    return Math.min(maxScroll, Math.max(0, start));
  }
  if (end > scrollLeft + clientWidth) {
    return Math.min(maxScroll, Math.max(0, end - clientWidth));
  }
  return Math.min(maxScroll, Math.max(0, scrollLeft));
}

/* ------------------------------------------------------------------ *
 * Queue
 * ------------------------------------------------------------------ */

/**
 * The badge for one lane, read from the SERVER's scope-wide summary.
 *
 * NOTHING IS COUNTED HERE. Rows on screen are narrowed to one lane and one
 * page, so recounting them would make every unclicked lane read 0. `null`
 * means the server could not determine the count; the caller renders a dash.
 */
export function laneCount(
  summary: RadWorklistSummary | null,
  laneKey: string,
): number | null {
  if (!summary) return null;
  const value = (summary as unknown as Record<string, unknown>)[laneKey];
  return typeof value === "number" ? value : null;
}

export function laneCountLabel(
  summary: RadWorklistSummary | null,
  laneKey: string,
): string {
  const value = laneCount(summary, laneKey);
  return value === null ? "—" : String(value);
}

/**
 * Local text filtering over rows the server already scoped and returned. It
 * NARROWS a visible list and can never widen it; the server-side `q` is the
 * real search, and this matches the same identity fields it searches.
 */
export function matchesSearch(row: RadQueueRow, term: string) {
  const needle = term.trim().toLowerCase();
  if (!needle) return true;
  const haystack = [
    row.request_code,
    row.patient?.name ?? "",
    row.patient?.mrn ?? "",
    row.exams_summary ?? "",
    row.first_exam?.code ?? "",
    row.ordering_physician?.name ?? "",
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(needle);
}

/**
 * The selection in force, DERIVED rather than synchronised: when a filter or
 * refresh removes the selected request, fall to the top of what is visible in
 * the same render. Null for an empty queue.
 */
export function resolveSelection(
  rows: RadQueueRow[],
  selectedId: number | null,
): number | null {
  if (selectedId !== null && rows.some((row) => row.id === selectedId)) {
    return selectedId;
  }
  return rows[0]?.id ?? null;
}

/**
 * The request the desk is working on, WITH the post-action pin honoured.
 *
 * Scheduling moves a request out of To schedule and starting moves it out of
 * Ready to start, so the queue refetch drops it from the lane on screen. While
 * a request is pinned it IS the active request -- the Laboratory Desk's proven
 * fix -- so the confirmation stays in front of the user instead of an unrelated
 * top row. A real selection or a lane change clears the pin.
 */
export function activeSelection(
  rows: RadQueueRow[],
  selectedId: number | null,
  justActedId: number | null,
): number | null {
  if (justActedId !== null) return justActedId;
  return resolveSelection(rows, selectedId);
}

/**
 * A detail payload is only shown against the request it belongs to: the
 * active selection, or the request just acted on.
 */
export function visibleDetail(
  detail: RadRequestDetail | null,
  activeId: number | null,
  justActedId: number | null = null,
): RadRequestDetail | null {
  if (!detail) return null;
  if (activeId !== null && detail.id === activeId) return detail;
  if (justActedId !== null && detail.id === justActedId) return detail;
  return null;
}

/**
 * Whether the panel shows its loading state. DERIVED, NEVER STORED -- the
 * Laboratory Desk's UAT hang taught that a raw flag can be left true by an
 * aborted fetch. With nothing selected there is nothing to load, and a request
 * already on screen is never loading from empty.
 */
export function detailIsLoading(
  activeId: number | null,
  loadingFlag: boolean,
  visible: RadRequestDetail | null,
): boolean {
  if (activeId === null) return false;
  if (visible !== null) return false;
  return loadingFlag;
}

/* ------------------------------------------------------------------ *
 * Routes -- BFF paths only
 * ------------------------------------------------------------------ */

export const RAD_SESSION_PATH = "/api/radiology/session";

/**
 * The BFF worklist URL. Empty values are omitted rather than sent as `q=`,
 * which upstream would read as a search for the empty string.
 */
export function worklistPath(params: {
  status?: readonly string[];
  date?: string | null;
  q?: string | null;
  modality?: string | null;
  limit?: number | null;
}) {
  const query = new URLSearchParams();
  if (params.status && params.status.length > 0) {
    query.set("status", params.status.join(","));
  }
  if (params.date) query.set("date", params.date);
  if (params.q && params.q.trim()) query.set("q", params.q.trim());
  if (params.modality) query.set("modality", params.modality);
  if (params.limit) query.set("limit", String(params.limit));
  const queryString = query.toString();
  return queryString
    ? `/api/radiology/worklist?${queryString}`
    : "/api/radiology/worklist";
}

export function requestPath(requestId: number) {
  return `/api/radiology/requests/${requestId}`;
}

/** The BFF schedule route. */
export function schedulePath(requestId: number) {
  return `/api/radiology/requests/${requestId}/schedule`;
}

/** The BFF start-exam route. */
export function startPath(requestId: number) {
  return `/api/radiology/requests/${requestId}/start`;
}

/** The only two paths this desk ever POSTs to. */
export function transitionPath(kind: RadTransitionKind, requestId: number) {
  return kind === "schedule" ? schedulePath(requestId) : startPath(requestId);
}

/* ------------------------------------------------------------------ *
 * Actions (Slice 2)
 * ------------------------------------------------------------------ */

type ActionFacts = Pick<
  RadRequestDetail,
  "state" | "lane" | "billing_blocked" | "result_conflict" | "anomaly_reason" | "exam_count"
>;

function actionable(detail: ActionFacts | null | undefined): detail is ActionFacts {
  return (
    !!detail &&
    detail.billing_blocked === false &&
    detail.result_conflict === false &&
    detail.anomaly_reason === null &&
    detail.lane !== "anomaly" &&
    detail.exam_count > 0
  );
}

/**
 * May the desk OFFER Schedule study for this request?
 *
 * Every condition, together: requested, in To schedule, not blocked, no report
 * conflict or other anomaly, at least one active study. It mirrors the server's
 * policy exactly -- and it is an affordance, never a permission: the endpoint
 * re-checks all of it under a row lock.
 */
export function canScheduleStudy(detail: ActionFacts | null | undefined): boolean {
  return actionable(detail) && detail.state === "requested" && detail.lane === "to_schedule";
}

/** May the desk OFFER Start exam? The same shape, for scheduled / Ready to start. */
export function canStartExam(detail: ActionFacts | null | undefined): boolean {
  return actionable(detail) && detail.state === "scheduled" && detail.lane === "ready_to_start";
}

/** "Brain CT Scan", "Brain CT Scan +1 more", or a neutral phrase for none. */
export function studyPhrase(
  detail: Pick<RadRequestDetail, "first_exam" | "exam_count">,
): string {
  if (!detail.first_exam) return "the ordered study";
  const more = detail.exam_count > 1 ? ` +${detail.exam_count - 1} more` : "";
  return `${detail.first_exam.name}${more}`;
}

function whoLabel(detail: Pick<RadRequestDetail, "patient">): string {
  const name = detail.patient?.name ?? "this patient";
  const mrn = detail.patient?.mrn;
  return mrn ? `${name} (${mrn})` : name;
}

type ConfirmFacts = Pick<RadRequestDetail, "request_code" | "patient" | "first_exam" | "exam_count">;

/** "Schedule RADREQ0411 for Bezabeh Ketema (HMS11832) — Brain CT Scan?" */
export function scheduleConfirmText(detail: ConfirmFacts): string {
  return `Schedule ${detail.request_code} for ${whoLabel(detail)} — ${studyPhrase(detail)}?`;
}

/**
 * NO DATE, TIME OR SLOT IS CLAIMED. The model records none, so the support text
 * says so plainly rather than letting "scheduled" imply a booking.
 */
export const SCHEDULE_SUPPORT_TEXT =
  "This moves the study to the Radiology ready-to-start queue. No appointment time or imaging slot is created.";

/** "Start Brain CT Scan for Bezabeh Ketema (HMS11832)?" */
export function startConfirmText(detail: ConfirmFacts): string {
  return `Start ${studyPhrase(detail)} for ${whoLabel(detail)}?`;
}

/** No performed-at or performed-by is claimed: the model records neither. */
export const START_SUPPORT_TEXT =
  "This confirms the study is beginning now and moves it into active imaging work.";

/** The confirmation shown for the request just acted on, from the SERVER's lane. */
export function transitionOutcomeText(
  kind: RadTransitionKind,
  detail: Pick<RadRequestDetail, "lane_label">,
): string {
  const verb = kind === "schedule" ? "Scheduled" : "Exam started";
  return `${verb}. The request is now in ${detail.lane_label}.`;
}

/**
 * The message shown when an action is refused. The server's sentence is
 * preferred: the Radiology API has already replaced every refusal that could
 * carry billing text with a fixed, amount-free one. The fallback is for a
 * transport failure, where there is no server sentence at all.
 */
export function transitionErrorMessage(
  serverMessage: string | null | undefined,
  kind: RadTransitionKind,
): string {
  const trimmed = typeof serverMessage === "string" ? serverMessage.trim() : "";
  if (trimmed) return trimmed;
  return kind === "schedule"
    ? "The study could not be scheduled. Nothing was changed."
    : "The exam could not be started. Nothing was changed.";
}

/**
 * Refusals after which the screen no longer matches the database, so the desk
 * RE-READS instead of leaving a button the server has just refused.
 */
const RECONCILE_CODES = new Set([
  "radiology_request_not_found",
  "radiology_request_not_schedulable",
  "radiology_request_not_startable",
  "radiology_request_state_conflict",
  "radiology_request_awaiting_clearance",
  "radiology_request_start_blocked",
  "radiology_request_needs_review",
  "radiology_request_no_active_study",
]);

export function shouldReconcileAfterTransition(code: string | null | undefined): boolean {
  return typeof code === "string" && RECONCILE_CODES.has(code);
}

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
  RadDeskCapabilities,
  RadDeskRoles,
  RadImageMetadata,
  RadOperationalResult,
  RadQueueRow,
  RadReportDraftBody,
  RadRequestDetail,
  RadSignoffKind,
  RadSignoffResponse,
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

/* ------------------------------------------------------------------ *
 * The report (Slice 3)
 * ------------------------------------------------------------------ */

/** Find or create THE operational report of a request. Bodiless. */
export function reportPath(requestId: number) {
  return `/api/radiology/requests/${requestId}/report`;
}

/** Save a draft report's allow-listed text. */
export function reportSavePath(resultId: number) {
  return `/api/radiology/results/${resultId}/save`;
}

/** Save and mark entered, in one act. */
export function reportEnterPath(resultId: number) {
  return `/api/radiology/results/${resultId}/enter`;
}

/**
 * May the desk OFFER "Open report" for this request?
 *
 * In progress, in Awaiting report, with an active study and no conflict or
 * other anomaly: the one lane where the server may still have to CREATE the
 * draft. Every later lane VIEWS the report it already has (canViewReport). An
 * affordance, never a permission: the endpoint re-checks the request state and
 * the one-report rule under a row lock.
 */
export function canOpenReport(
  detail:
    | Pick<RadRequestDetail, "state" | "lane" | "result_conflict" | "anomaly_reason" | "exam_count">
    | null
    | undefined,
): boolean {
  return (
    !!detail &&
    detail.state === "in_progress" &&
    detail.lane === "awaiting_report" &&
    detail.result_conflict === false &&
    detail.anomaly_reason === null &&
    detail.exam_count > 0
  );
}

/**
 * May THIS user edit THIS report? A draft, and a report-author role. The
 * technician opens the report and reads it; the server refuses their writes
 * with 403 whatever the screen shows.
 */
export function reportEditable(
  result: Pick<RadOperationalResult, "state"> | null | undefined,
  capabilities: Pick<RadDeskCapabilities, "edit_report" | "enter_report"> | null | undefined,
): boolean {
  return (
    !!result &&
    result.state === "draft" &&
    capabilities?.edit_report === true &&
    capabilities?.enter_report === true
  );
}

/** What the modal edits: every field as a string, "" for nothing. */
export type RadReportDraft = {
  findings: string;
  impression: string;
  recommendations: string;
  lines: { id: number; result_summary: string; notes: string }[];
};

export function reportDraftFromResult(
  result: Pick<RadOperationalResult, "findings" | "impression" | "recommendations" | "lines">,
): RadReportDraft {
  return {
    findings: result.findings ?? "",
    impression: result.impression ?? "",
    recommendations: result.recommendations ?? "",
    lines: result.lines.map((line) => ({
      id: line.id,
      result_summary: line.result_summary ?? "",
      notes: line.notes ?? "",
    })),
  };
}

export function reportDraftChanged(a: RadReportDraft, b: RadReportDraft): boolean {
  if (
    a.findings !== b.findings ||
    a.impression !== b.impression ||
    a.recommendations !== b.recommendations ||
    a.lines.length !== b.lines.length
  ) {
    return true;
  }
  return a.lines.some((line, index) => {
    const other = b.lines[index];
    return (
      line.id !== other.id ||
      line.result_summary !== other.result_summary ||
      line.notes !== other.notes
    );
  });
}

export function patchReportLine(
  draft: RadReportDraft,
  lineId: number,
  patch: Partial<{ result_summary: string; notes: string }>,
): RadReportDraft {
  return {
    ...draft,
    lines: draft.lines.map((line) => (line.id === lineId ? { ...line, ...patch } : line)),
  };
}

/**
 * THE ONLY BODY THE REPORT ROUTES ARE SENT. Exactly the allow-listed keys --
 * findings, impression, recommendations, and per line its id, summary and
 * notes -- with "" sent as null. Nothing is trimmed: whether whitespace is a
 * report is the server's entry gate to decide, and the draft is stored as typed.
 */
export function reportBody(draft: RadReportDraft): RadReportDraftBody {
  const orNull = (value: string) => (value === "" ? null : value);
  return {
    findings: orNull(draft.findings),
    impression: orNull(draft.impression),
    recommendations: orNull(draft.recommendations),
    lines: draft.lines.map((line) => ({
      id: line.id,
      result_summary: orNull(line.result_summary),
      notes: orNull(line.notes),
    })),
  };
}

/** The serialized body for the one POST site. Built only from reportBody(). */
export function serializeReportBody(draft: RadReportDraft): string {
  return JSON.stringify(reportBody(draft));
}

/** "Mark RADRES0012 as entered?" */
export function reportEnterConfirmText(result: Pick<RadOperationalResult, "name">): string {
  return `Mark ${result.name} as entered?`;
}

export const REPORT_ENTER_SUPPORT_TEXT =
  "This locks the current report text for review before validation. The ordering clinician will still not see the report until it is released.";

/**
 * The header context of the report modal, as label/value pairs, from the
 * server's own payloads. Nothing priced, nothing invented.
 */
export function reportContext(
  detail: Pick<
    RadRequestDetail,
    | "patient"
    | "request_code"
    | "exams_summary"
    | "modality_label"
    | "body_part"
    | "ordering_physician"
  >,
  result: Pick<RadOperationalResult, "name" | "radiologist">,
): { label: string; value: string }[] {
  return [
    { label: "Patient", value: orDash(detail.patient?.name) },
    { label: "MRN", value: orDash(detail.patient?.mrn) },
    { label: "Request", value: detail.request_code },
    { label: "Report", value: result.name },
    { label: "Exams", value: orDash(detail.exams_summary) },
    { label: "Modality", value: orDash(detail.modality_label) },
    { label: "Body part", value: orDash(detail.body_part) },
    { label: "Ordering doctor", value: orDash(detail.ordering_physician?.name) },
    // Slice 5: who entered -- and so signs -- the report. Set by the server.
    { label: "Radiologist", value: orDash(result.radiologist) },
  ];
}

/** The confirmation shown for the request whose report was just entered. */
export function reportEnteredOutcomeText(
  result: Pick<RadOperationalResult, "name">,
  request: Pick<RadRequestDetail, "lane_label">,
): string {
  return `Report ${result.name} entered. The request is now in ${request.lane_label}.`;
}

/** The status line under the report editor. */
export function reportStatusText(state: {
  readOnly: boolean;
  busy: "save" | "enter" | null;
  changed: boolean;
  savedSinceOpen: boolean;
}): string {
  if (state.busy === "save") return "Saving draft…";
  if (state.busy === "enter") return "Marking entered…";
  if (state.readOnly) return "Read only.";
  if (state.changed) return "Unsaved changes.";
  if (state.savedSinceOpen) return "Draft saved.";
  return "No changes yet.";
}

/**
 * A report refusal's message. The server's sentence is preferred -- the
 * Radiology API's report routes carry no billing text -- and the fallback is
 * for a transport failure, where there is no server sentence at all.
 */
export function reportErrorMessage(
  serverMessage: string | null | undefined,
  kind: "open" | "save" | "enter",
): string {
  const trimmed = typeof serverMessage === "string" ? serverMessage.trim() : "";
  if (trimmed) return trimmed;
  if (kind === "open") return "The report could not be opened. Nothing was changed.";
  if (kind === "save") return "The draft could not be saved. Nothing was changed.";
  return "The report could not be marked entered. Nothing was changed.";
}

/**
 * Refusals after which the screen no longer matches the database, so the desk
 * RE-READS the request instead of leaving a stale button or report on screen.
 */
const REPORT_RECONCILE_CODES = new Set([
  "radiology_request_not_found",
  "radiology_result_not_found",
  "radiology_report_not_available",
  "radiology_request_no_active_study",
  "radiology_result_ambiguous",
  "radiology_result_not_editable",
  "radiology_result_state_conflict",
]);

export function shouldReconcileAfterReport(code: string | null | undefined): boolean {
  return typeof code === "string" && REPORT_RECONCILE_CODES.has(code);
}

/* ------------------------------------------------------------------ *
 * Images (Slice 4)
 * ------------------------------------------------------------------ */

/** Upload one file to a report. multipart/form-data. */
export function imagesPath(resultId: number) {
  return `/api/radiology/results/${resultId}/images`;
}

/**
 * The desk's OWN byte route for one file of one report -- never a
 * /web/content link, never the Doctor's released-only route. `download` asks
 * for an attachment disposition; the default opens inline.
 */
export function imagePath(resultId: number, imageId: number, download = false) {
  const base = `/api/radiology/results/${resultId}/images/${imageId}`;
  return download ? `${base}?disposition=attachment` : base;
}

/** Remove one file from a report. Bodiless. */
export function imageRemovePath(resultId: number, imageId: number) {
  return `/api/radiology/results/${resultId}/images/${imageId}/remove`;
}

/**
 * May THIS user change THIS report's images? The SERVER's images_mutable --
 * derived from the image model's own states, and true for an entered report
 * whose text is frozen -- and the manage_images capability. The report text's
 * editability (reportEditable) is a separate question with a separate answer.
 */
export function imagesEditable(
  result: Pick<RadOperationalResult, "images_mutable"> | null | undefined,
  capabilities: Pick<RadDeskCapabilities, "manage_images"> | null | undefined,
): boolean {
  return !!result && result.images_mutable === true && capabilities?.manage_images === true;
}

/** What the file picker offers. The SERVER still decides by the bytes. */
export const ACCEPTED_IMAGE_TYPES = "image/jpeg,image/png,application/pdf";
export const ACCEPTED_IMAGE_LABEL = "JPG, PNG or PDF";
export const MAX_IMAGE_BYTES = 25 * 1024 * 1024;
export const MAX_IMAGE_LABEL = "Max file size: 25 MB";

/** A local, advisory size check, so a huge file is not sent at all. */
export function imageTooLarge(file: Pick<File, "size">): boolean {
  return file.size > MAX_IMAGE_BYTES;
}

export const IMAGE_TOO_LARGE_TEXT =
  "This file is larger than 25 MB, the limit for one radiology file. Choose a smaller export.";

/**
 * THE ONLY UPLOAD BODY: one file under `file`, and a caption if one was typed.
 * No type, no size, no name field -- the server reads those from the bytes.
 */
export function imageUploadForm(file: File, caption?: string | null): FormData {
  const form = new FormData();
  form.append("file", file, file.name);
  const trimmed = typeof caption === "string" ? caption.trim() : "";
  if (trimmed) form.append("caption", trimmed);
  return form;
}

/** How a file is shown: a preview for JPEG/PNG, an Open item for a PDF. */
export function imageKind(
  image: Pick<RadImageMetadata, "mimetype">,
): "image" | "pdf" | "other" {
  if (image.mimetype === "image/jpeg" || image.mimetype === "image/png") return "image";
  if (image.mimetype === "application/pdf") return "pdf";
  return "other";
}

export function imageTypeLabel(image: Pick<RadImageMetadata, "mimetype">): string {
  if (image.mimetype === "image/jpeg") return "JPEG";
  if (image.mimetype === "image/png") return "PNG";
  if (image.mimetype === "application/pdf") return "PDF";
  return "File";
}

/** "Remove axial.png from this radiology report?" */
export function imageRemoveConfirmText(image: Pick<RadImageMetadata, "filename">): string {
  return `Remove ${image.filename} from this radiology report?`;
}

export const IMAGE_REMOVE_SUPPORT_TEXT =
  "This removes the uploaded file from the report. This action is unavailable after validation.";

/** An image refusal's message: the server's sentence, else a fallback. */
export function imageErrorMessage(
  serverMessage: string | null | undefined,
  kind: "upload" | "remove",
): string {
  const trimmed = typeof serverMessage === "string" ? serverMessage.trim() : "";
  if (trimmed) return trimmed;
  return kind === "upload"
    ? "The file could not be uploaded. Nothing was changed."
    : "The file could not be removed. Nothing was changed.";
}

/** Image refusals after which the report on screen is stale and is re-read. */
const IMAGE_RECONCILE_CODES = new Set([
  "radiology_result_not_found",
  "radiology_image_not_found",
  "radiology_image_not_editable",
]);

export function shouldReconcileAfterImage(code: string | null | undefined): boolean {
  return typeof code === "string" && IMAGE_RECONCILE_CODES.has(code);
}

/* ------------------------------------------------------------------ *
 * Validation and release (Slice 5)
 * ------------------------------------------------------------------ */

export function reportValidatePath(resultId: number) {
  return `/api/radiology/results/${resultId}/validate`;
}

export function reportReleasePath(resultId: number) {
  return `/api/radiology/results/${resultId}/release`;
}

/** The only two sign-off paths. */
export function signoffPath(kind: RadSignoffKind, resultId: number) {
  return kind === "validate" ? reportValidatePath(resultId) : reportReleasePath(resultId);
}

type SignoffFacts = Pick<
  RadRequestDetail,
  "state" | "lane" | "result_conflict" | "anomaly_reason" | "result"
>;

function signable(detail: SignoffFacts | null | undefined): detail is SignoffFacts {
  return (
    !!detail &&
    detail.state === "in_progress" &&
    detail.result !== null &&
    detail.result_conflict === false &&
    detail.anomaly_reason === null
  );
}

/**
 * "View report": the report the request ALREADY has, read from the detail
 * payload -- no request is sent. Every lane past Awaiting report, including
 * Completed; never on a conflict, where no report is authoritative.
 */
export function canViewReport(
  detail: Pick<RadRequestDetail, "lane" | "result" | "result_conflict"> | null | undefined,
): boolean {
  return (
    !!detail &&
    detail.result !== null &&
    detail.result_conflict === false &&
    detail.lane !== "awaiting_report"
  );
}

/**
 * "Validate report": an in-progress study in Awaiting validation -- the
 * server's lane for an ENTERED report -- and a report-author role. Never read
 * from the report's own state here: the lane already says it.
 */
export function canValidateReport(
  detail: SignoffFacts | null | undefined,
  capabilities: Pick<RadDeskCapabilities, "validate_report"> | null | undefined,
): boolean {
  return (
    capabilities?.validate_report === true &&
    signable(detail) &&
    detail.lane === "awaiting_validation"
  );
}

/** "Release report": Awaiting release -- a VALIDATED report -- and an author. */
export function canReleaseReport(
  detail: SignoffFacts | null | undefined,
  capabilities: Pick<RadDeskCapabilities, "release_report"> | null | undefined,
): boolean {
  return (
    capabilities?.release_report === true &&
    signable(detail) &&
    detail.lane === "awaiting_release"
  );
}

type SignoffCopyFacts = Pick<
  RadRequestDetail,
  "patient" | "first_exam" | "exam_count" | "ordering_physician" | "request_code"
>;

function patientLabel(detail: Pick<RadRequestDetail, "patient">): string {
  const name = detail.patient?.name ?? "this patient";
  const mrn = detail.patient?.mrn;
  return mrn ? `${name} (${mrn})` : name;
}

export const VALIDATE_REVIEW_TEXT =
  "Review the report and all attached imaging before validation. Validation freezes the report and uploaded files for final release.";

/** "Validate RADRES0075 for Bezabeh Ketema (HMS11832) — Brain CT Scan?" */
export function validateConfirmText(
  detail: SignoffCopyFacts,
  result: Pick<RadOperationalResult, "name">,
): string {
  return `Validate ${result.name} for ${patientLabel(detail)} — ${studyPhrase(detail)}?`;
}

export const VALIDATE_SUPPORT_TEXT =
  "This cannot be undone from the Radiology Desk. The ordering clinician will still not see the report until it is released.";

export const RELEASE_REVIEW_TEXT =
  "Review the validated report before release. Releasing publishes the report and imaging to the ordering clinician immediately.";

/** "Release RADRES0075 to Dr. Hana Bekele?" */
export function releaseConfirmText(
  detail: SignoffCopyFacts,
  result: Pick<RadOperationalResult, "name">,
): string {
  const doctor = detail.ordering_physician?.name ?? "the ordering clinician";
  return `Release ${result.name} to ${doctor}?`;
}

/** "Bezabeh Ketema (HMS11832) · Brain CT Scan" */
export function releaseIdentityText(detail: SignoffCopyFacts): string {
  return `${patientLabel(detail)} · ${studyPhrase(detail)}`;
}

export function releaseSupportText(detail: Pick<RadRequestDetail, "request_code">): string {
  return `The report and attached images become visible on the Doctor Desk immediately. ${detail.request_code} will complete when all ordered studies are released.`;
}

/**
 * What the desk says after a CONFIRMED sign-off, from the server's payload.
 * A release that did not complete the request says so, with the server's own
 * review sentence -- never a guess about why.
 */
export function signoffOutcomeText(
  kind: RadSignoffKind,
  response: Pick<RadSignoffResponse, "request_completed"> & {
    request: Pick<RadRequestDetail, "lane_label" | "review_message">;
  },
): string {
  if (kind === "validate") {
    return `Validated · The request is now in ${response.request.lane_label}.`;
  }
  if (response.request_completed) return "Released · Request completed";
  const blocker = response.request.review_message?.trim();
  return blocker ? `Released · Request still in progress. ${blocker}` : "Released · Request still in progress";
}

/** A sign-off refusal: the server's fixed sentence, else a fallback. */
export function signoffErrorMessage(
  serverMessage: string | null | undefined,
  kind: RadSignoffKind,
): string {
  const trimmed = typeof serverMessage === "string" ? serverMessage.trim() : "";
  if (trimmed) return trimmed;
  return kind === "validate"
    ? "The radiology report could not be validated. Nothing was changed."
    : "The radiology report could not be released. Nothing was changed.";
}

const SIGNOFF_RECONCILE_CODES = new Set([
  "radiology_result_not_found",
  "radiology_result_not_validatable",
  "radiology_result_not_releasable",
  "radiology_result_ambiguous",
  "radiology_result_state_conflict",
]);

/** Sign-off refusals after which the screen is stale and is re-read. */
export function shouldReconcileAfterSignoff(code: string | null | undefined): boolean {
  return typeof code === "string" && SIGNOFF_RECONCILE_CODES.has(code);
}

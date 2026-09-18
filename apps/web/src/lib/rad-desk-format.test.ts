/**
 * Radiology Desk display helpers.
 *
 * Run with `npm test` -- Node's built-in runner executing TypeScript directly.
 * No resolver and no DOM: these are the pure functions the workstation renders
 * through, so the rules they keep are pinned where they would regress.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// TYPE-ONLY: erased at runtime, so no path alias has to resolve.
import type {
  RadDeskCapabilities,
  RadOperationalResult,
  RadQueueRow,
  RadRequestDetail,
  RadWorklistSummary,
} from "@/types/rad-desk";

import {
  ACTIVE_LANE_KEY,
  RAD_DESK_ACTIVE_LANE_ORDER,
  RAD_DESK_LANE_ORDER,
  RAD_SESSION_PATH,
  REPORT_ENTER_SUPPORT_TEXT,
  SCHEDULE_SUPPORT_TEXT,
  START_SUPPORT_TEXT,
  activeSelection,
  ageSexLabel,
  ACCEPTED_IMAGE_LABEL,
  ACCEPTED_IMAGE_TYPES,
  IMAGE_REMOVE_SUPPORT_TEXT,
  MAX_IMAGE_BYTES,
  MAX_IMAGE_LABEL,
  RELEASE_REVIEW_TEXT,
  VALIDATE_REVIEW_TEXT,
  VALIDATE_SUPPORT_TEXT,
  canOpenReport,
  canReleaseReport,
  canValidateReport,
  canViewReport,
  releaseConfirmText,
  releaseIdentityText,
  releaseSupportText,
  reportReleasePath,
  reportValidatePath,
  shouldReconcileAfterSignoff,
  signoffErrorMessage,
  signoffOutcomeText,
  signoffPath,
  validateConfirmText,
  imageErrorMessage,
  imageKind,
  imagePath,
  imageRemoveConfirmText,
  imageRemovePath,
  imageTooLarge,
  imageTypeLabel,
  imageUploadForm,
  imagesEditable,
  imagesPath,
  shouldReconcileAfterImage,
  canScheduleStudy,
  canStartExam,
  clearanceNotice,
  deskRoleLabel,
  detailIsLoading,
  examCellLabel,
  examLabel,
  fileSizeLabel,
  isKnownLane,
  isUrgentPriority,
  laneStripScrollFor,
  laneCount,
  laneCountLabel,
  laneStatuses,
  matchesSearch,
  orDash,
  patchReportLine,
  radLaneCode,
  radLaneLabel,
  radPriorityCode,
  radPriorityLabel,
  reportBody,
  reportContext,
  reportDraftChanged,
  reportDraftFromResult,
  reportEditable,
  reportEnterConfirmText,
  reportEnterPath,
  reportEnteredOutcomeText,
  reportErrorMessage,
  reportPath,
  reportSavePath,
  reportStatusText,
  requestPath,
  resolveSelection,
  reviewMessage,
  serializeReportBody,
  shouldReconcileAfterReport,
  scheduleConfirmText,
  schedulePath,
  shouldReconcileAfterTransition,
  startConfirmText,
  startPath,
  studyCountLabel,
  studyPhrase,
  transitionErrorMessage,
  transitionOutcomeText,
  transitionPath,
  visibleDetail,
  worklistPath,
} from "./rad-desk-format.ts";

const TYPES_SOURCE = readFileSync(new URL("../types/rad-desk.ts", import.meta.url), "utf8");

function constFromTypes(name: string): string[] {
  const match = TYPES_SOURCE.match(new RegExp(`export const ${name} = \\[([^\\]]*)\\]`));
  assert.ok(match, `${name} must be declared in types/rad-desk.ts`);
  return [...match[1].matchAll(/"([^"]+)"/g)].map((entry) => entry[1]);
}

function row(overrides: Partial<RadQueueRow> = {}): RadQueueRow {
  return {
    id: 1,
    request_code: "RADREQ0411",
    state: "requested",
    lane: "to_schedule",
    lane_label: "To schedule",
    anomaly_reason: null,
    priority: "routine",
    priority_label: "Routine",
    request_date: "2026-08-24",
    created_at: "2026-08-24T09:00:00",
    billing_blocked: false,
    active: true,
    patient: { id: 7, name: "Bezabeh Ketema", mrn: "HMS11832", age: 41, gender: "male" },
    ordering_physician: { id: 3, name: "Dr. Hana Bekele" },
    exam_count: 1,
    first_exam: { id: 3, name: "Brain CT Scan", code: "CT-BRAIN" },
    modality: "ct",
    modality_label: "CT Scan",
    body_part: "Brain",
    exams_summary: "Brain CT Scan",
    result: null,
    result_conflict: false,
    result_count: 0,
    ...overrides,
  };
}

function detail(overrides: Partial<RadRequestDetail> = {}): RadRequestDetail {
  const { result: _result, ...base } = row();
  void _result;
  return {
    ...base,
    clinical_indication: null,
    instructions: null,
    completed_at: null,
    ordered_from_consultation: true,
    exams: [],
    cancelled_exam_count: 0,
    result: null,
    review_message: null,
    ...overrides,
  };
}

const SUMMARY: RadWorklistSummary = {
  active: 6,
  awaiting_clearance: 1,
  to_schedule: 2,
  ready_to_start: 0,
  awaiting_report: 1,
  awaiting_validation: 1,
  awaiting_release: 0,
  anomaly: 1,
  completed: 12,
  cancelled: 3,
  work_total: 6,
};

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

test("the runtime lane lists agree with the wire contract", () => {
  assert.deepEqual([...RAD_DESK_ACTIVE_LANE_ORDER], constFromTypes("RAD_DESK_ACTIVE_LANES"));
  assert.deepEqual(
    [...RAD_DESK_LANE_ORDER].sort(),
    constFromTypes("RAD_DESK_LANES").sort(),
  );
});

test("draft is never a pickable lane", () => {
  assert.ok(!RAD_DESK_LANE_ORDER.includes("draft"));
  assert.ok(!constFromTypes("RAD_DESK_LANES").includes("draft"));
  assert.ok(isKnownLane("draft"), "a draft opened by id still renders");
});

test("completed and cancelled are pickable but not default", () => {
  for (const lane of ["completed", "cancelled"]) {
    assert.ok(RAD_DESK_LANE_ORDER.includes(lane));
    assert.ok(!RAD_DESK_ACTIVE_LANE_ORDER.includes(lane));
  }
  assert.ok(RAD_DESK_ACTIVE_LANE_ORDER.includes("anomaly"));
});

test("every lane has words and a text code", () => {
  for (const lane of [...RAD_DESK_LANE_ORDER, "draft"]) {
    assert.notEqual(radLaneLabel(lane), lane, `${lane} needs a label`);
    assert.match(radLaneCode(lane), /^[A-Z]{3,4}$/);
  }
  assert.equal(radLaneLabel("awaiting_validation"), "Awaiting validation");
  assert.equal(radLaneLabel(ACTIVE_LANE_KEY), "All active");
  assert.equal(radLaneCode(ACTIVE_LANE_KEY), "ALL");
  assert.equal(radLaneLabel(null), "—");
  // An unknown key still renders as itself rather than as blank.
  assert.equal(radLaneLabel("future_lane"), "future_lane");
});

test("priority pills read as words and codes", () => {
  assert.equal(radPriorityLabel("stat"), "STAT");
  assert.equal(radPriorityCode("urgent"), "URG");
  assert.equal(radPriorityCode(null), "RTN");
  assert.equal(isUrgentPriority("stat"), true);
  assert.equal(isUrgentPriority("urgent"), true);
  assert.equal(isUrgentPriority("routine"), false);
});

test("lane selection maps to the statuses the server is asked for", () => {
  assert.deepEqual([...laneStatuses(ACTIVE_LANE_KEY)], [...RAD_DESK_ACTIVE_LANE_ORDER]);
  assert.deepEqual([...laneStatuses("completed")], ["completed"]);
  assert.deepEqual([...laneStatuses("anomaly")], ["anomaly"]);
  // An unknown or draft key falls back to active work, never to "everything".
  assert.deepEqual([...laneStatuses("draft")], [...RAD_DESK_ACTIVE_LANE_ORDER]);
});

/* ------------------------------------------------------------------ *
 * Counts
 * ------------------------------------------------------------------ */

test("lane counts are read from the server summary, including All active", () => {
  assert.equal(laneCount(SUMMARY, "to_schedule"), 2);
  assert.equal(laneCount(SUMMARY, ACTIVE_LANE_KEY), 6);
  assert.equal(laneCount(SUMMARY, "completed"), 12);
  assert.equal(laneCountLabel(SUMMARY, "ready_to_start"), "0");
});

test("an unknown count is a dash, never a zero", () => {
  assert.equal(laneCountLabel(null, "to_schedule"), "—");
  const capped = { ...SUMMARY, awaiting_clearance: null, active: null };
  assert.equal(laneCountLabel(capped, "awaiting_clearance"), "—");
  assert.equal(laneCountLabel(capped, ACTIVE_LANE_KEY), "—");
});

/* ------------------------------------------------------------------ *
 * Money-free wording
 * ------------------------------------------------------------------ */

test("the clearance notice appears only for awaiting clearance and names no figure", () => {
  const notice = clearanceNotice("awaiting_clearance");
  assert.ok(notice);
  assert.doesNotMatch(notice, /\d/);
  assert.doesNotMatch(notice, /amount|birr|etb|invoice|receipt|balance|price/i);
  for (const lane of ["to_schedule", "ready_to_start", "anomaly", "completed"]) {
    assert.equal(clearanceNotice(lane), null);
  }
});

test("the review message prefers the server's sentence", () => {
  assert.equal(
    reviewMessage(detail({ lane: "anomaly", anomaly_reason: "released_not_completed", review_message: "Server says review." })),
    "Server says review.",
  );
});

test("a conflict is stated generically when the server sends no sentence", () => {
  const message = reviewMessage(
    detail({ lane: "anomaly", anomaly_reason: "result_conflict", result_conflict: true }),
  );
  assert.equal(
    message,
    "Multiple active radiology reports were found for this request. Review is required before workflow actions can continue.",
  );
  assert.doesNotMatch(message ?? "", /RADRES|hospital\.|id\s*\d/i);
  assert.equal(reviewMessage(detail({ lane: "to_schedule" })), null);
});

/* ------------------------------------------------------------------ *
 * Display
 * ------------------------------------------------------------------ */

test("patient identity renders age and sex, or dashes", () => {
  assert.equal(ageSexLabel(row().patient), "41 / M");
  assert.equal(ageSexLabel({ age: null, gender: null }), "— / —");
  assert.equal(ageSexLabel(null), "—");
  assert.equal(orDash("  "), "—");
  assert.equal(orDash("HMS11832"), "HMS11832");
});

test("the exam cell shows the first study and how many more", () => {
  assert.equal(examCellLabel(row()), "Brain CT Scan");
  assert.equal(examCellLabel(row({ exam_count: 3 })), "Brain CT Scan +2");
  assert.equal(examCellLabel(row({ first_exam: null, exam_count: 0 })), "No active study");
  assert.equal(examLabel({ name: "Brain CT Scan", code: "CT-BRAIN" }), "Brain CT Scan (CT-BRAIN)");
  assert.equal(examLabel({ name: "Chest X-Ray", code: null }), "Chest X-Ray");
  assert.equal(studyCountLabel(1), "1 study");
  assert.equal(studyCountLabel(2), "2 studies");
});

test("image metadata sizes render without touching bytes", () => {
  assert.equal(fileSizeLabel(32838), "32.1 KB");
  assert.equal(fileSizeLabel(512), "512 B");
  assert.equal(fileSizeLabel(3 * 1024 * 1024), "3.0 MB");
  assert.equal(fileSizeLabel(0), "—");
});

test("the role line uses the desk's own four flags", () => {
  const none = { radiology_technician: false, radiologist: false, manager: false, system_admin: false };
  assert.equal(deskRoleLabel({ ...none, radiology_technician: true }), "Radiology Technician");
  assert.equal(
    deskRoleLabel({ ...none, radiology_technician: true, radiologist: true }),
    "Radiology Technician · Radiologist",
  );
  assert.equal(deskRoleLabel({ ...none, manager: true }), "Manager");
  assert.equal(deskRoleLabel({ ...none, manager: true, system_admin: true }), "System Administrator");
  assert.equal(deskRoleLabel(none), null);
  assert.equal(deskRoleLabel(null), null);
});

/* ------------------------------------------------------------------ *
 * Lane strip scrolling -- the UAT clipping defect
 * ------------------------------------------------------------------ */

// A strip 974px wide showing 1519px of tabs, as measured at a 1024px viewport.
const STRIP = { clientWidth: 974, scrollWidth: 1519 };

test("the first lane always scrolls the strip fully back to the start", () => {
  // The UAT state: scrolled 289px, "All active" hidden, "Awaiting clearance" cut.
  assert.equal(
    laneStripScrollFor({ ...STRIP, scrollLeft: 289, tabLeft: 0, tabWidth: 119, isFirst: true }),
    0,
  );
  assert.equal(
    laneStripScrollFor({ ...STRIP, scrollLeft: 0, tabLeft: 0, tabWidth: 119, isFirst: true }),
    0,
  );
});

test("a lane cut off on the left that fits from the start resets the strip to 0", () => {
  // "Awaiting clearance" (123-300px) while the strip sits at 289px: the exact
  // UAT view. It fits from the start, so nothing before it is left cut either.
  assert.equal(
    laneStripScrollFor({ ...STRIP, scrollLeft: 289, tabLeft: 123, tabWidth: 177, isFirst: false }),
    0,
  );
});

test("a lane cut off on the left further along scrolls just far enough", () => {
  // A narrow 400px strip: a tab at 600-770px cannot be shown from the start,
  // so the strip moves only as far as the tab plus its gutter.
  const narrow = { clientWidth: 400, scrollWidth: 1519 };
  const next = laneStripScrollFor({ ...narrow, scrollLeft: 700, tabLeft: 600, tabWidth: 170, isFirst: false });
  assert.equal(next, 592);
  assert.ok(next <= 600 && next + narrow.clientWidth >= 770);
});

test("a lane cut off on the right is scrolled fully into view", () => {
  // "Cancelled" at 1420-1519 while the strip shows 0-974.
  const next = laneStripScrollFor({ ...STRIP, scrollLeft: 0, tabLeft: 1420, tabWidth: 99, isFirst: false });
  assert.equal(next, 545, "clamped to the maximum scroll");
  assert.ok(next + STRIP.clientWidth >= 1420 + 99);
});

test("a lane already fully visible does not move the strip", () => {
  assert.equal(
    laneStripScrollFor({ ...STRIP, scrollLeft: 100, tabLeft: 400, tabWidth: 120, isFirst: false }),
    100,
  );
});

test("a strip that does not overflow never scrolls", () => {
  const wide = { clientWidth: 1870, scrollWidth: 1870 };
  assert.equal(laneStripScrollFor({ ...wide, scrollLeft: 0, tabLeft: 1700, tabWidth: 120, isFirst: false }), 0);
  assert.equal(laneStripScrollFor({ ...wide, scrollLeft: 50, tabLeft: 0, tabWidth: 120, isFirst: true }), 0);
});

/* ------------------------------------------------------------------ *
 * Search and selection
 * ------------------------------------------------------------------ */

test("local search matches request, patient, MRN, exam and doctor", () => {
  const sample = row();
  for (const term of ["radreq0411", "bezabeh", "HMS11832", "brain ct", "ct-brain", "hana"]) {
    assert.equal(matchesSearch(sample, term), true, term);
  }
  assert.equal(matchesSearch(sample, "chest"), false);
  assert.equal(matchesSearch(sample, "   "), true);
});

test("local search never reads anything financial", () => {
  assert.equal(matchesSearch(row({ billing_blocked: true }), "true"), false);
});

test("selection falls to the top visible row when the selected one leaves", () => {
  const rows = [row({ id: 5 }), row({ id: 9 })];
  assert.equal(resolveSelection(rows, 9), 9);
  assert.equal(resolveSelection(rows, 42), 5);
  assert.equal(resolveSelection([], 9), null);
});

test("a detail payload only renders against its own request", () => {
  const loaded = detail({ id: 9 });
  assert.equal(visibleDetail(loaded, 9), loaded);
  assert.equal(visibleDetail(loaded, 5), null);
  assert.equal(visibleDetail(loaded, null), null);
});

test("a request just acted on stays the active request while pinned", () => {
  const rows = [row({ id: 5 }), row({ id: 9 })];
  // Scheduling moved 411 out of the lane on screen: the pin still holds it.
  assert.equal(activeSelection(rows, 411, 411), 411);
  assert.equal(activeSelection(rows, 9, 411), 411);
  assert.equal(activeSelection([], null, 411), 411);
  // Without a pin it is ordinary selection.
  assert.equal(activeSelection(rows, 9, null), 9);
  assert.equal(activeSelection(rows, 411, null), 5);
});

test("the confirmed detail renders for the pinned request, and only for it", () => {
  const confirmed = detail({ id: 411, state: "scheduled", lane: "ready_to_start" });
  assert.equal(visibleDetail(confirmed, 5, 411), confirmed);
  assert.equal(visibleDetail(confirmed, 411, null), confirmed);
  assert.equal(visibleDetail(confirmed, 5, 9), null);
  assert.equal(visibleDetail(confirmed, null, null), null);
  assert.equal(visibleDetail(null, 411, 411), null);
});

test("the panel loading state is derived and cannot hang", () => {
  assert.equal(detailIsLoading(null, true, null), false);
  assert.equal(detailIsLoading(9, true, detail({ id: 9 })), false);
  assert.equal(detailIsLoading(9, true, null), true);
  assert.equal(detailIsLoading(9, false, null), false);
});

/* ------------------------------------------------------------------ *
 * Routes
 * ------------------------------------------------------------------ */

test("every path is a BFF path", () => {
  assert.equal(RAD_SESSION_PATH, "/api/radiology/session");
  assert.equal(requestPath(411), "/api/radiology/requests/411");
  assert.equal(worklistPath({}), "/api/radiology/worklist");
  assert.equal(schedulePath(411), "/api/radiology/requests/411/schedule");
  assert.equal(startPath(411), "/api/radiology/requests/411/start");
});

test("the two transition paths are the only POST targets, by kind", () => {
  assert.equal(transitionPath("schedule", 411), "/api/radiology/requests/411/schedule");
  assert.equal(transitionPath("start", 411), "/api/radiology/requests/411/start");
  for (const kind of ["schedule", "start"] as const) {
    const path = transitionPath(kind, 7);
    assert.ok(path.startsWith("/api/radiology/requests/7/"), path);
    assert.doesNotMatch(path, /odoo|yoya-emr|\?/);
  }
});

/* ------------------------------------------------------------------ *
 * Actions (Slice 2)
 * ------------------------------------------------------------------ */

const SCHEDULABLE = { state: "requested", lane: "to_schedule" } as const;
const STARTABLE = { state: "scheduled", lane: "ready_to_start" } as const;

test("Schedule study is offered for a clear requested study in To schedule", () => {
  assert.equal(canScheduleStudy(detail(SCHEDULABLE)), true);
});

test("Schedule study is hidden for every other combination", () => {
  const hidden: Array<[string, Partial<RadRequestDetail>]> = [
    ["awaiting clearance", { ...SCHEDULABLE, lane: "awaiting_clearance", billing_blocked: true }],
    ["blocked even if the lane says otherwise", { ...SCHEDULABLE, billing_blocked: true }],
    ["draft", { state: "draft", lane: "to_schedule" }],
    ["already scheduled", STARTABLE],
    ["in progress", { state: "in_progress", lane: "awaiting_report" }],
    ["completed", { state: "completed", lane: "completed" }],
    ["cancelled", { state: "cancelled", lane: "cancelled" }],
    ["requested but in another lane", { state: "requested", lane: "ready_to_start" }],
    ["anomaly lane", { ...SCHEDULABLE, lane: "anomaly" }],
    ["anomaly reason", { ...SCHEDULABLE, anomaly_reason: "unknown_state" }],
    ["result conflict", { ...SCHEDULABLE, result_conflict: true }],
    ["no active studies", { ...SCHEDULABLE, exam_count: 0, first_exam: null }],
  ];
  for (const [name, overrides] of hidden) {
    assert.equal(canScheduleStudy(detail(overrides)), false, name);
  }
  assert.equal(canScheduleStudy(null), false);
  assert.equal(canScheduleStudy(undefined), false);
});

test("Start exam is offered for a clear scheduled study in Ready to start", () => {
  assert.equal(canStartExam(detail(STARTABLE)), true);
});

test("Start exam is hidden for every other combination", () => {
  const hidden: Array<[string, Partial<RadRequestDetail>]> = [
    ["still requested", SCHEDULABLE],
    ["blocked", { ...STARTABLE, billing_blocked: true }],
    ["awaiting clearance lane", { ...STARTABLE, lane: "awaiting_clearance", billing_blocked: true }],
    ["in progress", { state: "in_progress", lane: "awaiting_report" }],
    ["completed", { state: "completed", lane: "completed" }],
    ["cancelled", { state: "cancelled", lane: "cancelled" }],
    ["scheduled but in another lane", { state: "scheduled", lane: "to_schedule" }],
    ["anomaly lane", { ...STARTABLE, lane: "anomaly" }],
    ["anomaly reason", { ...STARTABLE, anomaly_reason: "unknown_state" }],
    ["result conflict", { ...STARTABLE, result_conflict: true }],
    ["no active studies", { ...STARTABLE, exam_count: 0, first_exam: null }],
  ];
  for (const [name, overrides] of hidden) {
    assert.equal(canStartExam(detail(overrides)), false, name);
  }
  assert.equal(canStartExam(null), false);
});

test("no request is ever offered both actions", () => {
  const states = ["draft", "requested", "scheduled", "in_progress", "completed", "cancelled"] as const;
  const lanes = RAD_DESK_LANE_ORDER;
  for (const state of states) {
    for (const lane of lanes) {
      const candidate = detail({ state, lane });
      assert.ok(!(canScheduleStudy(candidate) && canStartExam(candidate)), `${state}/${lane}`);
    }
  }
});

test("the schedule confirmation names the request, patient and study exactly", () => {
  assert.equal(
    scheduleConfirmText(detail(SCHEDULABLE)),
    "Schedule RADREQ0411 for Bezabeh Ketema (HMS11832) — Brain CT Scan?",
  );
  assert.equal(
    SCHEDULE_SUPPORT_TEXT,
    "This moves the study to the Radiology ready-to-start queue. No appointment time or imaging slot is created.",
  );
});

test("the start confirmation names the study and patient exactly", () => {
  assert.equal(
    startConfirmText(detail(STARTABLE)),
    "Start Brain CT Scan for Bezabeh Ketema (HMS11832)?",
  );
  assert.equal(
    START_SUPPORT_TEXT,
    "This confirms the study is beginning now and moves it into active imaging work.",
  );
});

test("confirmation copy handles several studies, no study and no MRN", () => {
  assert.equal(studyPhrase(detail({ exam_count: 3 })), "Brain CT Scan +2 more");
  assert.equal(studyPhrase(detail({ exam_count: 0, first_exam: null })), "the ordered study");
  const noMrn = detail({ patient: { id: 7, name: "Bezabeh Ketema", mrn: null, age: null, gender: null } });
  assert.equal(startConfirmText(noMrn), "Start Brain CT Scan for Bezabeh Ketema?");
});

test("no confirmation claims a date, time, slot, room or performer", () => {
  const texts = [
    scheduleConfirmText(detail(SCHEDULABLE)),
    startConfirmText(detail(STARTABLE)),
    START_SUPPORT_TEXT,
    transitionOutcomeText("schedule", detail({ lane_label: "Ready to start" })),
    transitionOutcomeText("start", detail({ lane_label: "Awaiting report" })),
  ];
  for (const text of texts) {
    assert.doesNotMatch(text, /\b(at \d|\d{1,2}:\d{2}|slot|room|appointment|booked|performed by|technologist)\b/i, text);
  }
  // The schedule support text mentions slot/appointment ONLY to deny creating one.
  assert.match(SCHEDULE_SUPPORT_TEXT, /No appointment time or imaging slot is created\.$/);
});

test("the outcome sentence reads the server's lane label", () => {
  assert.equal(
    transitionOutcomeText("schedule", detail({ lane_label: "Ready to start" })),
    "Scheduled. The request is now in Ready to start.",
  );
  assert.equal(
    transitionOutcomeText("start", detail({ lane_label: "Awaiting report" })),
    "Exam started. The request is now in Awaiting report.",
  );
});

test("a refusal prefers the server's sentence and falls back without figures", () => {
  const server = "The radiology study is awaiting financial clearance and cannot be scheduled from the Radiology Desk.";
  assert.equal(transitionErrorMessage(`  ${server} `, "schedule"), server);
  const fallbacks = [
    transitionErrorMessage(null, "schedule"),
    transitionErrorMessage("   ", "start"),
    transitionErrorMessage(undefined, "start"),
  ];
  assert.equal(fallbacks[0], "The study could not be scheduled. Nothing was changed.");
  assert.equal(fallbacks[1], "The exam could not be started. Nothing was changed.");
  for (const text of [...fallbacks, server]) {
    assert.doesNotMatch(text, /\d+\.\d{2}|ETB|Birr|payable|invoice|receipt|amount|price|balance/i, text);
  }
});

test("the desk re-reads after any refusal that means the screen is stale", () => {
  for (const code of [
    "radiology_request_not_found",
    "radiology_request_not_schedulable",
    "radiology_request_not_startable",
    "radiology_request_state_conflict",
    "radiology_request_awaiting_clearance",
    "radiology_request_start_blocked",
    "radiology_request_needs_review",
    "radiology_request_no_active_study",
  ]) {
    assert.equal(shouldReconcileAfterTransition(code), true, code);
  }
  for (const code of [null, undefined, "", "odoo_unreachable", "radiology_request_transition_response_failed"]) {
    assert.equal(shouldReconcileAfterTransition(code), false, String(code));
  }
});

test("the worklist path encodes filters and omits empties", () => {
  const path = worklistPath({
    status: ["to_schedule", "anomaly"],
    date: "2026-08-24",
    q: " Bezabeh Ketema ",
    modality: "ct",
    limit: 50,
  });
  const url = new URL(path, "http://bff.invalid");
  assert.equal(url.pathname, "/api/radiology/worklist");
  assert.equal(url.searchParams.get("status"), "to_schedule,anomaly");
  assert.equal(url.searchParams.get("date"), "2026-08-24");
  assert.equal(url.searchParams.get("q"), "Bezabeh Ketema");
  assert.equal(url.searchParams.get("modality"), "ct");
  assert.equal(url.searchParams.get("limit"), "50");

  const empty = new URL(worklistPath({ q: "  ", date: null, modality: "" }), "http://bff.invalid");
  assert.equal(empty.search, "");
});

/* ------------------------------------------------------------------ *
 * The report (Slice 3)
 * ------------------------------------------------------------------ */

function report(overrides: Partial<RadOperationalResult> = {}): RadOperationalResult {
  return {
    id: 51,
    name: "RADRES0051",
    state: "draft",
    state_label: "Draft",
    result_date: "2026-09-18",
    radiologist: null,
    has_report: false,
    image_count: 0,
    findings: null,
    impression: null,
    recommendations: null,
    lines: [
      {
        id: 81,
        request_line_id: 71,
        exam: { id: 3, name: "Brain CT Scan", code: "CT-BRAIN" },
        modality: "ct",
        modality_label: "CT Scan",
        body_part: "Brain",
        contrast_used: false,
        result_summary: null,
        notes: null,
      },
    ],
    images: [],
    images_mutable: true,
    ...overrides,
  };
}

const AUTHOR: RadDeskCapabilities = {
  radiology_desk: true,
  schedule_study: true,
  start_exam: true,
  open_report: true,
  edit_report: true,
  enter_report: true,
  manage_images: true,
  validate_report: true,
  release_report: true,
};
const TECHNICIAN: RadDeskCapabilities = {
  ...AUTHOR,
  edit_report: false,
  enter_report: false,
  validate_report: false,
  release_report: false,
};

const REPORTABLE = { state: "in_progress", lane: "awaiting_report" } as const;

test("report paths are BFF paths", () => {
  assert.equal(reportPath(411), "/api/radiology/requests/411/report");
  assert.equal(reportSavePath(51), "/api/radiology/results/51/save");
  assert.equal(reportEnterPath(51), "/api/radiology/results/51/enter");
  for (const path of [reportPath(1), reportSavePath(1), reportEnterPath(1)]) {
    assert.doesNotMatch(path, /odoo|yoya-emr|\/validate|\/release/);
  }
});

test("Open report is offered only where a draft may still be created", () => {
  assert.equal(canOpenReport(detail(REPORTABLE)), true);
  // Slice 5: every later lane VIEWS the report it already has instead.
  const hidden: Array<[string, Partial<RadRequestDetail>]> = [
    ["awaiting validation", { state: "in_progress", lane: "awaiting_validation" }],
    ["requested", { state: "requested", lane: "to_schedule" }],
    ["scheduled", { state: "scheduled", lane: "ready_to_start" }],
    ["awaiting release", { state: "in_progress", lane: "awaiting_release" }],
    ["completed", { state: "completed", lane: "completed" }],
    ["cancelled", { state: "cancelled", lane: "cancelled" }],
    ["anomaly lane", { state: "in_progress", lane: "anomaly" }],
    ["anomaly reason", { ...REPORTABLE, anomaly_reason: "unknown_state" }],
    ["report conflict", { ...REPORTABLE, result_conflict: true }],
    ["no active study", { ...REPORTABLE, exam_count: 0, first_exam: null }],
  ];
  for (const [name, overrides] of hidden) {
    assert.equal(canOpenReport(detail(overrides)), false, name);
  }
  assert.equal(canOpenReport(null), false);
});

test("a report is editable only as a draft, and only by a report author", () => {
  assert.equal(reportEditable(report(), AUTHOR), true);
  assert.equal(reportEditable(report(), TECHNICIAN), false);
  assert.equal(reportEditable(report(), null), false);
  for (const state of ["entered", "validated", "released", "cancelled"]) {
    assert.equal(reportEditable(report({ state }), AUTHOR), false, state);
  }
  assert.equal(reportEditable(null, AUTHOR), false);
});

test("the draft is the report's text as strings, and back again as the allow-listed body", () => {
  const draft = reportDraftFromResult(
    report({ findings: "Clear.", lines: [{ ...report().lines[0], notes: "Non-contrast." }] }),
  );
  assert.deepEqual(draft, {
    findings: "Clear.",
    impression: "",
    recommendations: "",
    lines: [{ id: 81, result_summary: "", notes: "Non-contrast." }],
  });
  const body = reportBody(draft);
  assert.deepEqual(body, {
    findings: "Clear.",
    impression: null,
    recommendations: null,
    lines: [{ id: 81, result_summary: null, notes: "Non-contrast." }],
  });
  assert.deepEqual(Object.keys(body).sort(), ["findings", "impression", "lines", "recommendations"]);
  assert.deepEqual(Object.keys(body.lines[0]).sort(), ["id", "notes", "result_summary"]);
  assert.deepEqual(JSON.parse(serializeReportBody(draft)), body);
});

test("the body carries text exactly as typed: whitespace is the server's to judge", () => {
  const body = reportBody({ findings: "  ", impression: "Normal.\n", recommendations: "", lines: [] });
  assert.equal(body.findings, "  ");
  assert.equal(body.impression, "Normal.\n");
  assert.equal(body.recommendations, null);
});

test("the body never carries a protected field", () => {
  const text = serializeReportBody(reportDraftFromResult(report()));
  for (const key of [
    "state", "request_id", "patient_id", "physician_id", "radiologist", "active",
    "result_date", "image", "exam", "request_line_id", "contrast", "body_part",
  ]) {
    assert.ok(!text.includes(`"${key}`), key);
  }
});

test("a change is detected field by field, and a line patch touches one line", () => {
  const base = reportDraftFromResult(report());
  assert.equal(reportDraftChanged(base, reportDraftFromResult(report())), false);
  assert.equal(reportDraftChanged(base, { ...base, impression: "Normal." }), true);
  const patched = patchReportLine(base, 81, { result_summary: "Normal." });
  assert.equal(patched.lines[0].result_summary, "Normal.");
  assert.equal(base.lines[0].result_summary, "", "the original draft is not mutated");
  assert.equal(reportDraftChanged(base, patched), true);
  assert.deepEqual(patchReportLine(base, 999, { notes: "X" }), base);
});

test("the entry confirmation names the report, in the tested wording", () => {
  assert.equal(reportEnterConfirmText(report()), "Mark RADRES0051 as entered?");
  assert.equal(
    REPORT_ENTER_SUPPORT_TEXT,
    "This locks the current report text for review before validation. The ordering clinician will still not see the report until it is released.",
  );
});

test("the sheet's header context comes from the server's payloads", () => {
  const context = reportContext(
    detail({
      exams_summary: "Brain CT Scan",
      modality_label: "CT Scan",
      body_part: "Brain",
    }),
    report(),
  );
  assert.deepEqual(context, [
    { label: "Patient", value: "Bezabeh Ketema" },
    { label: "MRN", value: "HMS11832" },
    { label: "Request", value: "RADREQ0411" },
    { label: "Report", value: "RADRES0051" },
    { label: "Exams", value: "Brain CT Scan" },
    { label: "Modality", value: "CT Scan" },
    { label: "Body part", value: "Brain" },
    { label: "Ordering doctor", value: "Dr. Hana Bekele" },
    { label: "Radiologist", value: "—" },
  ]);
  assert.equal(
    reportContext(detail({}), report({ radiologist: "Dr. Yonas Radiologist" }))[8].value,
    "Dr. Yonas Radiologist",
  );
  const bare = reportContext(detail({ patient: null, ordering_physician: null }), report());
  assert.equal(bare[0].value, "—");
  assert.equal(bare[7].value, "—");
});

test("the entered outcome reads the server's new lane", () => {
  assert.equal(
    reportEnteredOutcomeText(report(), detail({ lane_label: "Awaiting validation" })),
    "Report RADRES0051 entered. The request is now in Awaiting validation.",
  );
});

test("the status line says what the sheet is doing", () => {
  const base = { readOnly: false, busy: null, changed: false, savedSinceOpen: false } as const;
  assert.equal(reportStatusText({ ...base, busy: "save" }), "Saving draft…");
  assert.equal(reportStatusText({ ...base, busy: "enter" }), "Marking entered…");
  assert.equal(reportStatusText({ ...base, readOnly: true }), "Read only.");
  assert.equal(reportStatusText({ ...base, changed: true }), "Unsaved changes.");
  assert.equal(reportStatusText({ ...base, savedSinceOpen: true }), "Draft saved.");
  assert.equal(reportStatusText(base), "No changes yet.");
});

test("a report refusal prefers the server's sentence and falls back without figures", () => {
  const server = "Radiology report RADRES0051 is not complete: enter the findings or the impression.";
  assert.equal(reportErrorMessage(` ${server} `, "enter"), server);
  const fallbacks = [
    reportErrorMessage(null, "open"),
    reportErrorMessage("", "save"),
    reportErrorMessage(undefined, "enter"),
  ];
  assert.deepEqual(fallbacks, [
    "The report could not be opened. Nothing was changed.",
    "The draft could not be saved. Nothing was changed.",
    "The report could not be marked entered. Nothing was changed.",
  ]);
  for (const text of [...fallbacks, server]) {
    assert.doesNotMatch(text, /\d+\.\d{2}|ETB|Birr|payable|invoice|receipt|amount|price|balance|charge/i, text);
  }
});

test("the desk re-reads after a report refusal that means the screen is stale", () => {
  for (const code of [
    "radiology_request_not_found",
    "radiology_result_not_found",
    "radiology_report_not_available",
    "radiology_request_no_active_study",
    "radiology_result_ambiguous",
    "radiology_result_not_editable",
    "radiology_result_state_conflict",
  ]) {
    assert.equal(shouldReconcileAfterReport(code), true, code);
  }
  for (const code of [
    null, undefined, "", "radiology_report_incomplete", "radiology_report_author_required",
    "radiology_report_field_not_allowed", "radiology_report_response_failed",
  ]) {
    assert.equal(shouldReconcileAfterReport(code), false, String(code));
  }
});

/* ------------------------------------------------------------------ *
 * Images (Slice 4)
 * ------------------------------------------------------------------ */

test("image paths are the desk's own BFF paths", () => {
  assert.equal(imagesPath(51), "/api/radiology/results/51/images");
  assert.equal(imagePath(51, 7), "/api/radiology/results/51/images/7");
  assert.equal(imagePath(51, 7, true), "/api/radiology/results/51/images/7?disposition=attachment");
  assert.equal(imageRemovePath(51, 7), "/api/radiology/results/51/images/7/remove");
  for (const path of [imagesPath(1), imagePath(1, 2), imageRemovePath(1, 2)]) {
    assert.doesNotMatch(path, /odoo|yoya-emr|web\/content|doctor|token/);
  }
});

test("image mutability is the server's flag and the role's capability, not the text's", () => {
  assert.equal(imagesEditable(report({ state: "entered", images_mutable: true }), AUTHOR), true);
  assert.equal(imagesEditable(report({ state: "entered", images_mutable: true }), TECHNICIAN), true);
  assert.equal(reportEditable(report({ state: "entered" }), AUTHOR), false, "the text stays frozen");
  assert.equal(imagesEditable(report({ images_mutable: false }), AUTHOR), false);
  assert.equal(imagesEditable(report(), { ...AUTHOR, manage_images: false }), false);
  assert.equal(imagesEditable(null, AUTHOR), false);
  assert.equal(imagesEditable(report(), null), false);
});

test("the picker offers JPG, PNG and PDF up to 25 MB", () => {
  assert.equal(ACCEPTED_IMAGE_TYPES, "image/jpeg,image/png,application/pdf");
  assert.equal(ACCEPTED_IMAGE_LABEL, "JPG, PNG or PDF");
  assert.equal(MAX_IMAGE_LABEL, "Max file size: 25 MB");
  assert.equal(MAX_IMAGE_BYTES, 25 * 1024 * 1024);
  assert.equal(imageTooLarge({ size: MAX_IMAGE_BYTES }), false);
  assert.equal(imageTooLarge({ size: MAX_IMAGE_BYTES + 1 }), true);
});

test("the upload form carries the file and a trimmed caption, nothing else", () => {
  const file = new File([new Uint8Array([0x89, 0x50])], "axial.png", { type: "image/png" });
  const form = imageUploadForm(file, "  Axial 12  ");
  assert.deepEqual([...form.keys()].sort(), ["caption", "file"]);
  assert.equal(form.get("caption"), "Axial 12");
  assert.equal((form.get("file") as File).name, "axial.png");
  assert.deepEqual([...imageUploadForm(file, "   ").keys()], ["file"]);
  assert.deepEqual([...imageUploadForm(file).keys()], ["file"]);
});

test("a JPEG or PNG previews, a PDF opens, anything else is a plain file", () => {
  assert.equal(imageKind({ mimetype: "image/jpeg" }), "image");
  assert.equal(imageKind({ mimetype: "image/png" }), "image");
  assert.equal(imageKind({ mimetype: "application/pdf" }), "pdf");
  assert.equal(imageKind({ mimetype: "image/svg+xml" }), "other");
  assert.equal(imageKind({ mimetype: null }), "other");
  assert.deepEqual(
    ["image/jpeg", "image/png", "application/pdf", null].map((mimetype) => imageTypeLabel({ mimetype })),
    ["JPEG", "PNG", "PDF", "File"],
  );
});

test("the remove confirmation names the file, in the tested wording", () => {
  assert.equal(
    imageRemoveConfirmText({ filename: "axial.png" }),
    "Remove axial.png from this radiology report?",
  );
  assert.equal(
    IMAGE_REMOVE_SUPPORT_TEXT,
    "This removes the uploaded file from the report. This action is unavailable after validation.",
  );
});

test("an image refusal prefers the server's sentence and falls back without figures", () => {
  const server = "This file is not a JPEG, PNG or PDF. The file's content decides this, not its name. Nothing has been uploaded.";
  assert.equal(imageErrorMessage(server, "upload"), server);
  assert.equal(imageErrorMessage(null, "upload"), "The file could not be uploaded. Nothing was changed.");
  assert.equal(imageErrorMessage("  ", "remove"), "The file could not be removed. Nothing was changed.");
  for (const text of [server, imageErrorMessage(null, "upload"), imageErrorMessage(null, "remove")]) {
    assert.doesNotMatch(text, /\d+\.\d{2}|ETB|Birr|payable|invoice|receipt|amount|price|balance|charge/i);
  }
});

test("the desk re-reads after an image refusal that means the screen is stale", () => {
  for (const code of ["radiology_result_not_found", "radiology_image_not_found", "radiology_image_not_editable"]) {
    assert.equal(shouldReconcileAfterImage(code), true, code);
  }
  for (const code of [null, "", "radiology_image_too_large", "radiology_image_unsupported_type",
                      "radiology_image_invalid_file", "radiology_image_response_failed"]) {
    assert.equal(shouldReconcileAfterImage(code), false, String(code));
  }
});

/* ------------------------------------------------------------------ *
 * Validation and release (Slice 5)
 * ------------------------------------------------------------------ */

const AWAITING_VALIDATION = {
  state: "in_progress", lane: "awaiting_validation", result: report({ state: "entered" }),
} as const;
const AWAITING_RELEASE = {
  state: "in_progress", lane: "awaiting_release", result: report({ state: "validated" }),
} as const;

test("sign-off paths are the desk's own BFF paths", () => {
  assert.equal(reportValidatePath(75), "/api/radiology/results/75/validate");
  assert.equal(reportReleasePath(75), "/api/radiology/results/75/release");
  assert.equal(signoffPath("validate", 75), "/api/radiology/results/75/validate");
  assert.equal(signoffPath("release", 75), "/api/radiology/results/75/release");
});

test("View report is offered for every lane past Awaiting report, never on a conflict", () => {
  for (const lane of ["awaiting_validation", "awaiting_release", "completed", "anomaly"]) {
    assert.equal(canViewReport(detail({ lane, result: report() })), true, lane);
  }
  assert.equal(canViewReport(detail({ lane: "awaiting_report", result: report() })), false);
  assert.equal(canViewReport(detail({ lane: "completed", result: null })), false);
  assert.equal(canViewReport(detail({ lane: "anomaly", result: null, result_conflict: true })), false);
});

test("Validate is offered only in Awaiting validation, and only to a report author", () => {
  assert.equal(canValidateReport(detail(AWAITING_VALIDATION), AUTHOR), true);
  assert.equal(canValidateReport(detail(AWAITING_VALIDATION), TECHNICIAN), false);
  assert.equal(canValidateReport(detail(AWAITING_VALIDATION), null), false);
  for (const [name, overrides] of [
    ["awaiting release", AWAITING_RELEASE],
    ["awaiting report", { ...AWAITING_VALIDATION, lane: "awaiting_report" }],
    ["completed", { ...AWAITING_VALIDATION, state: "completed", lane: "completed" }],
    ["conflict", { ...AWAITING_VALIDATION, result_conflict: true }],
    ["anomaly", { ...AWAITING_VALIDATION, anomaly_reason: "unknown_state" }],
    ["no report", { ...AWAITING_VALIDATION, result: null }],
  ] as Array<[string, Partial<RadRequestDetail>]>) {
    assert.equal(canValidateReport(detail(overrides), AUTHOR), false, name);
  }
});

test("Release is offered only in Awaiting release, and only to a report author", () => {
  assert.equal(canReleaseReport(detail(AWAITING_RELEASE), AUTHOR), true);
  assert.equal(canReleaseReport(detail(AWAITING_RELEASE), TECHNICIAN), false);
  for (const [name, overrides] of [
    ["awaiting validation", AWAITING_VALIDATION],
    ["completed", { ...AWAITING_RELEASE, state: "completed", lane: "completed" }],
    ["conflict", { ...AWAITING_RELEASE, result_conflict: true }],
    ["released but not completed", { ...AWAITING_RELEASE, lane: "anomaly", anomaly_reason: "released_not_completed" }],
  ] as Array<[string, Partial<RadRequestDetail>]>) {
    assert.equal(canReleaseReport(detail(overrides), AUTHOR), false, name);
  }
  // No request is ever offered both.
  for (const lane of RAD_DESK_LANE_ORDER) {
    const candidate = detail({ ...AWAITING_RELEASE, lane });
    assert.ok(!(canValidateReport(candidate, AUTHOR) && canReleaseReport(candidate, AUTHOR)), lane);
  }
});

test("the validation copy is the tested wording", () => {
  assert.equal(
    VALIDATE_REVIEW_TEXT,
    "Review the report and all attached imaging before validation. Validation freezes the report and uploaded files for final release.",
  );
  assert.equal(
    validateConfirmText(detail({}), report({ name: "RADRES0075" })),
    "Validate RADRES0075 for Bezabeh Ketema (HMS11832) — Brain CT Scan?",
  );
  assert.equal(
    VALIDATE_SUPPORT_TEXT,
    "This cannot be undone from the Radiology Desk. The ordering clinician will still not see the report until it is released.",
  );
});

test("the release copy is the tested wording", () => {
  assert.equal(
    RELEASE_REVIEW_TEXT,
    "Review the validated report before release. Releasing publishes the report and imaging to the ordering clinician immediately.",
  );
  assert.equal(
    releaseConfirmText(detail({}), report({ name: "RADRES0075" })),
    "Release RADRES0075 to Dr. Hana Bekele?",
  );
  assert.equal(releaseIdentityText(detail({})), "Bezabeh Ketema (HMS11832) · Brain CT Scan");
  assert.equal(
    releaseSupportText(detail({})),
    "The report and attached images become visible on the Doctor Desk immediately. RADREQ0411 will complete when all ordered studies are released.",
  );
});

test("the outcome reads the server's completion answer and lane", () => {
  const request = (overrides: Partial<RadRequestDetail>) => detail(overrides);
  assert.equal(
    signoffOutcomeText("validate", { request_completed: false, request: request({ lane_label: "Awaiting release" }) }),
    "Validated · The request is now in Awaiting release.",
  );
  assert.equal(
    signoffOutcomeText("release", { request_completed: true, request: request({ lane_label: "Completed" }) }),
    "Released · Request completed",
  );
  assert.equal(
    signoffOutcomeText("release", { request_completed: false, request: request({ review_message: null }) }),
    "Released · Request still in progress",
  );
  assert.equal(
    signoffOutcomeText("release", {
      request_completed: false,
      request: request({ review_message: "A released report did not complete this request." }),
    }),
    "Released · Request still in progress. A released report did not complete this request.",
  );
});

test("a sign-off refusal prefers the server's fixed sentence and never carries a figure", () => {
  const server = "The radiology report could not be released. Nothing was changed. Ask a hospital manager to review this request.";
  assert.equal(signoffErrorMessage(server, "release"), server);
  assert.equal(signoffErrorMessage(null, "validate"), "The radiology report could not be validated. Nothing was changed.");
  assert.equal(signoffErrorMessage("", "release"), "The radiology report could not be released. Nothing was changed.");
  for (const text of [server, signoffErrorMessage(null, "validate"), signoffErrorMessage(null, "release")]) {
    assert.doesNotMatch(text, /\d+\.\d{2}|ETB|Birr|payable|invoice|receipt|amount|balance|charge/i);
  }
  for (const code of ["radiology_result_not_validatable", "radiology_result_not_releasable",
                      "radiology_result_ambiguous", "radiology_result_state_conflict",
                      "radiology_result_not_found"]) {
    assert.equal(shouldReconcileAfterSignoff(code), true, code);
  }
  for (const code of [null, "radiology_report_validation_blocked", "radiology_report_release_blocked",
                      "radiology_report_incomplete", "radiology_report_author_required"]) {
    assert.equal(shouldReconcileAfterSignoff(code), false, String(code));
  }
});

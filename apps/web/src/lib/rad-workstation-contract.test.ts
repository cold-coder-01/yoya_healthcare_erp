/**
 * THE RADIOLOGY DESK'S READ-ONLY AND CONFIDENTIALITY GUARANTEES, HELD AT THE
 * SOURCE.
 *
 * This project ships no DOM test stack, so component wiring is pinned by
 * reading source -- the technique lab-workstation-contract.test.ts and
 * order-wizard-contract.test.ts already use. Comments are stripped before any
 * "must not contain" check, so a docstring that explains a rule cannot trip it.
 *
 * WHAT IS HELD:
 *   1. EXACTLY TWO WRITES (Slice 2). One bodiless POST site, to the schedule and
 *      start paths only; each offered for exactly one lane, behind an explicit
 *      confirmation. No other workflow action exists, not even disabled.
 *   2. NO ODOO. Every URL is a /api/radiology/* BFF path.
 *   3. NO MONEY. No financial vocabulary is rendered or read.
 *   4. SERVER-DERIVED STATE. Lane badges read the server summary, the lane is
 *      never derived in the browser, and the panel's loading state is derived.
 *   5. SAFE STATES. Loading, empty, error, not-your-workstation, anomaly and
 *      report-conflict states all render.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

/** Source with comments removed: the property is what a file EMITS. */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const WORKSTATION = read("components/radiology/rad-workstation.tsx");
const PANEL = read("components/radiology/rad-request-panel.tsx");
const QUEUE = read("components/radiology/rad-queue.tsx");
const FILTERS = read("components/radiology/rad-filters.tsx");
const PILLS = read("components/radiology/rad-status-pill.tsx");
const FORMAT = read("lib/rad-desk-format.ts");
const LAYOUT = read("app/radiology/layout.tsx");
const PAGE = read("app/radiology/page.tsx");

const COMPONENTS: ReadonlyArray<readonly [string, string]> = [
  ["rad-workstation.tsx", WORKSTATION],
  ["rad-request-panel.tsx", PANEL],
  ["rad-queue.tsx", QUEUE],
  ["rad-filters.tsx", FILTERS],
  ["rad-status-pill.tsx", PILLS],
  ["rad-desk-format.ts", FORMAT],
  ["layout.tsx", LAYOUT],
  ["page.tsx", PAGE],
];

/* ------------------------------------------------------------------ *
 * 1. Exactly two writes
 * ------------------------------------------------------------------ */

test("there is exactly one POST site, and it sends no body worth reading", () => {
  const posts = COMPONENTS.flatMap(([name, source]) =>
    [...code(source).matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/g)].map(
      (match) => [name, match[1]] as const,
    ),
  );
  assert.deepEqual(posts, [["rad-workstation.tsx", "POST"]]);
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("async function postRad<T>(path: string)"));
  assert.ok(emitted.includes('body: "{}",'), "bodiless: the request is identified by the URL");
  for (const [name, source] of COMPONENTS) {
    assert.doesNotMatch(code(source), /JSON\.stringify\(/, `${name} must build no request body`);
  }
});

test("the POST site is only ever pointed at the two transition paths", () => {
  const emitted = code(WORKSTATION);
  // Call sites only (`await postRad<...>(`), captured up to the trailing `,)`.
  const calls = [...emitted.matchAll(/await postRad<[^>]*>\(\s*([\s\S]*?),\s*\)/g)].map((m) => m[1].trim());
  assert.deepEqual(calls, ["transitionPath(kind, requestId)"]);
  const format = code(FORMAT);
  assert.ok(format.includes('return kind === "schedule" ? schedulePath(requestId) : startPath(requestId);'));
  assert.ok(format.includes("/api/radiology/requests/${requestId}/schedule"));
  assert.ok(format.includes("/api/radiology/requests/${requestId}/start"));
});

test("the panel's only controls are the two actions and their confirmation", () => {
  const emitted = code(PANEL);
  const labels = [...emitted.matchAll(/"(Schedule study|Start exam|Confirm schedule|Confirm start|Cancel)"/g)].map((m) => m[1]);
  for (const label of ["Schedule study", "Start exam", "Confirm schedule", "Confirm start"]) {
    assert.ok(labels.includes(label), label);
  }
  assert.ok(emitted.includes(">\n          Cancel\n        </button>") || emitted.includes("Cancel"));
  // Every <button in the panel lives inside TransitionAction.
  const actionStart = emitted.indexOf("function TransitionAction(");
  const actionEnd = emitted.indexOf("function ReportSection(");
  const outside = emitted.slice(0, actionStart) + emitted.slice(actionEnd);
  assert.doesNotMatch(outside, /<button/, "no control outside the two transitions");
});

test("each action is offered only through the tested visibility helpers and capability", () => {
  const emitted = code(PANEL);
  assert.ok(emitted.includes("capabilities?.schedule_study === true && canScheduleStudy(detail)"));
  assert.ok(emitted.includes("capabilities?.start_exam === true && canStartExam(detail)"));
  assert.ok(emitted.includes("{offerSchedule ? ("));
  assert.ok(emitted.includes("{offerStart ? ("));
});

test("no other workflow action is offered anywhere on the desk", () => {
  const forbidden = [
    /Mark in progress/i, /Enter report/i, /Validate report/i, /Release report/i,
    /Upload image/i, /Delete image/i, /Cancel request/i, /Reset to draft/i,
    /onValidate|onRelease|onUpload|onCancelRequest|onEnter/,
  ];
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    for (const pattern of forbidden) {
      assert.doesNotMatch(emitted, pattern, `${name} offers ${pattern}`);
    }
  }
});

test("the panel says in words which actions this desk has", () => {
  assert.ok(PANEL.includes("Schedule and start only. Reporting, images, validation and release are not available on this desk yet."));
});

/* ------------------------------------------------------------------ *
 * 1b. Confirmation and keyboard safety
 * ------------------------------------------------------------------ */

test("the confirmation copy is the tested wording and claims no date, slot or performer", () => {
  const emitted = code(PANEL);
  assert.ok(emitted.includes('kind === "schedule" ? scheduleConfirmText(detail) : startConfirmText(detail)'));
  assert.ok(emitted.includes('kind === "schedule" ? SCHEDULE_SUPPORT_TEXT : START_SUPPORT_TEXT'));
  assert.doesNotMatch(emitted, /type="(date|time|datetime-local)"/, "no scheduling date or time is collected");
  for (const [name, source] of [["panel", PANEL], ["format", FORMAT]] as const) {
    assert.doesNotMatch(code(source), /scheduled for|scheduled at|performed by|performed at|slot booked|appointment booked/i, name);
  }
});

test("only an explicit final click sends; Escape cancels; no shortcut submits", () => {
  const emitted = code(PANEL);
  const action = emitted.slice(emitted.indexOf("function TransitionAction("), emitted.indexOf("function ReportSection("));
  // Step one only opens the confirmation.
  assert.ok(action.includes("onClick={() => setConfirming(true)}"));
  // Only the Confirm button calls onConfirm.
  assert.equal([...action.matchAll(/onConfirm\(/g)].length, 1);
  assert.ok(action.includes('if (event.key === "Escape" && !pending)'));
  assert.ok(action.includes('if (event.key === "Enter" && (event.ctrlKey || event.metaKey))'));
  assert.doesNotMatch(action, /onKeyDown=\{[^}]*onConfirm/);
});

test("focus lands on Cancel, never on the step that changes the record", () => {
  const action = code(PANEL);
  assert.ok(action.includes("ref={cancelRef}"));
  assert.ok(action.includes("if (confirming) cancelRef.current?.focus();"));
  assert.doesNotMatch(action, /autoFocus/);
});

test("busy disables every action control and a second submit is refused", () => {
  const panel = code(PANEL);
  const action = panel.slice(panel.indexOf("function TransitionAction("), panel.indexOf("function ReportSection("));
  assert.equal([...action.matchAll(/disabled=\{pending\}/g)].length, 3, "open, cancel and confirm");
  assert.ok(action.includes("if (pending) return;"));
  const workstation = code(WORKSTATION);
  assert.ok(workstation.includes("if (pendingId !== null) return false;"));
  assert.ok(workstation.includes("pending={pendingId !== null && detailForSelection !== null && pendingId === detailForSelection.id}"));
});

/* ------------------------------------------------------------------ *
 * 1c. Reconciliation after an action
 * ------------------------------------------------------------------ */

test("success shows the server's request, pins it, and refetches queue and counts", () => {
  const emitted = code(WORKSTATION);
  const run = emitted.slice(emitted.indexOf("const runTransition = useCallback("), emitted.indexOf("const detailForSelection ="));
  const success = run.slice(run.indexOf("const confirmed = payload.data.request;"), run.indexOf("return true;"));
  assert.ok(success.includes("setDetail(confirmed);"));
  assert.ok(success.includes("setJustActedId(confirmed.id);"));
  assert.ok(success.includes("transitionOutcomeText(kind, confirmed)"));
  assert.ok(success.includes("refresh();"), "counts come back from the server summary");
});

test("a refusal never replaces the request on screen, and reconciles when stale", () => {
  const emitted = code(WORKSTATION);
  const run = emitted.slice(emitted.indexOf("const runTransition = useCallback("), emitted.indexOf("const detailForSelection ="));
  const refusal = run.slice(run.indexOf("if (!response.ok || !payload.success) {"), run.indexOf("const confirmed = payload.data.request;"));
  assert.doesNotMatch(refusal, /setDetail\(/);
  assert.ok(refusal.includes("shouldReconcileAfterTransition(codeFromPayload(payload))"));
  const lost = run.slice(run.indexOf("} catch {"), run.indexOf("} finally {"));
  assert.doesNotMatch(lost, /setDetail\(/);
  assert.ok(lost.includes("refresh();"), "a lost response is re-read, not reported as not done");
  assert.ok(run.includes("setPendingId(null);"));
});

test("the pin holds the request on screen and a real selection or lane change releases it", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("activeSelection(visibleRows, selectedId, justActedId)"));
  assert.ok(emitted.includes("visibleDetail(detail, activeId, justActedId)"));
  const lane = emitted.slice(emitted.indexOf("onLaneChange={(nextLane) => {"), emitted.indexOf("onDateChange={setDate}"));
  assert.ok(lane.includes("setJustActedId(null);"));
  const select = emitted.slice(emitted.indexOf("onSelect={(requestId) => {"), emitted.indexOf("<RadRequestPanel"));
  assert.ok(select.includes("setJustActedId(null);"));
});

test("a failed refresh after a confirmed action keeps the confirmed state and warns", () => {
  const workstation = code(WORKSTATION);
  assert.equal([...workstation.matchAll(/setRefreshWarningFor\(justActedRef\.current\)/g)].length, 2);
  assert.ok(workstation.includes("setDetailStaleFor(requestId);"));
  const panel = code(PANEL);
  assert.ok(panel.includes("The action was confirmed, but the queue could not be refreshed."));
  assert.ok(panel.includes("{outcome ? ("));
});

/* ------------------------------------------------------------------ *
 * 2. No Odoo, no bytes
 * ------------------------------------------------------------------ */

test("the browser never addresses Odoo", () => {
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    for (const marker of ["yoya-emr", "http://", "https://", "8069", "8171", "/web/content", "/web/image", "session_id"]) {
      assert.ok(!emitted.includes(marker), `${name} must not contain ${marker}`);
    }
  }
});

test("every fetch goes through a BFF path helper", () => {
  const emitted = code(WORKSTATION);
  const fetches = [...emitted.matchAll(/fetch\(\s*([^,\n]+)/g)].map((match) => match[1].trim());
  assert.ok(fetches.length >= 4);
  for (const target of fetches) {
    // `path` is postRad's own parameter, which only transitionPath() supplies.
    assert.match(target, /^(RAD_SESSION_PATH|worklistPath\(|requestPath\(|path$)/, target);
  }
});

test("images are metadata only: no image element, link or source", () => {
  const emitted = code(PANEL);
  assert.doesNotMatch(emitted, /<img|<Image|<a\s|href=|src=/);
  assert.ok(emitted.includes("image.mimetype"));
  assert.ok(emitted.includes("fileSizeLabel(image.file_size)"));
  assert.ok(emitted.includes("image.uploaded_by"));
});

/* ------------------------------------------------------------------ *
 * 3. No money
 * ------------------------------------------------------------------ */

test("no financial vocabulary is rendered or read", () => {
  const forbidden = /amount|invoice|receipt|price|balance|payer|charge_line|clearance_message|currency|\bbirr\b|\betb\b/i;
  for (const [name, source] of COMPONENTS) {
    assert.doesNotMatch(code(source), forbidden, name);
  }
});

test("the wire contract carries billing only as a boolean", () => {
  const types = code(read("types/rad-desk.ts"));
  const billing = [...types.matchAll(/(\w*billing\w*)\s*:/g)].map((match) => match[1]);
  assert.deepEqual([...new Set(billing)], ["billing_blocked"]);
  assert.match(types, /billing_blocked:\s*boolean/);
  assert.doesNotMatch(types, /amount|invoice|receipt|price|payer|charge/i);
});

/* ------------------------------------------------------------------ *
 * 4. Server-derived state
 * ------------------------------------------------------------------ */

test("lane badges read the server summary, never a local recount", () => {
  const emitted = code(FILTERS);
  assert.ok(emitted.includes("laneCountLabel(summary, key)"));
  assert.doesNotMatch(emitted, /rows\.(length|filter|reduce)/);
});

test("the browser never derives a lane", () => {
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    assert.doesNotMatch(emitted, /\.lane\s*=[^=]/, `${name} assigns a lane`);
    assert.doesNotMatch(emitted, /billing_blocked\s*\?/, `${name} branches on billing_blocked`);
    assert.doesNotMatch(emitted, /result\??\.state\s*===/, `${name} derives from report state`);
  }
});

test("the queue renders the server's lane and priority", () => {
  const emitted = code(QUEUE);
  assert.ok(emitted.includes("lane={row.lane} label={row.lane_label}"));
  assert.ok(emitted.includes("priority={row.priority} label={row.priority_label}"));
  assert.ok(emitted.includes("row.patient?.mrn"));
  assert.ok(emitted.includes("row.modality_label"));
  assert.ok(emitted.includes("row.body_part"));
  assert.ok(emitted.includes("row.ordering_physician?.name"));
  assert.ok(emitted.includes("row.request_date"));
});

test("the panel receives the DERIVED loading value", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("const panelIsLoading = detailIsLoading("));
  assert.ok(emitted.includes("loading={panelIsLoading}"));
  assert.ok(!emitted.includes("loading={detailLoading}"));
  assert.ok(emitted.includes("visibleDetail(detail, activeId, justActedId)"));
});

test("selection is derived, and lane, date, modality and search all refetch", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("activeSelection(visibleRows, selectedId, justActedId)"));
  assert.ok(emitted.includes("[statuses, date, modality, debouncedSearch, refreshToken]"));
  assert.ok(emitted.includes("setLane(nextLane);"));
  assert.ok(emitted.includes("onModalityChange={setModality}"));
  assert.ok(emitted.includes("onDateChange={setDate}"));
  assert.ok(emitted.includes("onSearchChange={setSearch}"));
});

test("the modality options come from the server", () => {
  assert.ok(code(WORKSTATION).includes("setModalities(payload.data.meta.modalities"));
  const emitted = code(FILTERS);
  assert.ok(emitted.includes("modalities.map("));
  assert.doesNotMatch(emitted, /"xray"|"ultrasound"|"mri"/);
});

test("the lane strip keeps the selected lane, and so the first lane, in view", () => {
  const emitted = code(FILTERS);
  // The strip is still a horizontally scrolling tablist: nothing was redesigned.
  assert.ok(emitted.includes('className="flex min-w-0 items-center gap-1 overflow-x-auto"'));
  assert.ok(emitted.includes("ref={stripRef}"));
  assert.ok(emitted.includes("data-lane={key}"));
  // The offset is computed by the tested helper and written to the strip only.
  assert.ok(emitted.includes("strip.scrollLeft = laneStripScrollFor("));
  assert.ok(emitted.includes("isFirst: lane === LANES[0]"));
  assert.doesNotMatch(emitted, /scrollIntoView/, "must not scroll the page or panels");
  // On mount, on every lane change, and on a back/forward-cache restore.
  assert.ok(emitted.includes("}, [lane]);"));
  assert.ok(emitted.includes('window.addEventListener("pageshow", revealSelectedLane)'));
  assert.ok(emitted.includes('window.removeEventListener("pageshow", revealSelectedLane)'));
  // The first strip entry is "All active", so isFirst resets the strip to 0.
  assert.ok(emitted.includes("const LANES: readonly string[] = [ACTIVE_LANE_KEY, ...RAD_DESK_LANE_ORDER];"));
});

/* ------------------------------------------------------------------ *
 * 5. Safe states
 * ------------------------------------------------------------------ */

test("loading, empty and error states render in the queue and the panel", () => {
  const queue = code(QUEUE);
  assert.ok(queue.includes("Loading imaging queue"));
  assert.ok(queue.includes("No requests here"));
  assert.ok(queue.includes("{error}"));
  const panel = code(PANEL);
  assert.ok(panel.includes("Loading request"));
  assert.ok(panel.includes("No request selected"));
  assert.ok(panel.includes('role="alert"'));
});

test("a refused role is told this is not their workstation", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("This is not your workstation."));
  assert.ok(emitted.includes("response.status === 403 ? false : null"));
});

test("anomaly and report conflict are stated, not resolved", () => {
  const emitted = code(PANEL);
  assert.ok(emitted.includes("reviewMessage(detail)"));
  // With a conflict the panel shows no report and no "not started" claim.
  assert.ok(emitted.includes("detail.result_conflict ? null"));
  assert.ok(code(QUEUE).includes("row.result_conflict"));
});

test("report text renders when the server sends it", () => {
  const emitted = code(PANEL);
  for (const field of ["result.findings", "result.impression", "result.recommendations"]) {
    assert.ok(emitted.includes(field), field);
  }
  assert.ok(emitted.includes("No report text has been recorded"));
});

test("the shell is visually distinct and guards nothing itself", () => {
  const emitted = code(LAYOUT);
  assert.ok(emitted.includes("Radiology Desk"));
  assert.ok(emitted.includes("#0f766e"));
  assert.ok(!emitted.includes("#4338ca"), "must not reuse the Laboratory keyline");
  assert.doesNotMatch(emitted, /redirect\(|notFound\(/);
});

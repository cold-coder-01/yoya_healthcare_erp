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
 *   1. EXACTLY NINE WRITES, ONE POST SITE. Schedule and Start (Slice 2), Open
 *      report (Slice 3), Remove file (Slice 4), Validate and Release (Slice 5)
 *      are bodiless; Save draft and Mark entered send the one body
 *      serializeReportBody() builds; Upload sends the one form
 *      imageUploadForm() builds. The record-changing acts need an explicit
 *      confirmation. No other workflow action exists.
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
  // Line endings normalized: the working tree may be CRLF.
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8").replace(/\r\n/g, "\n");
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
const MODAL = read("components/radiology/rad-report-modal.tsx");

const COMPONENTS: ReadonlyArray<readonly [string, string]> = [
  ["rad-workstation.tsx", WORKSTATION],
  ["rad-request-panel.tsx", PANEL],
  ["rad-queue.tsx", QUEUE],
  ["rad-filters.tsx", FILTERS],
  ["rad-status-pill.tsx", PILLS],
  ["rad-report-modal.tsx", MODAL],
  ["rad-desk-format.ts", FORMAT],
  ["layout.tsx", LAYOUT],
  ["page.tsx", PAGE],
];

/* ------------------------------------------------------------------ *
 * 1. Exactly two writes
 * ------------------------------------------------------------------ */

/** The one function allowed to build a request body. */
function serializeReportBodySource(): string {
  const emitted = code(FORMAT);
  const start = emitted.indexOf("export function serializeReportBody(");
  assert.ok(start >= 0, "serializeReportBody exists");
  const end = emitted.indexOf("\n}\n", start);
  return emitted.slice(start, end + 3);
}

test("there is exactly one POST site, and its only body comes from serializeReportBody", () => {
  const posts = COMPONENTS.flatMap(([name, source]) =>
    [...code(source).matchAll(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/g)].map(
      (match) => [name, match[1]] as const,
    ),
  );
  assert.deepEqual(posts, [["rad-workstation.tsx", "POST"]]);
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes('async function postRad<T>(path: string, body: string | FormData = "{}")'));
  assert.ok(emitted.includes("    body,\n"), "the body is the parameter, bodiless by default");
  // A multipart body gets NO Content-Type: the browser writes the boundary.
  assert.ok(emitted.includes('headers: typeof body === "string" ? { "Content-Type": "application/json" } : undefined,'));
  // FormData is built in exactly one place: imageUploadForm.
  for (const [name, source] of COMPONENTS) {
    const stripped =
      name === "rad-desk-format.ts"
        ? code(source).replace(imageUploadFormSource(), "")
        : code(source);
    assert.doesNotMatch(stripped, /new FormData\(/, `${name} must build no form`);
  }
  // JSON.stringify exists in exactly one place: serializeReportBody, over
  // reportBody()'s allow-listed keys.
  for (const [name, source] of COMPONENTS) {
    const stripped =
      name === "rad-desk-format.ts"
        ? code(source).replace(serializeReportBodySource(), "")
        : code(source);
    assert.doesNotMatch(stripped, /JSON\.stringify\(/, `${name} must build no request body`);
  }
  assert.match(serializeReportBodySource(), /return JSON\.stringify\(reportBody\(draft\)\);/);
});

test("the POST site is only ever pointed at the transition and report paths", () => {
  const emitted = code(WORKSTATION);
  // Call sites only (`await postRad<...>(`), up to the closing paren.
  const calls = [...emitted.matchAll(/await postRad<[^>]*>\(([\s\S]*?)\);/g)].map((m) =>
    m[1].replace(/\s+/g, " ").replace(/,\s*$/, "").trim(),
  );
  assert.deepEqual(calls, [
    "transitionPath(kind, requestId)",
    "reportPath(requestId)",
    "signoffPath(kind, report.result.id)",
    'kind === "save" ? reportSavePath(resultId) : reportEnterPath(resultId), serializeReportBody(draft)',
    "imagesPath(resultId), imageUploadForm(target.file, target.caption)",
    "imageRemovePath(resultId, target.imageId)",
  ]);
  assert.ok(format_includes("/api/radiology/results/${resultId}/validate"));
  assert.ok(format_includes("/api/radiology/results/${resultId}/release"));
  assert.ok(format_includes('return kind === "validate" ? reportValidatePath(resultId) : reportReleasePath(resultId);'));
  assert.ok(format_includes("/api/radiology/results/${resultId}/images"));
  assert.ok(format_includes("/api/radiology/results/${resultId}/images/${imageId}/remove"));
  assert.ok(format_includes("/api/radiology/requests/${requestId}/report"));
  assert.ok(format_includes("/api/radiology/results/${resultId}/save"));
  assert.ok(format_includes("/api/radiology/results/${resultId}/enter"));
  const format = code(FORMAT);
  assert.ok(format.includes('return kind === "schedule" ? schedulePath(requestId) : startPath(requestId);'));
  assert.ok(format.includes("/api/radiology/requests/${requestId}/schedule"));
  assert.ok(format.includes("/api/radiology/requests/${requestId}/start"));
});

function format_includes(text: string): boolean {
  return code(FORMAT).includes(text);
}

test("the panel's only controls are the transitions and the report actions", () => {
  const emitted = code(PANEL);
  const labels = [...emitted.matchAll(/"(Schedule study|Start exam|Confirm schedule|Confirm start|Cancel)"/g)].map((m) => m[1]);
  for (const label of ["Schedule study", "Start exam", "Confirm schedule", "Confirm start"]) {
    assert.ok(labels.includes(label), label);
  }
  assert.ok(emitted.includes(">\n          Cancel\n        </button>") || emitted.includes("Cancel"));
  // Every <button in the panel lives inside TransitionAction -- except the
  // report actions: Open report (offerReport), and View / Validate / Release
  // report (offerView).
  const actionStart = emitted.indexOf("function TransitionAction(");
  const actionEnd = emitted.indexOf("function ReportSection(");
  const outside = emitted.slice(0, actionStart) + emitted.slice(actionEnd);
  assert.equal([...outside.matchAll(/<button/g)].length, 4, "the report actions only");
  const viewBlock = outside.slice(outside.indexOf("{offerView ? ("));
  assert.equal(
    [...viewBlock.slice(0, viewBlock.indexOf("\n        ) : null}\n")).matchAll(/<button/g)].length,
    3,
  );
  const reportBlock = outside.slice(outside.indexOf("{offerReport ? ("));
  assert.ok(reportBlock.indexOf("<button") >= 0 && reportBlock.indexOf("<button") < reportBlock.indexOf(") : null}"));
  assert.ok(outside.includes('{reportPending ? "Opening…" : "Open report"}'));
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
    /Mark in progress/i, /Enter report/i, /Amend report/i, /Retract/i,
    /Delete image/i, /Cancel request/i, /Reset to draft/i, /Cancel report/i,
    /onValidate|onRelease|onCancelRequest|onEnter\b/,
  ];
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    for (const pattern of forbidden) {
      assert.doesNotMatch(emitted, pattern, `${name} offers ${pattern}`);
    }
  }
});

test("the panel says in words which actions this desk has", () => {
  assert.ok(PANEL.includes("Schedule, start, report entry, images, validation and release. Amendments are not available on this desk."));
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

/** reportEditable's body: the ONE report-state read, and it decides editing, not a lane. */
function reportEditableSource(): string {
  const emitted = code(FORMAT);
  const start = emitted.indexOf("export function reportEditable(");
  return emitted.slice(start, emitted.indexOf("\n}\n", start) + 3);
}

test("the browser never derives a lane", () => {
  assert.match(reportEditableSource(), /result\.state === "draft" &&/);
  for (const [name, source] of COMPONENTS) {
    const emitted =
      name === "rad-desk-format.ts"
        ? code(source).replace(reportEditableSource(), "")
        : code(source);
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

/* ------------------------------------------------------------------ *
 * The report (Slice 3)
 * ------------------------------------------------------------------ */

test("Open report is offered only through canOpenReport and the open_report capability", () => {
  const emitted = code(PANEL);
  assert.ok(
    emitted.includes(
      "const offerReport = capabilities?.open_report === true && canOpenReport(detail);",
    ),
  );
  const format = code(FORMAT);
  for (const rule of [
    'detail.state === "in_progress"',
    'detail.lane === "awaiting_report"',
    "detail.result_conflict === false",
    "detail.anomaly_reason === null",
    "detail.exam_count > 0",
  ]) {
    assert.ok(format.includes(rule), rule);
  }
});

test("the report sheet is editable only for a draft and a report-author role", () => {
  assert.ok(
    code(WORKSTATION).includes("editable={!report.signoff && reportEditable(report.result, capabilities)}"),
  );
  const helper = reportEditableSource();
  assert.ok(helper.includes("capabilities?.edit_report === true"));
  assert.ok(helper.includes("capabilities?.enter_report === true"));
  // Read-only is the ONLY other mode, and it offers Close and nothing else.
  const modal = code(MODAL);
  assert.ok(modal.includes("const readOnly = !editable;"));
  assert.ok(modal.includes("readOnly={readOnly}"));
  const readOnlyFooter = modal.slice(modal.indexOf("{readOnly ? (\n              <button"));
  assert.ok(readOnlyFooter.indexOf("Close") < readOnlyFooter.indexOf(") : ("));
});

test("the report sheet shows the request context and the tested labels", () => {
  const modal = code(MODAL);
  assert.ok(modal.includes("Radiology report"));
  assert.ok(modal.includes("reportContext(request, result)"));
  for (const label of ["Findings", "Impression", "Recommendations", "Result summary", "Notes"]) {
    assert.ok(modal.includes(label), label);
  }
  for (const label of ['"Saving…" : "Save draft"', "Mark entered", "Cancel", "Close"]) {
    assert.ok(modal.includes(label), label);
  }
  const format = code(FORMAT);
  for (const label of ["Patient", "MRN", "Request", "Report", "Exams", "Modality", "Body part", "Ordering doctor"]) {
    assert.ok(format.includes(`label: "${label}"`), label);
  }
});

test("the report sheet has no cancel, reset, amendment or retraction control", () => {
  const modal = code(MODAL);
  assert.doesNotMatch(modal, /Cancel report|Reset|Amend|Retract/);
  for (const source of [code(WORKSTATION), code(FORMAT)]) {
    assert.doesNotMatch(source, /\/cancel|\/reset|\/amend|\/retract/);
  }
});

test("Mark entered takes a second click, focuses Cancel, and has no shortcut", () => {
  const modal = code(MODAL);
  // Step one only opens the confirmation.
  const ask = modal.slice(modal.indexOf("const askToEnter"), modal.indexOf("const markEntered"));
  assert.doesNotMatch(ask, /onMarkEntered/);
  assert.ok(ask.includes("setConfirmEnter(true)"));
  // Step two refuses to run unless the confirmation is open.
  const mark = modal.slice(modal.indexOf("const markEntered"), modal.indexOf("/* ---------------- keyboard"));
  assert.ok(mark.includes("if (readOnly || busy !== null || !confirmEnter) return;"));
  assert.ok(modal.includes("{reportEnterConfirmText(result)}"));
  assert.ok(modal.includes("{REPORT_ENTER_SUPPORT_TEXT}"));
  assert.ok(modal.includes("if (confirmEnter) enterCancelRef.current?.focus();"));
  assert.ok(modal.includes("ref={enterCancelRef}"));
  // Ctrl/Cmd+Enter is swallowed and performs nothing.
  const keys = modal.slice(modal.indexOf("function onKeyDown"), modal.indexOf("const trapFocus"));
  assert.ok(keys.includes('if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {\n        event.preventDefault();\n      }'));
  assert.doesNotMatch(keys, /markEntered|saveDraft|onMarkEntered|onSaveDraft/);
  assert.doesNotMatch(modal, /isSaveShortcut/);
});

test("Escape backs out of the confirmation and guards unsaved text; busy disables every act", () => {
  const modal = code(MODAL);
  const keys = modal.slice(modal.indexOf("function onKeyDown"), modal.indexOf("const trapFocus"));
  assert.ok(keys.includes("if (confirmEnter) {\n          if (!isBusy) setConfirmEnter(false);"));
  assert.ok(keys.includes("escapeIntent("));
  assert.ok(modal.includes("closeIntent({ readOnly, changed, busy: isBusy })"));
  assert.ok(modal.includes("Discard the changes you have not saved?"));
  assert.ok(modal.includes("if (readOnly || busy !== null) return;"));
  assert.equal([...modal.matchAll(/disabled=\{isBusy \|\| confirmEnter\}/g)].length, 2);
  // Close, the entry confirmation's two buttons and Cancel -- plus, in the
  // Images section, Upload image/report, caption, Upload, Clear and the remove
  // confirmation's two buttons. Every act is disabled while anything is busy.
  // ...and, since Slice 5, the one passed to the sign-off step.
  assert.equal([...modal.matchAll(/disabled=\{isBusy\}/g)].length, 11);
  // Slice 5's sign-off step: its three buttons take the same `disabled` flag.
  assert.equal([...modal.matchAll(/disabled=\{disabled\}/g)].length, 3);
  assert.ok(modal.includes("disabled={isBusy}\n              goBackRef={goBackRef}"));
});

test("a refused save or entry keeps the text and shows the reason in the sheet", () => {
  const modal = code(MODAL);
  assert.ok(modal.includes("setError(outcome.message);"));
  assert.ok(modal.includes('role="alert"'));
  // The sheet never closes itself on entry: the workstation replaces it with
  // the entered, read-only report the server returned.
  const mark = modal.slice(modal.indexOf("const markEntered"), modal.indexOf("/* ---------------- keyboard"));
  assert.doesNotMatch(mark, /onClose\(\)/);
});

test("an entered report becomes a fresh read-only sheet, pinned, with the lane refetched", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("key={`${report.result.id}-${report.result.state}-${report.signoff ?? \"report\"}`}"));
  const write = emitted.slice(emitted.indexOf("const writeReport"), emitted.indexOf("const detailForSelection"));
  assert.ok(write.includes("setReport({ request: confirmed.request, result: confirmed.result });"));
  assert.ok(write.includes("setDetail(confirmed.request);"));
  const enter = write.slice(write.indexOf('if (kind === "enter") {'));
  assert.ok(enter.includes("setJustActedId(confirmed.request.id);"));
  assert.ok(enter.includes("reportEnteredOutcomeText(confirmed.result, confirmed.request)"));
  assert.ok(enter.includes("refresh();"));
  // A refusal replaces nothing on screen, and re-reads when stale.
  const refused = write.slice(write.indexOf("if (!response.ok || !payload.success) {"), write.indexOf("const confirmed"));
  assert.doesNotMatch(refused, /setDetail|setReport/);
  assert.ok(refused.includes("shouldReconcileAfterReport(codeFromPayload(payload))"));
});

test("the refresh warning covers a failed refresh after report entry too", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("setRefreshWarningFor(justActedRef.current);"));
});

/* ------------------------------------------------------------------ *
 * Images (Slice 4)
 * ------------------------------------------------------------------ */

function imageUploadFormSource(): string {
  const emitted = code(FORMAT);
  const start = emitted.indexOf("export function imageUploadForm(");
  assert.ok(start >= 0, "imageUploadForm exists");
  return emitted.slice(start, emitted.indexOf("\n}\n", start) + 3);
}

function imagesSectionSource(): string {
  const modal = code(MODAL);
  return modal.slice(modal.indexOf("function ImagesSection("));
}

test("the report sheet has an Images section, separate from the text's mutability", () => {
  const modal = code(MODAL);
  assert.ok(modal.includes("<ImagesSection"));
  assert.ok(modal.includes("editable={imagesEditable}"));
  // The text's read-only flag never touches the image controls.
  assert.doesNotMatch(imagesSectionSource(), /readOnly|canAuthor/);
  assert.ok(
    code(WORKSTATION).includes("imagesEditable={!report.signoff && imagesEditable(report.result, capabilities)}"),
  );
  // images_mutable is the SERVER's; the browser never reads report state for it.
  const format = code(FORMAT);
  const helper = format.slice(format.indexOf("export function imagesEditable("));
  assert.ok(helper.includes("result.images_mutable === true && capabilities?.manage_images === true"));
});

test("upload offers the tested types and limit, one file, through the BFF", () => {
  const section = imagesSectionSource();
  assert.ok(section.includes("Upload image/report"));
  assert.ok(section.includes("accept={ACCEPTED_IMAGE_TYPES}"));
  assert.ok(section.includes("{ACCEPTED_IMAGE_LABEL} · {MAX_IMAGE_LABEL}"));
  assert.doesNotMatch(section, /\bmultiple\b/);
  const format = code(FORMAT);
  assert.ok(format.includes('ACCEPTED_IMAGE_TYPES = "image/jpeg,image/png,application/pdf"'));
  assert.ok(format.includes('ACCEPTED_IMAGE_LABEL = "JPG, PNG or PDF"'));
  assert.ok(format.includes('MAX_IMAGE_LABEL = "Max file size: 25 MB"'));
  // The form carries the file and a caption, never a type, size or name field.
  const form = imageUploadFormSource();
  assert.ok(form.includes('form.append("file", file, file.name);'));
  assert.ok(form.includes('form.append("caption", trimmed);'));
  assert.equal([...form.matchAll(/form\.append\(/g)].length, 2);
});

test("previews and Open links use the desk's own BFF byte route, never Odoo or the Doctor's", () => {
  const section = imagesSectionSource();
  assert.ok(section.includes("const href = imagePath(resultId, image.id);"));
  assert.equal([...section.matchAll(/src=\{href\}/g)].length, 1);
  assert.equal([...section.matchAll(/href=\{href\}/g)].length, 1);
  assert.ok(section.includes('rel="noopener noreferrer"'));
  // A preview only for JPEG/PNG; a PDF is an item with Open, never embedded.
  assert.ok(section.includes('{kind === "image" ? ('));
  assert.doesNotMatch(section, /<iframe|<embed|<object|createObjectURL|FileReader|dangerouslySetInnerHTML/);
  for (const source of [code(MODAL), code(WORKSTATION), code(FORMAT)]) {
    assert.doesNotMatch(source, /\/web\/content|\/web\/image|\/api\/doctor|access_token|yoya-emr/);
  }
  assert.ok(code(FORMAT).includes("/api/radiology/results/${resultId}/images/${imageId}"));
});

test("removing a file takes a second click, focused on Cancel, with the tested wording", () => {
  const modal = code(MODAL);
  const section = imagesSectionSource();
  assert.ok(section.includes("{imageRemoveConfirmText(image)}"));
  assert.ok(section.includes("{IMAGE_REMOVE_SUPPORT_TEXT}"));
  assert.ok(section.includes('{busy === "remove" ? "Removing…" : "Remove file"}'));
  assert.ok(section.includes("ref={removeCancelRef}"));
  assert.ok(modal.includes("if (removeFor !== null) removeCancelRef.current?.focus();"));
  // The first button only opens the confirmation; only the final one removes.
  assert.ok(section.includes("onClick={() => onAskRemove(image.id)}"));
  assert.ok(section.includes("onClick={onConfirmRemove}"));
  const remove = modal.slice(modal.indexOf("const removeImage"), modal.indexOf("/* ---------------- keyboard"));
  assert.ok(remove.includes("if (!imagesEditable || removeFor === null || busy !== null) return;"));
  // Escape backs out of it; no key performs it.
  const keys = modal.slice(modal.indexOf("function onKeyDown"), modal.indexOf("const trapFocus"));
  assert.ok(keys.includes("if (removeFor !== null) {\n          if (!isBusy) setRemoveFor(null);"));
  assert.doesNotMatch(keys, /removeImage|onRemoveImage|uploadImage/);
  // Upload and remove controls exist only when the image set may change.
  assert.ok(section.includes("{editable ? (\n        <div"));
  assert.ok(section.includes("{editable && removeFor === image.id ? ("));
  assert.ok(section.includes("{editable ? (\n                      <button"));
});

test("a refused upload or removal keeps the sheet and the confirmed file list", () => {
  const modal = code(MODAL);
  const upload = modal.slice(modal.indexOf("const uploadImage"), modal.indexOf("const removeImage"));
  assert.ok(upload.includes("if (!imagesEditable || !selectedFile || busy !== null) return;"));
  assert.ok(upload.includes("setImageError(outcome.message);"));
  assert.doesNotMatch(upload, /onClose|setReport/);
  // The list rendered is always the report the server last returned.
  assert.ok(modal.includes("images={result.images}"));
  const write = code(WORKSTATION).slice(
    code(WORKSTATION).indexOf("const writeImage"),
    code(WORKSTATION).indexOf("const detailForSelection"),
  );
  const refused = write.slice(write.indexOf("if (!response.ok || !payload.success) {"), write.indexOf("const confirmed"));
  assert.doesNotMatch(refused, /setDetail|setReport/);
  assert.ok(refused.includes("shouldReconcileAfterImage(codeFromPayload(payload))"));
  // Success: the server's report replaces the one on screen, pinned; no lane is set.
  assert.ok(write.includes("setReport({ request: confirmed.request, result: confirmed.result });"));
  assert.ok(write.includes("setJustActedId(confirmed.request.id);"));
  assert.doesNotMatch(write, /setLane|lane:/);
  // An over-size file is refused locally and never sent.
  assert.ok(modal.includes("if (imageTooLarge(file)) {"));
});

test("an entered report is VIEWED from Awaiting validation, where its images stay manageable", () => {
  const format = code(FORMAT);
  // Open report creates the draft, so it is only for Awaiting report...
  assert.ok(format.includes('    detail.lane === "awaiting_report" &&\n    detail.result_conflict === false &&'));
  // ...and View report opens the report every later lane already holds.
  assert.ok(format.includes('detail.lane !== "awaiting_report"'));
  const panel = code(PANEL);
  assert.ok(panel.includes("const offerView = capabilities?.radiology_desk === true && canViewReport(detail);"));
});

/* ------------------------------------------------------------------ *
 * Validation and release (Slice 5)
 * ------------------------------------------------------------------ */

function signoffStepSource(): string {
  const modal = code(MODAL);
  return modal.slice(modal.indexOf("function SignoffStep("));
}

test("Validate and Release are offered only through the tested helpers and author capabilities", () => {
  const panel = code(PANEL);
  assert.ok(panel.includes("const offerValidate = canValidateReport(detail, capabilities);"));
  assert.ok(panel.includes("const offerRelease = canReleaseReport(detail, capabilities);"));
  assert.ok(panel.includes('onClick={(event) => onViewReport(detail.id, event.currentTarget, "validate")}'));
  assert.ok(panel.includes('onClick={(event) => onViewReport(detail.id, event.currentTarget, "release")}'));
  assert.ok(panel.includes('onClick={(event) => onViewReport(detail.id, event.currentTarget, null)}'));
  // A technician sees View report, and why there is nothing else.
  assert.ok(panel.includes("View only: a radiologist validates and releases the report."));
  const format = code(FORMAT);
  assert.ok(format.includes('capabilities?.validate_report === true &&'));
  assert.ok(format.includes('capabilities?.release_report === true &&'));
  assert.ok(format.includes('detail.lane === "awaiting_validation"'));
  assert.ok(format.includes('detail.lane === "awaiting_release"'));
});

test("the sign-off sheet is read-only, text AND images", () => {
  const ws = code(WORKSTATION);
  assert.ok(ws.includes("editable={!report.signoff && reportEditable(report.result, capabilities)}"));
  assert.ok(ws.includes("imagesEditable={!report.signoff && imagesEditable(report.result, capabilities)}"));
  // View / Validate / Release open from the detail payload: nothing is sent.
  const view = ws.slice(ws.indexOf("const viewReport"), ws.indexOf("const runSignoff"));
  assert.doesNotMatch(view, /postRad|fetch\(/);
  assert.ok(view.includes("setReport({ request: current, result: current.result, signoff });"));
});

test("both sign-offs take two steps, with the tested wording, focused on Go back", () => {
  const modal = code(MODAL);
  const step = signoffStepSource();
  assert.ok(modal.includes('? "Validate radiology report"'));
  assert.ok(modal.includes('? "Release radiology report"'));
  assert.ok(step.includes("{validate ? VALIDATE_REVIEW_TEXT : RELEASE_REVIEW_TEXT}"));
  assert.ok(step.includes('{validate ? "Validate report…" : "Release report…"}'));
  assert.ok(step.includes("{validate ? validateConfirmText(request, result) : releaseConfirmText(request, result)}"));
  assert.ok(step.includes("{releaseIdentityText(request)}"));
  assert.ok(step.includes("{validate ? VALIDATE_SUPPORT_TEXT : releaseSupportText(request)}"));
  assert.ok(step.includes('{busy ? "Working…" : validate ? "Confirm validation" : "Confirm release"}'));
  assert.ok(step.includes("Go back"));
  assert.ok(step.includes("ref={goBackRef}"));
  assert.ok(modal.includes('if (signStep === "confirm") goBackRef.current?.focus();'));
  // Step one only moves on; step two is the ONLY sender.
  const ask = modal.slice(modal.indexOf("const askToSign"), modal.indexOf("const confirmSign"));
  assert.doesNotMatch(ask, /onSignoff/);
  assert.ok(ask.includes('setSignStep("confirm");'));
  const confirm = modal.slice(modal.indexOf("const confirmSign"), modal.indexOf("const chooseFile"));
  assert.ok(confirm.includes('if (!signoff || !onSignoff || busy !== null || signStep !== "confirm") return;'));
  assert.ok(confirm.includes('setSignStep("review");'));
  assert.doesNotMatch(confirm, /onClose/);
});

test("no keyboard shortcut validates or releases; Escape goes back", () => {
  const modal = code(MODAL);
  const keys = modal.slice(modal.indexOf("function onKeyDown"), modal.indexOf("const trapFocus"));
  assert.ok(keys.includes('if (signStep === "confirm") {\n          if (!isBusy) setSignStep("review");'));
  assert.doesNotMatch(keys, /confirmSign|askToSign|onSignoff/);
  assert.ok(keys.includes('if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {\n        event.preventDefault();\n      }'));
});

test("a confirmed sign-off closes the sheet, pins the request and refetches the lanes", () => {
  const ws = code(WORKSTATION);
  const run = ws.slice(ws.indexOf("const runSignoff"), ws.indexOf("const writeReport"));
  assert.ok(run.includes("await postRad<RadSignoffResponse>(signoffPath(kind, report.result.id));"));
  const ok = run.slice(run.indexOf("const confirmed"));
  for (const line of [
    "setDetail(confirmed.request);",
    "setJustActedId(confirmed.request.id);",
    "text: signoffOutcomeText(kind, confirmed),",
    "setReport(null);",
    "refresh();",
  ]) {
    assert.ok(ok.includes(line), line);
  }
  const refused = run.slice(run.indexOf("if (!response.ok || !payload.success) {"), run.indexOf("const confirmed"));
  assert.doesNotMatch(refused, /setDetail|setReport|setJustActedId/);
  assert.ok(refused.includes("shouldReconcileAfterSignoff(codeFromPayload(payload))"));
  // A failed queue refresh after a confirmed act keeps the confirmed request
  // and warns -- the same mechanism every action uses.
  assert.ok(ws.includes("setRefreshWarningFor(justActedRef.current);"));
});

test("sign-off paths are BFF paths and nothing reaches the Doctor or Odoo from the desk", () => {
  for (const source of [code(WORKSTATION), code(FORMAT), code(MODAL), code(PANEL)]) {
    assert.doesNotMatch(source, /\/api\/doctor|yoya-emr|\/web\/content|localhost|:8069|:8171/);
  }
});

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
 *   1. READ ONLY. No request other than GET leaves this desk, and the detail
 *      panel renders no control at all -- not even a disabled one.
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
 * 1. Read only
 * ------------------------------------------------------------------ */

test("no request other than GET is ever sent", () => {
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    assert.doesNotMatch(emitted, /method:\s*["'](POST|PUT|PATCH|DELETE)["']/, name);
    assert.doesNotMatch(emitted, /JSON\.stringify\(/, `${name} must build no request body`);
  }
});

test("the detail panel renders no control, not even a disabled one", () => {
  const emitted = code(PANEL);
  assert.doesNotMatch(emitted, /<button/, "the read-only panel has no buttons");
  assert.doesNotMatch(emitted, /onClick=/);
  assert.doesNotMatch(emitted, /disabled/);
});

test("no workflow action is offered anywhere on the desk", () => {
  const forbidden = [
    /Schedule study/i, /Start (exam|study)/i, /Mark in progress/i, /Enter report/i,
    /Validate report/i, /Release report/i, /Upload image/i, /Delete image/i,
    /Cancel request/i, /onSchedule|onStart|onValidate|onRelease|onUpload/,
  ];
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    for (const pattern of forbidden) {
      assert.doesNotMatch(emitted, pattern, `${name} offers ${pattern}`);
    }
  }
});

test("the panel says in words that the desk is read only", () => {
  assert.ok(PANEL.includes("Read-only view. Workflow actions are not available on this desk yet."));
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
  assert.ok(fetches.length >= 3);
  for (const target of fetches) {
    assert.match(target, /^(RAD_SESSION_PATH|worklistPath\(|requestPath\()/, target);
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
  assert.ok(emitted.includes("visibleDetail(detail, activeId)"));
});

test("selection is derived, and lane, date, modality and search all refetch", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("resolveSelection(visibleRows, selectedId)"));
  assert.ok(emitted.includes("[statuses, date, modality, debouncedSearch, refreshToken]"));
  assert.ok(emitted.includes("onLaneChange={setLane}"));
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

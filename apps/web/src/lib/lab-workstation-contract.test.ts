/**
 * THE LABORATORY DESK'S POST-TRANSITION RECONCILIATION, HELD AT THE SOURCE.
 *
 * WHY THIS IS A SOURCE ASSERTION. lab-desk-format.test.ts proves the pure
 * helpers behave -- what the panel may render, and when it is loading. It
 * cannot prove the thing the UAT defect was actually about: WHICH VALUE THE
 * COMPONENT PASSES DOWN. A workstation that went back to handing the panel its
 * raw `detailLoading` flag would pass every pure test and hang the desk on the
 * first collection, and this project ships no DOM test stack to catch that by
 * rendering.
 *
 * Reading the source is crude, but it pins the properties exactly where they
 * regress, and it is the technique order-wizard-contract.test.ts already uses
 * to hold component wiring on the Doctor Desk.
 *
 * THE DEFECT, IN ONE PARAGRAPH. Collecting a sample transitions the request to
 * sample_collected, which removes it from the Ready lane the technician is
 * looking at. The queue emptied, the active selection became null, the
 * in-flight detail fetch was aborted (so its `finally` skipped the reset), and
 * the effect's "nothing selected" branch returned without touching state --
 * because writing state there cascades a render. Nothing could clear the flag.
 * The desk sat on "Loading request…" indefinitely while the backend had in
 * fact succeeded.
 *
 * FIVE PROPERTIES ARE HELD HERE:
 *
 *   1. THE PANEL NEVER RECEIVES THE RAW LOADING FLAG. It receives the derived
 *      value, which cannot hang.
 *   2. THE PANEL'S REQUEST COMES FROM visibleDetail, so a request just acted on
 *      stays on screen after it leaves the lane.
 *   3. A TRANSITION PINS ITS REQUEST, and a fresh selection clears the pin.
 *   4. NO TRANSITION CAN BE SUBMITTED TWICE from the browser.
 *   5. BOTH TRANSITIONS SHARE ONE HANDLER, so collection and start-processing
 *      cannot grow divergent pending/error/reconciliation behaviour.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function component(name: string): string {
  return readFileSync(
    new URL(`../components/laboratory/${name}`, import.meta.url),
    "utf8",
  );
}

const WORKSTATION = component("lab-workstation.tsx");
const PANEL = component("lab-request-panel.tsx");
const FILTERS = component("lab-filters.tsx");

/* ------------------------------------------------------------------ *
 * 1. The panel cannot be handed a flag that hangs
 * ------------------------------------------------------------------ */

test("the panel receives the DERIVED loading value, never the raw flag", () => {
  assert.ok(
    WORKSTATION.includes("loading={panelIsLoading}"),
    "the detail panel must consume the derived loading value",
  );
  assert.ok(
    !WORKSTATION.includes("loading={detailLoading}"),
    "handing the panel the raw flag is the UAT hang, and must not return",
  );
});

test("the derived value is produced by the tested helper", () => {
  assert.ok(WORKSTATION.includes("detailIsLoading("));
  assert.ok(
    WORKSTATION.includes("const panelIsLoading = detailIsLoading("),
    "the derivation must go through the helper the unit tests cover",
  );
});

test("the nothing-selected branch still writes no state", () => {
  // It must not: setState in an effect body cascades a render, which is the
  // lint rule that produced the early return in the first place. The fix is
  // derivation at the render site, not a write here.
  const branch = WORKSTATION.slice(
    WORKSTATION.indexOf("if (activeId === null) {"),
    WORKSTATION.indexOf("const controller = new AbortController();", WORKSTATION.indexOf("if (activeId === null) {")),
  );
  assert.ok(branch.length > 0, "the guard branch must still exist");
  assert.ok(
    !branch.includes("setDetailLoading"),
    "clearing the flag here would cascade a render",
  );
  assert.ok(
    !branch.includes("setDetail("),
    "clearing the detail here would cascade a render",
  );
});

/* ------------------------------------------------------------------ *
 * 2 & 3. The collected request stays visible
 * ------------------------------------------------------------------ */

test("the panel's request comes from visibleDetail", () => {
  assert.ok(
    WORKSTATION.includes("visibleDetail(detail, activeId, justActedId)"),
    "the panel must be able to show a request that has left the lane",
  );
});

test("a successful transition pins its request", () => {
  assert.ok(
    WORKSTATION.includes("setJustActedId(requestId)"),
    "the request acted on must stay on screen as its own confirmation",
  );
});

test("a fresh selection clears the pin", () => {
  assert.ok(
    WORKSTATION.includes("setJustActedId(null)"),
    "a real selection must win over the post-collection pin",
  );
});

test("the transitioned detail is taken from the authoritative response", () => {
  // Not patched locally: the server re-serializes after the transition, so the
  // status shown is the derived one rather than a value guessed from the click.
  assert.ok(WORKSTATION.includes("setDetail(payload.data.request)"));
});

/* ------------------------------------------------------------------ *
 * 4. Double submission
 * ------------------------------------------------------------------ */

test("a second transition cannot be launched while one is in flight", () => {
  assert.ok(
    WORKSTATION.includes("if (pendingId !== null) return;"),
    "the handler must refuse a concurrent submit",
  );
  const disabled = PANEL.match(/disabled=\{pending\}/g) ?? [];
  assert.equal(
    disabled.length, 3,
    "Collect, Start processing and Enter results must each disable themselves on submit",
  );
});

test("the pending state is keyed on the request, not a bare boolean", () => {
  // So an action in flight cannot grey out a different request's button.
  assert.ok(WORKSTATION.includes("pendingId !== null && pendingId === activeId"));
});

test("both transitions run through the one shared handler", () => {
  // Collection and start-processing must not grow separate pending, error or
  // reconciliation behaviour -- that is how the two drift apart.
  assert.ok(WORKSTATION.includes("const runTransition = useCallback("));
  assert.ok(WORKSTATION.includes("collectPath(requestId)"));
  assert.ok(WORKSTATION.includes("startProcessingPath(requestId)"));
});

test("start processing is offered for exactly one status in the panel", () => {
  assert.ok(PANEL.includes("canStartProcessing(detail.status)"));
  assert.ok(PANEL.includes("canCollect(detail.status)"));
  // Neither is ever rendered as a disabled control for a status it does not
  // belong to: both are inside a conditional, not passed a `disabled` status.
  assert.ok(!PANEL.includes("disabled={!canCollect"));
  assert.ok(!PANEL.includes("disabled={!canStartProcessing"));
});

/* ------------------------------------------------------------------ *
 * Slice scope, still held
 * ------------------------------------------------------------------ */

test("every laboratory mutation goes through one POST site", () => {
  for (const [name, source] of [
    ["workstation", WORKSTATION],
    ["panel", PANEL],
    ["filters", FILTERS],
  ] as const) {
    for (const verb of ['method: "PATCH"', 'method: "DELETE"', 'method: "PUT"']) {
      assert.ok(!source.includes(verb), `${name} must not ${verb}`);
    }
  }
  // ONE POST site, by construction: the two transitions and the three result
  // writes all go through postLab, so there is a single POST in the file.
  const posts = WORKSTATION.match(/method: "POST"/g) ?? [];
  assert.equal(posts.length, 1, "one shared POST site for every mutation");
  assert.ok(WORKSTATION.includes("async function postLab<T>("));
});

test("the browser still never addresses Odoo directly", () => {
  for (const [name, source] of [
    ["workstation", WORKSTATION],
    ["panel", PANEL],
    ["filters", FILTERS],
  ] as const) {
    assert.ok(!source.includes("yoya-emr/api"), `${name} must go through the BFF`);
    assert.ok(!source.includes("ODOO_BASE_URL"), `${name} must not know Odoo`);
    assert.ok(!source.includes("localhost:8"), `${name} must not name a port`);
  }
});

test("the lane badges read the server summary, never a local recount", () => {
  assert.ok(FILTERS.includes("laneCountLabel(summary, entry.key)"));
  assert.ok(
    !WORKSTATION.includes("statusCounts("),
    "recounting rows on the client is the lazy-badge defect",
  );
  assert.ok(WORKSTATION.includes("setSummary(payload.data.summary"));
});

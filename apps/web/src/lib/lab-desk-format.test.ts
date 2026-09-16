/**
 * Laboratory Desk: vocabulary, filtering, selection and route construction.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins.
 * Run with `npm test`.
 *
 * THE PROPERTIES THESE TESTS EXIST FOR:
 *
 *   1. THE CLIENT FORMATS A CLEARANCE VERDICT; IT NEVER REACHES ONE. There is
 *      no balance, no amount and no payer anywhere in this module, and the one
 *      sentence it renders about money names no figure. A browser that could
 *      compute "cleared" would be a second, drifting definition of a decision
 *      hospital_billing owns.
 *
 *   2. NO FABRICATED STATE. The seven display statuses are the seven the
 *      server derives, and `awaiting_clearance` / `ready_for_collection` are
 *      not database values -- they are `requested`, split by a boolean.
 *
 *   3. STATUS AND PRIORITY ARE NEVER COLOUR ALONE. Every status and every
 *      priority carries a text code as well as a tone.
 *
 *   4. SELECTION IS SAFE ACROSS A REFRESH. When the selected request leaves
 *      the queue, the detail panel must fall to something real or to nothing --
 *      never linger on a row that is gone.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// TYPE-ONLY. node:test runs with no path resolver and no transform, so a VALUE
// imported through "@/..." would fail to resolve -- types are erased, values
// are not. The constants in types/lab-desk.ts are therefore pinned by reading
// the source, which is the technique order-wizard-contract.test.ts already uses
// for exactly this problem.
import type {
  LabQueueRow,
  LabRequestDetail,
  LabWorklistSummary,
} from "@/types/lab-desk";

import {
  LAB_DESK_ACTIVE_STATUS_ORDER,
  LAB_DESK_STATUS_ORDER,
  ageSexLabel,
  canCollect,
  clearanceNotice,
  collectErrorMessage,
  collectPath,
  detailIsLoading,
  detailMatchesSelection,
  isKnownStatus,
  isTerminalStatus,
  isUrgentPriority,
  labPriorityCode,
  labPriorityLabel,
  labStatusCode,
  labStatusLabel,
  laneCount,
  laneCountLabel,
  matchesSearch,
  orDash,
  requestPath,
  resolveSelection,
  shouldReconcileAfter,
  testCountLabel,
  testLabel,
  visibleDetail,
  worklistPath,
} from "./lab-desk-format.ts";

/* ------------------------------------------------------------------ *
 * The type-level constants, read from source
 * ------------------------------------------------------------------ */

const TYPES_SOURCE = readFileSync(
  new URL("../types/lab-desk.ts", import.meta.url),
  "utf8",
);

/**
 * Pull one `export const NAME = [...] as const;` list out of the types module.
 *
 * Crude, and deliberately so: it asserts the property exactly where it would
 * regress. A constant renamed or emptied fails here rather than drifting away
 * from the runtime copy in lab-desk-format.ts unnoticed.
 */
function constFromTypes(name: string): string[] {
  const match = TYPES_SOURCE.match(
    new RegExp(`export const ${name} = \\[([^\\]]*)\\]`),
  );
  assert.ok(match, `${name} must be declared in types/lab-desk.ts`);
  return [...match[1].matchAll(/"([^"]+)"/g)].map((entry) => entry[1]);
}

const LAB_DESK_STATUSES = constFromTypes("LAB_DESK_STATUSES");
const LAB_DESK_ACTIVE_STATUSES = constFromTypes("LAB_DESK_ACTIVE_STATUSES");
const LAB_REQUEST_STATES = constFromTypes("LAB_REQUEST_STATES");
const LAB_PRIORITIES = constFromTypes("LAB_PRIORITIES");

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

function row(overrides: Partial<LabQueueRow> = {}): LabQueueRow {
  return {
    id: 1,
    request_code: "LABREQ0001",
    state: "requested",
    status: "ready_for_collection",
    status_label: "Ready for collection",
    priority: "routine",
    priority_label: "Routine",
    request_date: "2026-09-07",
    created_at: "2026-09-07T06:30:00",
    billing_blocked: false,
    active: true,
    patient: { id: 5, name: "Abebe Kebede", mrn: "PAT0042", age: 34, gender: "male" },
    ordering_physician: { id: 2, name: "Dr Alem" },
    encounter_code: "ENC0007",
    department: { id: 3, name: "Internal Medicine" },
    test_count: 2,
    tests_summary: "CBC · Creatinine",
    result_count: 0,
    released_count: 0,
    ...overrides,
  };
}

function detail(overrides: Partial<LabRequestDetail> = {}): LabRequestDetail {
  return {
    ...row(),
    tests: [],
    clinical_notes: null,
    instructions: null,
    ...overrides,
  };
}

/* ------------------------------------------------------------------ *
 * 1. The vocabulary is the server's, and nothing is invented
 * ------------------------------------------------------------------ */

test("the runtime status list agrees with the type-level one", () => {
  // The two copies exist so the format module stays free of value imports;
  // this is what stops them drifting.
  assert.deepEqual([...LAB_DESK_STATUS_ORDER], [...LAB_DESK_STATUSES]);
});

test("the runtime active-status list agrees with the type-level one", () => {
  assert.deepEqual(
    [...LAB_DESK_ACTIVE_STATUS_ORDER],
    [...LAB_DESK_ACTIVE_STATUSES],
  );
});

test("the default queue is a strict subset of the full vocabulary", () => {
  for (const status of LAB_DESK_ACTIVE_STATUS_ORDER) {
    assert.ok(
      LAB_DESK_STATUS_ORDER.includes(status),
      `${status} must be a real bench status`,
    );
  }
  assert.ok(LAB_DESK_ACTIVE_STATUS_ORDER.length < LAB_DESK_STATUS_ORDER.length);
});

test("the default queue excludes draft, completed and cancelled", () => {
  for (const status of ["draft", "completed", "cancelled"]) {
    assert.ok(
      !LAB_DESK_ACTIVE_STATUS_ORDER.includes(status),
      `${status} is not active bench work`,
    );
  }
});

test("the default queue includes awaiting_clearance", () => {
  // A blocked order is still bench business: the technician is the person the
  // patient asks. Dropping it would strand exactly that patient.
  assert.ok(LAB_DESK_ACTIVE_STATUS_ORDER.includes("awaiting_clearance"));
});

test("the two derived statuses are NOT request states", () => {
  const states: readonly string[] = LAB_REQUEST_STATES;
  assert.ok(!states.includes("awaiting_clearance"));
  assert.ok(!states.includes("ready_for_collection"));
});

test("every real request state is representable as a bench status", () => {
  // draft, sample_collected, in_progress, completed and cancelled map one to
  // one; `requested` is the one that splits.
  for (const state of LAB_REQUEST_STATES) {
    if (state === "requested") continue;
    assert.ok(
      LAB_DESK_STATUS_ORDER.includes(state),
      `${state} must have a bench status`,
    );
  }
});

test("no invented status leaks through the label helpers", () => {
  assert.equal(labStatusLabel("processing_complete"), "processing_complete");
  assert.equal(labStatusLabel(null), "—");
  assert.equal(labStatusLabel(undefined), "—");
});

test("status labels are stated for every known status", () => {
  for (const status of LAB_DESK_STATUS_ORDER) {
    const label = labStatusLabel(status);
    assert.notEqual(label, status, `${status} must have real wording`);
    assert.ok(label.length > 0);
  }
});

test("isKnownStatus narrows only real statuses", () => {
  assert.ok(isKnownStatus("awaiting_clearance"));
  assert.ok(!isKnownStatus("released_request"));
});

/* ------------------------------------------------------------------ *
 * 2. Status and priority are never colour alone
 * ------------------------------------------------------------------ */

test("every status carries a distinct text code", () => {
  const codes = LAB_DESK_STATUS_ORDER.map(labStatusCode);
  assert.equal(new Set(codes).size, codes.length, "codes must be distinguishable");
  for (const code of codes) assert.ok(code.length > 0);
});

test("every priority carries a label and a distinct text code", () => {
  const codes = LAB_PRIORITIES.map(labPriorityCode);
  assert.equal(new Set(codes).size, codes.length);
  assert.equal(labPriorityLabel("stat"), "STAT");
  assert.equal(labPriorityLabel("routine"), "Routine");
  assert.equal(labPriorityLabel("urgent"), "Urgent");
});

test("priority falls back to routine rather than blank", () => {
  assert.equal(labPriorityLabel(null), "Routine");
  assert.equal(labPriorityCode(undefined), "RTN");
});

test("urgency is flagged for urgent and stat only", () => {
  assert.ok(isUrgentPriority("urgent"));
  assert.ok(isUrgentPriority("stat"));
  assert.ok(!isUrgentPriority("routine"));
  assert.ok(!isUrgentPriority(null));
});

test("terminal statuses are the two that no longer move", () => {
  assert.ok(isTerminalStatus("completed"));
  assert.ok(isTerminalStatus("cancelled"));
  for (const status of LAB_DESK_ACTIVE_STATUS_ORDER) {
    assert.ok(!isTerminalStatus(status), `${status} is still live work`);
  }
});

/* ------------------------------------------------------------------ *
 * 3. Money never appears, and clearance is never computed here
 * ------------------------------------------------------------------ */

test("the clearance notice names no figure and no payer", () => {
  const notice = clearanceNotice("awaiting_clearance") ?? "";
  assert.ok(notice.length > 0);
  for (const banned of [
    "ETB", "amount", "balance", "outstanding", "due", "payer",
    "invoice", "receipt", "insur", "birr",
  ]) {
    assert.ok(
      !notice.toLowerCase().includes(banned.toLowerCase()),
      `the clearance notice must not mention "${banned}"`,
    );
  }
});

test("the clearance notice is derived from the status, not from figures", () => {
  // The helper takes a status key and nothing else. There is no amount to pass
  // it, which is the structural version of this guarantee.
  assert.equal(clearanceNotice("ready_for_collection"), "Ready for collection.");
  assert.equal(clearanceNotice("sample_collected"), null);
  assert.equal(clearanceNotice("in_progress"), null);
  assert.equal(clearanceNotice("completed"), null);
  assert.equal(clearanceNotice(null), null);
});

test("a blocked row is rendered from the boolean the server sent", () => {
  const blocked = row({
    status: "awaiting_clearance",
    status_label: "Awaiting clearance",
    state: "requested",
    billing_blocked: true,
  });
  assert.equal(blocked.billing_blocked, true);
  assert.equal(typeof blocked.billing_blocked, "boolean");
  assert.ok(clearanceNotice(blocked.status));
});

/* ------------------------------------------------------------------ *
 * 4. Safe display of missing values
 * ------------------------------------------------------------------ */

test("missing values render as an em dash, never as blank", () => {
  assert.equal(orDash(null), "—");
  assert.equal(orDash(undefined), "—");
  assert.equal(orDash("   "), "—");
  assert.equal(orDash(" CBC "), "CBC");
});

test("age and sex degrade one half at a time", () => {
  assert.equal(ageSexLabel({ age: 34, gender: "female" }), "34 / F");
  assert.equal(ageSexLabel({ age: null, gender: "male" }), "— / M");
  // hospital.patient.age computes to 0 with no date of birth, which is
  // "not recorded" rather than a newborn.
  assert.equal(ageSexLabel({ age: 0, gender: "male" }), "— / M");
  assert.equal(ageSexLabel({ age: 34, gender: null }), "34 / —");
  assert.equal(ageSexLabel(null), "—");
});

test("a test renders with its catalogue code when there is one", () => {
  assert.equal(testLabel({ name: "CBC", code: "CBC01" }), "CBC (CBC01)");
  assert.equal(testLabel({ name: "CBC", code: null }), "CBC");
});

test("the test count is pluralised", () => {
  assert.equal(testCountLabel(1), "1 test");
  assert.equal(testCountLabel(3), "3 tests");
  assert.equal(testCountLabel(0), "0 tests");
});

/* ------------------------------------------------------------------ *
 * 5. Queue filtering and counts
 * ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ *
 * Lane counts (UAT defect fix)
 *
 * THE BUG THESE EXIST FOR: the client used to recount the rows on screen --
 * already narrowed to the selected lane AND capped at one page -- so every
 * unclicked lane read 0, and clicking it "discovered" the real number. The
 * counts now come from the server and describe the whole date+search scope.
 * ------------------------------------------------------------------ */

function summary(
  overrides: Partial<LabWorklistSummary> = {},
): LabWorklistSummary {
  return {
    active_bench: 173,
    draft: 4,
    awaiting_clearance: 45,
    ready_for_collection: 50,
    sample_collected: 75,
    in_progress: 3,
    completed: 4,
    cancelled: 13,
    requested_total: 95,
    ...overrides,
  };
}

test("every lane has a count, including ones never clicked", () => {
  const s = summary();
  for (const status of LAB_DESK_STATUS_ORDER) {
    assert.equal(
      typeof laneCount(s, status), "number",
      `${status} must have a count without being selected`,
    );
  }
  // The exact UAT symptom: ready_for_collection read 0 until clicked.
  assert.equal(laneCount(s, "ready_for_collection"), 50);
  assert.equal(laneCount(s, "awaiting_clearance"), 45);
});

test("the active bench lane maps to active_bench", () => {
  assert.equal(laneCount(summary(), "active"), 173);
});

test("active_bench is the four active statuses, not all seven", () => {
  // Pinned so a future change cannot quietly fold draft/completed/cancelled in.
  const s = summary();
  const active =
    (s.awaiting_clearance ?? 0) +
    (s.ready_for_collection ?? 0) +
    s.sample_collected +
    s.in_progress;
  assert.equal(s.active_bench, active);
  assert.equal(LAB_DESK_ACTIVE_STATUS_ORDER.length, 4);
});

test("counts never come from the rows on screen", () => {
  // laneCount takes the SUMMARY only. There is no row array in scope, which is
  // the structural version of "the page cannot be mistaken for the total".
  assert.equal(laneCount(summary(), "ready_for_collection"), 50);
});

test("an unknown count renders as a dash, never a zero", () => {
  // A zero would read as "no work here"; the split is simply unknown.
  const capped = summary({
    awaiting_clearance: null,
    ready_for_collection: null,
    active_bench: null,
  });
  assert.equal(laneCount(capped, "ready_for_collection"), null);
  assert.equal(laneCountLabel(capped, "ready_for_collection"), "—");
  assert.equal(laneCountLabel(capped, "active"), "—");
  // The statuses the server CAN always count stay numeric.
  assert.equal(laneCountLabel(capped, "sample_collected"), "75");
});

test("before the first load every lane shows a dash", () => {
  for (const status of LAB_DESK_STATUS_ORDER) {
    assert.equal(laneCountLabel(null, status), "—");
  }
  assert.equal(laneCountLabel(null, "active"), "—");
});

test("a real zero is shown as zero, not as a dash", () => {
  const empty = summary({
    active_bench: 0, draft: 0, awaiting_clearance: 0, ready_for_collection: 0,
    sample_collected: 0, in_progress: 0, completed: 0, cancelled: 0,
    requested_total: 0,
  });
  assert.equal(laneCountLabel(empty, "ready_for_collection"), "0");
  assert.equal(laneCountLabel(empty, "active"), "0");
});

test("the summary carries no financial vocabulary", () => {
  for (const key of Object.keys(summary())) {
    for (const banned of [
      "amount", "balance", "payer", "invoice", "receipt", "charge", "price",
    ]) {
      assert.ok(!key.includes(banned), `summary key "${key}" is financial`);
    }
  }
});

test("the browser never splits requested itself", () => {
  // awaiting/ready arrive already decided; requested_total is reported for
  // transparency but is NOT used to derive them client-side.
  const s = summary();
  assert.equal(s.requested_total, 95);
  assert.equal((s.awaiting_clearance ?? 0) + (s.ready_for_collection ?? 0), 95);
  // And when the server cannot split, the client does NOT fill the gap in.
  const capped = summary({ awaiting_clearance: null, ready_for_collection: null });
  assert.equal(laneCount(capped, "ready_for_collection"), null);
});

test("search matches request code, patient name and chart number", () => {
  const target = row({ request_code: "LABREQ0188", patient: { id: 5, name: "Abebe Kebede", mrn: "PAT0042", age: 34, gender: "male" } });
  assert.ok(matchesSearch(target, "labreq0188"));
  assert.ok(matchesSearch(target, "abebe"));
  assert.ok(matchesSearch(target, "PAT0042"));
  assert.ok(!matchesSearch(target, "zzz"));
});

test("an empty search term matches everything", () => {
  assert.ok(matchesSearch(row(), ""));
  assert.ok(matchesSearch(row(), "   "));
});

test("search survives a row with no patient", () => {
  assert.ok(!matchesSearch(row({ patient: null }), "abebe"));
  assert.ok(matchesSearch(row({ patient: null }), "LABREQ0001"));
});

/* ------------------------------------------------------------------ *
 * 6. Selection is stable, and safe when the queue changes
 * ------------------------------------------------------------------ */

test("the selection is kept when the request is still in the queue", () => {
  const rows = [row({ id: 1 }), row({ id: 2 }), row({ id: 3 })];
  assert.equal(resolveSelection(rows, 2), 2);
});

test("the selection falls to the top when the request has gone", () => {
  const rows = [row({ id: 4 }), row({ id: 5 })];
  assert.equal(resolveSelection(rows, 2), 4);
});

test("an empty queue clears the selection rather than keeping a ghost", () => {
  assert.equal(resolveSelection([], 2), null);
  assert.equal(resolveSelection([], null), null);
});

test("the detail panel only renders against the row it belongs to", () => {
  const loaded = detail({ id: 7 });
  assert.ok(detailMatchesSelection(loaded, 7));
  assert.ok(!detailMatchesSelection(loaded, 8));
  assert.ok(!detailMatchesSelection(loaded, null));
  assert.ok(!detailMatchesSelection(null, 7));
});

/* ------------------------------------------------------------------ *
 * 7. Route construction
 * ------------------------------------------------------------------ */

test("every route points at the BFF, never at Odoo", () => {
  assert.ok(worklistPath({}).startsWith("/api/laboratory/"));
  assert.ok(requestPath(9).startsWith("/api/laboratory/"));
  assert.equal(requestPath(9), "/api/laboratory/requests/9");
});

test("the worklist path joins statuses and omits empty values", () => {
  assert.equal(
    worklistPath({ status: ["awaiting_clearance", "in_progress"] }),
    "/api/laboratory/worklist?status=awaiting_clearance%2Cin_progress",
  );
  // No `q=` for an empty search: upstream would read it as a search for the
  // empty string.
  assert.equal(worklistPath({ q: "   " }), "/api/laboratory/worklist");
  assert.equal(worklistPath({ date: null, limit: null }), "/api/laboratory/worklist");
});

test("the worklist path carries date, search and limit when given", () => {
  const path = worklistPath({ date: "2026-09-07", q: " abebe ", limit: 50 });
  assert.ok(path.includes("date=2026-09-07"));
  assert.ok(path.includes("q=abebe"));
  assert.ok(path.includes("limit=50"));
});

test("no route builder accepts or emits a financial parameter", () => {
  const path = worklistPath({
    status: [...LAB_DESK_STATUS_ORDER],
    date: "2026-09-07",
    q: "abebe",
    limit: 100,
  });
  for (const banned of ["amount", "balance", "payer", "invoice", "charge"]) {
    assert.ok(!path.includes(banned), `${banned} must not appear in a lab route`);
  }
});

/* ------------------------------------------------------------------ *
 * 8. Sample collection (Slice 2)
 *
 * THE PROPERTY THESE EXIST FOR: the browser offers Collect from the SERVER'S
 * derived status and from nothing else. It never inspects a balance, an
 * amount or `billing_blocked` to decide, because deciding "is this cleared"
 * in the client would be a second definition of a verdict the billing engine
 * owns -- and one that drifts.
 * ------------------------------------------------------------------ */

test("collect is offered for ready_for_collection and no other status", () => {
  assert.ok(canCollect("ready_for_collection"));
  for (const status of LAB_DESK_STATUS_ORDER) {
    if (status === "ready_for_collection") continue;
    assert.ok(!canCollect(status), `${status} must not offer Collect`);
  }
});

test("awaiting_clearance never offers collect", () => {
  // The money case, called out separately because it is the one a stale
  // screen is most likely to get wrong.
  assert.ok(!canCollect("awaiting_clearance"));
});

test("collect is not offered for an unknown or missing status", () => {
  assert.ok(!canCollect(null));
  assert.ok(!canCollect(undefined));
  assert.ok(!canCollect("processing_complete"));
  assert.ok(!canCollect(""));
});

test("canCollect takes a status key only, so it cannot read a financial value", () => {
  // Structural guarantee: there is no row, no boolean pair and no amount in
  // scope. A blocked request and a cleared one differ ONLY by the status the
  // server derived.
  assert.equal(canCollect(row({ billing_blocked: true }).status), true);
  assert.equal(
    canCollect(row({ status: "awaiting_clearance", billing_blocked: false }).status),
    false,
  );
});

test("the collect route points at the BFF, never at Odoo", () => {
  assert.equal(collectPath(117), "/api/laboratory/requests/117/collect");
  assert.ok(collectPath(117).startsWith("/api/laboratory/"));
  assert.ok(!collectPath(117).includes("yoya-emr"));
  assert.ok(!collectPath(117).includes("8171"));
  assert.ok(!collectPath(117).includes("http"));
});

test("the collect route carries no financial parameter", () => {
  for (const banned of ["amount", "balance", "payer", "invoice", "charge", "receipt"]) {
    assert.ok(!collectPath(117).includes(banned));
  }
});

test("a refusal shows the server's own sentence", () => {
  // It is the only thing that says WHY, and the Lab API has already sanitised
  // the one refusal whose wording could carry an amount.
  assert.equal(
    collectErrorMessage("This request is not financially cleared."),
    "This request is not financially cleared.",
  );
});

test("a refusal with no server sentence falls back to safe wording", () => {
  const fallback = collectErrorMessage(null);
  assert.ok(fallback.length > 0);
  assert.equal(collectErrorMessage(""), fallback);
  assert.equal(collectErrorMessage("   "), fallback);
  assert.equal(collectErrorMessage(undefined), fallback);
});

test("no fallback collect message invents financial vocabulary", () => {
  const messages = [
    collectErrorMessage(null),
    collectErrorMessage(null, "The sample could not be marked collected."),
  ];
  for (const message of messages) {
    for (const banned of [
      "ETB", "amount", "balance", "outstanding", "due", "payer",
      "invoice", "receipt", "birr", "charge",
    ]) {
      assert.ok(
        !message.toLowerCase().includes(banned.toLowerCase()),
        `"${banned}" must not appear in a collect message`,
      );
    }
  }
});

test("a stale screen reconciles after the refusals the server can give it", () => {
  // All three mean the row on screen no longer matches the database.
  assert.ok(shouldReconcileAfter("invalid_workflow_state"));
  assert.ok(shouldReconcileAfter("lab_not_financially_cleared"));
  assert.ok(shouldReconcileAfter("lab_request_not_found"));
});

test("a transport failure does not trigger reconciliation", () => {
  // Nothing changed server-side, so refetching would only hide the error.
  assert.ok(!shouldReconcileAfter("lab_collect_failed"));
  assert.ok(!shouldReconcileAfter("odoo_unreachable"));
  assert.ok(!shouldReconcileAfter(null));
  assert.ok(!shouldReconcileAfter(undefined));
});

test("collection does not disturb the Slice 1 vocabulary", () => {
  // sample_collected remains a plain display status with no new key, and the
  // status list is unchanged in length and order.
  assert.equal(labStatusLabel("sample_collected"), "Sample collected");
  assert.equal(labStatusCode("sample_collected"), "COLL");
  assert.equal(LAB_DESK_STATUS_ORDER.length, 7);
  assert.equal(clearanceNotice("sample_collected"), null);
});


/* ------------------------------------------------------------------ *
 * 9. Post-collection reconciliation (UAT defect)
 *
 * THE BUG THESE EXIST FOR. A technician collected a sample. The backend
 * transitioned the request to sample_collected correctly -- but the request
 * then left the Ready lane the desk was showing, the queue emptied, the active
 * selection became null, and the panel sat on "Loading request…" forever.
 *
 * Two independent faults made that possible, and both are pinned below:
 *   1. the detail panel consumed a RAW loading flag that two code paths could
 *      leave true with nothing able to clear it
 *   2. the panel could only render a request that was still in the queue, so
 *      the request just acted on disappeared instead of confirming itself
 * ------------------------------------------------------------------ */

test("a collected request stays on screen after it leaves the lane", () => {
  const collected = detail({ id: 215, status: "sample_collected" });
  // It is gone from the Ready lane, so there is no active selection...
  assert.equal(visibleDetail(collected, null, 215), collected);
  // ...and its status is the transition the technician just performed.
  assert.equal(visibleDetail(collected, null, 215)?.status, "sample_collected");
});

test("the collect button disappears once the status has moved", () => {
  const collected = detail({ id: 215, status: "sample_collected" });
  const shown = visibleDetail(collected, null, 215);
  assert.ok(shown);
  assert.ok(!canCollect(shown.status), "Collect must not be offered again");
});

test("a normal selection still renders through activeId", () => {
  const selected = detail({ id: 7 });
  assert.equal(visibleDetail(selected, 7, null), selected);
});

test("a fresh selection wins over the post-collection pin", () => {
  const collected = detail({ id: 215 });
  // The pin is cleared on select, so a detail for another row shows nothing.
  assert.equal(visibleDetail(collected, 9, null), null);
});

test("the panel never renders a request that is neither selected nor pinned", () => {
  const stale = detail({ id: 1 });
  assert.equal(visibleDetail(stale, 2, 3), null);
  assert.equal(visibleDetail(stale, null, null), null);
  assert.equal(visibleDetail(null, 1, 1), null);
});

/* ---- the loading flag can no longer hang ---- */

test("nothing selected means nothing is loading", () => {
  // THE EXACT HANG: activeId null with the raw flag stuck true.
  assert.equal(detailIsLoading(null, true, null), false);
});

test("a request already on screen is not loading from empty", () => {
  const shown = detail({ id: 215, status: "sample_collected" });
  assert.equal(detailIsLoading(null, true, shown), false);
  assert.equal(detailIsLoading(215, true, shown), false);
});

test("a genuine first load still shows the loading state", () => {
  assert.equal(detailIsLoading(7, true, null), true);
});

test("a settled fetch is not loading", () => {
  assert.equal(detailIsLoading(7, false, null), false);
});

test("no combination of inputs can hang the panel with nothing selected", () => {
  // Exhaustive over the branch that produced the UAT defect.
  for (const flag of [true, false]) {
    for (const shown of [null, detail({ id: 215 })]) {
      assert.equal(
        detailIsLoading(null, flag, shown), false,
        "activeId null must always settle",
      );
    }
  }
});

test("a detail error state is reachable rather than masked by loading", () => {
  // With a selection and no detail, loading is true only while the flag is;
  // once it settles the panel can render the error instead of a spinner.
  assert.equal(detailIsLoading(7, true, null), true);
  assert.equal(detailIsLoading(7, false, null), false);
});

test("a secondary refresh failure cannot erase the collected result", () => {
  // A failed worklist refetch empties the queue (rows = [], summary = null),
  // so activeId goes null -- the collected request must still be on screen and
  // the lanes must read as unknown rather than zero.
  const collected = detail({ id: 215, status: "sample_collected" });
  assert.equal(visibleDetail(collected, null, 215), collected);
  assert.equal(detailIsLoading(null, true, collected), false);
  assert.equal(laneCountLabel(null, "ready_for_collection"), "—");
});

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
import type { LabQueueRow, LabRequestDetail } from "@/types/lab-desk";

import {
  LAB_DESK_ACTIVE_STATUS_ORDER,
  LAB_DESK_STATUS_ORDER,
  ageSexLabel,
  clearanceNotice,
  detailMatchesSelection,
  isKnownStatus,
  isTerminalStatus,
  isUrgentPriority,
  labPriorityCode,
  labPriorityLabel,
  labStatusCode,
  labStatusLabel,
  matchesSearch,
  orDash,
  requestPath,
  resolveSelection,
  statusCounts,
  testCountLabel,
  testLabel,
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

test("counts cover every status, including the empty ones", () => {
  const counts = statusCounts([
    row({ id: 1, status: "ready_for_collection" }),
    row({ id: 2, status: "ready_for_collection" }),
    row({ id: 3, status: "awaiting_clearance" }),
  ]);
  for (const status of LAB_DESK_STATUS_ORDER) {
    assert.equal(typeof counts[status], "number", `${status} must be counted`);
  }
  assert.equal(counts.ready_for_collection, 2);
  assert.equal(counts.awaiting_clearance, 1);
  assert.equal(counts.completed, 0);
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

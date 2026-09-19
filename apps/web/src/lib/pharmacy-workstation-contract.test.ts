/**
 * THE PHARMACY DESK'S READ-ONLY AND CONFIDENTIALITY GUARANTEES, HELD AT THE
 * SOURCE (no DOM test stack exists; the technique rad-workstation-contract uses).
 * Comments are stripped before any "must not contain" check.
 *
 *   1. READ ONLY: only GETs, no mutation control anywhere, none in the panel.
 *   2. NO ODOO: every URL is a /api/pharmacy/* BFF path.
 *   3. NO MONEY: no financial vocabulary rendered or read; billing is boolean.
 *   4. SERVER-DERIVED STATE: badges from the summary, lane never derived here.
 *   5. SAFE STATES: loading, empty, error, not-your-workstation, anomaly.
 *   6. NO PROVENANCE CLAIM: nothing says who dispensed.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const WORKSTATION = read("components/pharmacy/pharmacy-workstation.tsx");
const PANEL = read("components/pharmacy/pharmacy-dispense-panel.tsx");
const QUEUE = read("components/pharmacy/pharmacy-queue.tsx");
const FILTERS = read("components/pharmacy/pharmacy-filters.tsx");
const PILLS = read("components/pharmacy/pharmacy-status-pill.tsx");
const FORMAT = read("lib/pharmacy-desk-format.ts");
const LAYOUT = read("app/pharmacy/layout.tsx");
const PAGE = read("app/pharmacy/page.tsx");
const TYPES = read("types/pharmacy-desk.ts");

const COMPONENTS: ReadonlyArray<readonly [string, string]> = [
  ["pharmacy-workstation.tsx", WORKSTATION],
  ["pharmacy-dispense-panel.tsx", PANEL],
  ["pharmacy-queue.tsx", QUEUE],
  ["pharmacy-filters.tsx", FILTERS],
  ["pharmacy-status-pill.tsx", PILLS],
  ["pharmacy-desk-format.ts", FORMAT],
  ["layout.tsx", LAYOUT],
  ["page.tsx", PAGE],
];

/* 1. Read only */

test("no request other than GET is ever sent", () => {
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    assert.doesNotMatch(emitted, /method:\s*["'](POST|PUT|PATCH|DELETE)["']/, name);
    assert.doesNotMatch(emitted, /JSON\.stringify\(/, `${name} must build no request body`);
  }
});

test("the detail panel renders no control, not even a disabled one", () => {
  const emitted = code(PANEL);
  assert.doesNotMatch(emitted, /<button/);
  assert.doesNotMatch(emitted, /<input|<select|<textarea|<form/);
  assert.doesNotMatch(emitted, /onClick=|onChange=|onSubmit=/);
  assert.doesNotMatch(emitted, /disabled/);
});

test("no dispensing action is offered anywhere on the desk", () => {
  const forbidden = [
    /Mark ready/i, /Validate dispense/i, /Dispense now/i, /Hand over/i, /Cancel dispense/i,
    /Reset to draft/i, /Set quantity/i, /Save quantit/i, /Record payment/i,
    /onPrepare|onValidate|onCancel|onReady|onDispense/,
  ];
  for (const [name, source] of COMPONENTS) {
    for (const pattern of forbidden) {
      assert.doesNotMatch(code(source), pattern, `${name} offers ${pattern}`);
    }
  }
});

test("the panel says in words that the desk is read only", () => {
  assert.ok(PANEL.includes("Read-only view. Dispensing actions are not available on this desk yet."));
});

/* 2. No Odoo */

test("the browser never addresses Odoo", () => {
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    for (const marker of ["yoya-emr", "http://", "https://", "8069", "/web/", "session_id"]) {
      assert.ok(!emitted.includes(marker), `${name} must not contain ${marker}`);
    }
  }
});

test("every fetch goes through a BFF path helper", () => {
  const emitted = code(WORKSTATION);
  const fetches = [...emitted.matchAll(/fetch\(\s*([^,\n]+)/g)].map((match) => match[1].trim());
  assert.ok(fetches.length >= 3);
  for (const target of fetches) {
    assert.match(target, /^(PHARMACY_SESSION_PATH|worklistPath\(|dispensePath\()/, target);
  }
});

/* 3. No money */

test("no financial vocabulary is rendered or read", () => {
  const forbidden = /amount|invoice|receipt|price|balance|payer|charge_line|clearance_message|currency|subtotal|tariff|\bbirr\b|\betb\b|toFixed\(2\)/i;
  for (const [name, source] of COMPONENTS) {
    assert.doesNotMatch(code(source), forbidden, name);
  }
});

test("the wire contract carries billing only as booleans", () => {
  const types = code(TYPES);
  const billing = [...types.matchAll(/(\w*(?:billing|charge)\w*)\s*:\s*(\w+)/g)];
  assert.ok(billing.length >= 3);
  for (const [, key, kind] of billing) {
    assert.equal(kind, "boolean", `${key} must be a boolean`);
  }
  assert.doesNotMatch(types, /amount|invoice|receipt|price|payer|currency|subtotal/i);
});

/* 4. Server-derived state */

test("lane badges read the server summary, never a local recount", () => {
  const emitted = code(FILTERS);
  assert.ok(emitted.includes("laneCountLabel(summary, key)"));
  assert.doesNotMatch(emitted, /rows\.(length|filter|reduce)/);
});

test("the browser never derives a lane from state or quantities", () => {
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    assert.doesNotMatch(emitted, /\.state\s*===\s*["'](draft|ready|partial|dispensed)["']/, name);
    assert.doesNotMatch(emitted, /remaining_quantity\s*[<>]=?|intended_quantity\s*[<>]=?/, name);
  }
});

test("the panel's loading state is derived, not stored", () => {
  assert.ok(code(WORKSTATION).includes("detailIsLoading(activeId, detailLoading, detailForSelection)"));
});

/* 5. Safe states */

test("every safe state renders", () => {
  assert.ok(WORKSTATION.includes("This is not your workstation."));
  assert.ok(QUEUE.includes("No dispenses here"));
  assert.ok(QUEUE.includes('role="alert"'));
  assert.ok(QUEUE.includes("Queue limit reached."));
  assert.ok(PANEL.includes("Select a dispense"));
  assert.ok(PANEL.includes("Showing last loaded data"));
  assert.ok(PANEL.includes("No medicine lines could be read for this dispense."));
  assert.ok(code(PANEL).includes("reasonNotice(detail)"));
});

test("the detail shows the facts a pharmacist needs at a glance", () => {
  const emitted = code(PANEL);
  for (const needle of [
    "detail.patient?.name", "detail.patient?.mrn", "detail.prescriber?.name",
    "detail.prescription?.code", "detail.dispense_code", "detail.state",
    "line.prescribed_quantity", "line.intended_quantity", "line.delivered_quantity",
    "line.consumed_quantity", "line.remaining_quantity", "detail.billing_blocked",
    "stockLabel(line)", "billingLabel(line)",
  ]) {
    assert.ok(emitted.includes(needle), needle);
  }
});

test("dense queue: one grid for header and rows, keyboard navigation", () => {
  const emitted = code(QUEUE);
  assert.equal((emitted.match(/\$\{GRID\}/g) ?? []).length, 2);
  assert.ok(emitted.includes("ArrowDown"));
  assert.ok(emitted.includes("CLEARANCE"));
  assert.ok(emitted.includes("STOCK"));
});

/* 6. No provenance claim */

test("nothing claims who dispensed", () => {
  for (const [name, source] of COMPONENTS) {
    assert.doesNotMatch(code(source), /dispensed by|pharmacist_id|dispensed_by/i, name);
  }
  assert.doesNotMatch(code(TYPES), /pharmacist_id|dispensed_by/);
});

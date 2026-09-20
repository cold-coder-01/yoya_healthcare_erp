/**
 * THE PHARMACY DESK'S WORKFLOW AND CONFIDENTIALITY GUARANTEES, HELD AT THE
 * SOURCE (no DOM test stack exists; the technique rad-workstation-contract
 * uses). Comments are stripped before any "must not contain" check.
 *
 *   1. TWO WRITES ONLY. The only POSTs go to the prepare and validate BFF
 *      paths, from the workstation, with one operation token each. No line is
 *      ever saved directly and no other action is offered.
 *   2. SERVER-OFFERED. Inputs and buttons appear only on the record's own
 *      can_prepare / can_validate; the browser derives no lane.
 *   3. DELIBERATE CONFIRMATION. Validate is a pointer click in a separate
 *      step: no key handler, keyboard-activated clicks ignored, focus starts on
 *      "Go back".
 *   4. NO ODOO, NO MONEY, NO PROVENANCE CLAIM.
 *   5. AUTHORITATIVE RESULT. The returned dispense is pinned; a failed queue
 *      refresh keeps it and says so.
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
const DIALOG = read("components/pharmacy/pharmacy-confirm-dialog.tsx");
const QUEUE = read("components/pharmacy/pharmacy-queue.tsx");
const FILTERS = read("components/pharmacy/pharmacy-filters.tsx");
const PILLS = read("components/pharmacy/pharmacy-status-pill.tsx");
const FORMAT = read("lib/pharmacy-desk-format.ts");
const ACTIONS = read("lib/pharmacy-desk-actions.ts");
const LAYOUT = read("app/pharmacy/layout.tsx");
const PAGE = read("app/pharmacy/page.tsx");
const TYPES = read("types/pharmacy-desk.ts");

const COMPONENTS: ReadonlyArray<readonly [string, string]> = [
  ["pharmacy-workstation.tsx", WORKSTATION],
  ["pharmacy-dispense-panel.tsx", PANEL],
  ["pharmacy-confirm-dialog.tsx", DIALOG],
  ["pharmacy-queue.tsx", QUEUE],
  ["pharmacy-filters.tsx", FILTERS],
  ["pharmacy-status-pill.tsx", PILLS],
  ["pharmacy-desk-format.ts", FORMAT],
  ["pharmacy-desk-actions.ts", ACTIONS],
  ["layout.tsx", LAYOUT],
  ["page.tsx", PAGE],
];

/* 1. Two writes only */

test("only the workstation sends a POST, and only to the two action paths", () => {
  for (const [name, source] of COMPONENTS) {
    if (name === "pharmacy-workstation.tsx") continue;
    assert.doesNotMatch(code(source), /method:\s*["'](POST|PUT|PATCH|DELETE)["']/, name);
  }
  const emitted = code(WORKSTATION);
  assert.doesNotMatch(emitted, /method:\s*["'](PUT|PATCH|DELETE)["']/);
  assert.equal((emitted.match(/method:\s*"POST"/g) ?? []).length, 1);
  assert.ok(emitted.includes('kind === "prepare" ? preparePath(current.id) : validatePath(current.id)'));
});

test("every fetch goes through a BFF path helper", () => {
  const emitted = code(WORKSTATION);
  const fetches = [...emitted.matchAll(/fetch\(\s*([^,\n]+)/g)].map((match) => match[1].trim());
  assert.ok(fetches.length >= 4);
  for (const target of fetches) {
    assert.match(
      target,
      /^(PHARMACY_SESSION_PATH|worklistPath\(|dispensePath\(|kind === "prepare" \? preparePath)/,
      target,
    );
  }
});

test("one operation token per action, reused only for the same unresolved request", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("tokenFor(pendingRef.current, kind, current.id, signature"));
  assert.ok(emitted.includes("crypto.randomUUID()"));
  assert.ok(emitted.includes("pendingRef.current = null"));
  assert.ok(code(ACTIONS).includes("expected_revision: detail.workflow_revision"));
});

test("no line is ever saved directly and no other action exists", () => {
  const forbidden = [
    /Save quantit/i, /Mark ready/i, /Cancel dispense/i, /Reset to draft/i, /Return medication/i,
    /Substitut/i, /Record payment/i, /\/lines\//, /onCancelDispense|onReturn|onSubstitute/,
  ];
  for (const [name, source] of COMPONENTS) {
    for (const pattern of forbidden) {
      assert.doesNotMatch(code(source), pattern, `${name} offers ${pattern}`);
    }
  }
});

/* 2. Server-offered */

test("inputs and actions appear only on the server's per-record flags", () => {
  const panel = code(PANEL);
  assert.ok(panel.includes("const editable = detail.can_prepare && draft !== null;"));
  assert.ok(panel.includes("{detail.can_prepare ? ("));
  assert.ok(panel.includes("{detail.can_validate ? ("));
  assert.equal((panel.match(/<input/g) ?? []).length, 1, "the intended quantity is the only input");
  assert.ok(PANEL.includes("Intended cumulative"));
  assert.ok(code(WORKSTATION).includes("!detailForSelection.can_prepare"));
});

test("prescribed, delivered, consumed and remaining are never inputs", () => {
  const panel = code(PANEL);
  for (const field of ["prescribed_quantity", "delivered_quantity", "consumed_quantity", "remaining_quantity"]) {
    assert.ok(panel.includes(`<Qty value={line.${field}}`), field);
  }
});

test("the browser never derives a lane from state or quantities", () => {
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    assert.doesNotMatch(emitted, /\.state\s*===\s*["'](draft|ready|partial|dispensed)["']/, name);
    assert.doesNotMatch(emitted, /\.lane\s*=\s*["']/, name);
  }
});

/* 3. Deliberate confirmation */

test("validation is confirmed by pointer only, never by a key", () => {
  const dialog = code(DIALOG);
  assert.ok(DIALOG.includes("Go back"));
  assert.ok(DIALOG.includes("Confirm validation"));
  assert.ok(dialog.includes("onClick={pointerOnly(onConfirm)}"));
  assert.ok(dialog.includes("if (event.detail === 0) return;"));
  assert.ok(dialog.includes("backRef.current?.focus()"), "initial focus is Go back");
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    assert.doesNotMatch(emitted, /onKeyDown=\{[^}]*(submit|onConfirm|Validate)/i, name);
    assert.doesNotMatch(emitted, /key === ["']Enter["']|ctrlKey|metaKey/, name);
    assert.doesNotMatch(emitted, /<form/, `${name}: no form, so Enter submits nothing`);
  }
});

test("the validate summary shows who, what and the increment now", () => {
  const dialog = code(DIALOG);
  for (const needle of [
    "detail.patient?.name", "detail.patient?.mrn", "detail.dispense_code",
    "medicineLabel(entry.line)", "entry.intended", "entry.delivered", "entry.increment",
  ]) {
    assert.ok(dialog.includes(needle), needle);
  }
  assert.ok(DIALOG.includes("Hand over now"));
  assert.ok(DIALOG.includes("Already delivered"));
});

/* 4. No Odoo, no money, no provenance */

test("the browser never addresses Odoo", () => {
  for (const [name, source] of COMPONENTS) {
    const emitted = code(source);
    for (const marker of ["yoya-emr", "http://", "https://", "8069", "/web/", "session_id"]) {
      assert.ok(!emitted.includes(marker), `${name} must not contain ${marker}`);
    }
  }
});

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

test("nothing claims who dispensed", () => {
  for (const [name, source] of COMPONENTS) {
    assert.doesNotMatch(code(source), /dispensed by|pharmacist_id|dispensed_by/i, name);
  }
});

/* 5. Authoritative result */

test("the returned dispense is pinned and the queue reconciled", () => {
  const emitted = code(WORKSTATION);
  assert.ok(emitted.includes("setDetail(updated)"));
  assert.ok(emitted.includes("setPinnedId(updated.id)"));
  assert.ok(emitted.includes("reconcilingRef.current = true"));
  assert.ok(emitted.includes("QUEUE_REFRESH_FAILED_NOTICE"));
  assert.ok(emitted.includes("pinnedId ?? resolveSelection(visibleRows, selectedId)"));
});

test("safe states still render", () => {
  assert.ok(WORKSTATION.includes("This is not your workstation."));
  assert.ok(QUEUE.includes("No dispenses here"));
  assert.ok(PANEL.includes("Select a dispense"));
  assert.ok(PANEL.includes("Showing last loaded data"));
  assert.ok(code(PANEL).includes("reasonNotice(detail)"));
  assert.ok(code(WORKSTATION).includes("UNKNOWN_OUTCOME_NOTICE"));
});

test("lane badges read the server summary, never a local recount", () => {
  const emitted = code(FILTERS);
  assert.ok(emitted.includes("laneCountLabel(summary, key)"));
  assert.doesNotMatch(emitted, /rows\.(length|filter|reduce)/);
});

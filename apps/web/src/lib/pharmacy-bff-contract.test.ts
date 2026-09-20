/**
 * THE PHARMACY BFF: route set, upstream shape and body filtering, held at the
 * source (the technique rad-bff-contract.test.ts uses).
 *
 *   * Three GET routes and exactly two POST routes (prepare, validate).
 *   * Mutation bodies are REBUILT from the allowed fields, never passed through.
 *   * Validate forwards no quantity.
 *   * The existing Odoo session is the only credential; no host or port is named.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import test from "node:test";

import { pickPrepareBody, pickValidateBody } from "../app/api/pharmacy/_body.ts";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const UTILS = read("app/api/pharmacy/_utils.ts");
const READS = {
  session: read("app/api/pharmacy/session/route.ts"),
  worklist: read("app/api/pharmacy/worklist/route.ts"),
  detail: read("app/api/pharmacy/dispenses/[dispenseId]/route.ts"),
};
const WRITES = {
  prepare: read("app/api/pharmacy/dispenses/[dispenseId]/prepare/route.ts"),
  validate: read("app/api/pharmacy/dispenses/[dispenseId]/validate/route.ts"),
};

function listRoutes(dir: URL, prefix = ""): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir)) {
    const child = new URL(`${entry}${statSync(new URL(entry, dir)).isDirectory() ? "/" : ""}`, dir);
    if (statSync(child).isDirectory()) {
      found.push(...listRoutes(child, `${prefix}${entry}/`));
    } else if (entry === "route.ts") {
      found.push(`${prefix}${entry}`);
    }
  }
  return found;
}

test("exactly three read routes and two mutation routes exist", () => {
  const root = new URL("../app/api/pharmacy/", import.meta.url);
  assert.ok(existsSync(root));
  assert.deepEqual(listRoutes(root).sort(), [
    "dispenses/[dispenseId]/prepare/route.ts",
    "dispenses/[dispenseId]/route.ts",
    "dispenses/[dispenseId]/validate/route.ts",
    "session/route.ts",
    "worklist/route.ts",
  ]);
});

test("read routes export GET only; mutation routes export POST only", () => {
  for (const [name, source] of Object.entries(READS)) {
    const emitted = code(source);
    assert.match(emitted, /export async function GET\(/, name);
    assert.doesNotMatch(emitted, /export async function (POST|PUT|PATCH|DELETE)\(/, name);
  }
  for (const [name, source] of Object.entries(WRITES)) {
    const emitted = code(source);
    assert.match(emitted, /export async function POST\(/, name);
    assert.doesNotMatch(emitted, /export async function (GET|PUT|PATCH|DELETE)\(/, name);
  }
});

test("mutation bodies are rebuilt, never passed through", () => {
  assert.ok(code(WRITES.prepare).includes("pickPrepareBody(body.body)"));
  assert.ok(code(WRITES.validate).includes("pickValidateBody(body.body)"));
  for (const source of Object.values(WRITES)) {
    assert.doesNotMatch(code(source), /,\s*body\.body\s*,?\s*\)/, "raw body forwarded");
  }
});

test("pickPrepareBody keeps exactly the Prepare fields", () => {
  const picked = pickPrepareBody({
    operation_token: "t",
    expected_revision: 3,
    lines: [{ line_id: 1, intended_quantity: 4, unit_price: 99, state: "dispensed" }],
    state: "dispensed",
    amount: 500,
  });
  assert.deepEqual(picked, {
    operation_token: "t",
    expected_revision: 3,
    lines: [{ line_id: 1, intended_quantity: 4 }],
  });
});

test("pickValidateBody forwards no quantity at all", () => {
  assert.deepEqual(
    pickValidateBody({ operation_token: "t", expected_revision: 3, lines: [{ line_id: 1, intended_quantity: 9 }] }),
    { operation_token: "t", expected_revision: 3 },
  );
});

test("every route uses the existing Odoo session through the bound helpers", () => {
  for (const [name, source] of Object.entries({ ...READS, ...WRITES })) {
    const emitted = code(source);
    assert.match(emitted, /from "\.\.(\/\.\.)*\/_utils"/, name);
    assert.doesNotMatch(emitted, /@\/app\/api\/(reception|doctor|radiology)/, name);
    assert.ok(emitted.includes("requireOdooSession()"), name);
    assert.ok(emitted.includes("forwardOdooResult("), name);
    assert.ok(emitted.includes("handleRouteError("), name);
  }
});

test("each route targets its own upstream path", () => {
  assert.ok(code(UTILS).includes('PHARMACY_API = "/yoya-emr/api/v1/pharmacy"'));
  assert.ok(code(READS.session).includes("`${PHARMACY_API}/session`"));
  assert.ok(code(READS.worklist).includes("`${PHARMACY_API}/worklist${url.search}`"));
  assert.ok(code(READS.detail).includes("`${PHARMACY_API}/dispenses/${parsed.value}`"));
  assert.ok(code(WRITES.prepare).includes("`${PHARMACY_API}/dispenses/${parsed.value}/prepare`"));
  assert.ok(code(WRITES.validate).includes("`${PHARMACY_API}/dispenses/${parsed.value}/validate`"));
});

test("the utils stay server-only, labelled, and name no host or port", () => {
  const emitted = code(UTILS);
  assert.ok(emitted.includes('import "server-only";'));
  assert.ok(emitted.includes('const PHARMACY_SERVICE_LABEL = "pharmacy service"'));
  assert.doesNotMatch(emitted, /streamOdooBinary|postOdooMultipart/);
  for (const source of [UTILS, ...Object.values(READS), ...Object.values(WRITES)]) {
    const text = code(source);
    for (const marker of ["http://", "https://", "8069", "localhost"]) {
      assert.ok(!text.includes(marker), marker);
    }
  }
});

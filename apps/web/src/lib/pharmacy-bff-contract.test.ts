/**
 * THE PHARMACY BFF: service identity, route set and upstream shape, held at the
 * source (the technique rad-bff-contract.test.ts uses).
 *
 *   * Exactly three GET routes exist -- no mutation route in this slice.
 *   * Each forwards to its own upstream path with the existing Odoo session.
 *   * No write handler, body reader or binary stream is reachable.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import test from "node:test";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const UTILS = read("app/api/pharmacy/_utils.ts");
const ROUTES = {
  session: read("app/api/pharmacy/session/route.ts"),
  worklist: read("app/api/pharmacy/worklist/route.ts"),
  detail: read("app/api/pharmacy/dispenses/[dispenseId]/route.ts"),
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

test("exactly the three read routes exist", () => {
  const root = new URL("../app/api/pharmacy/", import.meta.url);
  assert.ok(existsSync(root));
  assert.deepEqual(listRoutes(root).sort(), [
    "dispenses/[dispenseId]/route.ts",
    "session/route.ts",
    "worklist/route.ts",
  ]);
});

test("every route exports GET and nothing else", () => {
  for (const [name, source] of Object.entries(ROUTES)) {
    const emitted = code(source);
    assert.match(emitted, /export async function GET\(/, name);
    assert.doesNotMatch(emitted, /export async function (POST|PUT|PATCH|DELETE)\(/, name);
  }
});

test("the utils can only issue GET, with the pharmacy service label", () => {
  const emitted = code(UTILS);
  assert.ok(emitted.includes('const PHARMACY_SERVICE_LABEL = "pharmacy service"'));
  assert.ok(emitted.includes('"GET",'));
  assert.doesNotMatch(emitted, /"POST"|"PUT"|"PATCH"|"DELETE"/);
  assert.doesNotMatch(emitted, /readJsonObject|streamOdooBinary|postOdoo/);
  assert.ok(emitted.includes('import "server-only";'));
});

test("every route uses the existing Odoo session through the bound helper", () => {
  for (const [name, source] of Object.entries(ROUTES)) {
    const emitted = code(source);
    assert.match(emitted, /from "\.\.(\/\.\.)?\/_utils"/, name);
    assert.doesNotMatch(emitted, /@\/app\/api\/(reception|doctor|radiology)/, name);
    assert.ok(emitted.includes("requireOdooSession()"), name);
    assert.ok(emitted.includes("forwardOdooResult("), name);
    assert.ok(emitted.includes("handleRouteError("), name);
  }
});

test("each route targets its own upstream path", () => {
  assert.ok(code(UTILS).includes('PHARMACY_API = "/yoya-emr/api/v1/pharmacy"'));
  assert.ok(code(ROUTES.session).includes("`${PHARMACY_API}/session`"));
  assert.ok(code(ROUTES.worklist).includes("`${PHARMACY_API}/worklist${url.search}`"));
  assert.ok(code(ROUTES.detail).includes("`${PHARMACY_API}/dispenses/${parsed.value}`"));
  assert.ok(code(ROUTES.detail).includes("parseDispenseId(dispenseId)"));
});

test("each route has its own fallback code, and no host or port is named", () => {
  assert.ok(ROUTES.session.includes('"pharmacy_session_failed"'));
  assert.ok(ROUTES.worklist.includes('"pharmacy_worklist_failed"'));
  assert.ok(ROUTES.detail.includes('"pharmacy_dispense_failed"'));
  for (const source of [UTILS, ...Object.values(ROUTES)]) {
    const text = code(source);
    for (const marker of ["http://", "https://", "8069", "localhost"]) {
      assert.ok(!text.includes(marker), marker);
    }
  }
});

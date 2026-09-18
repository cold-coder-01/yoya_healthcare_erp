/**
 * THE RADIOLOGY BFF: service identity, route set and upstream shape, held at
 * the source (the technique lab-bff-contract.test.ts uses).
 *
 *   * The desk names ITS OWN service in fallback wording ("radiology service"),
 *     so a stale Odoo process never sends a user looking at reception.
 *   * Exactly three GET routes exist, each forwarding to its own upstream path.
 *   * No write handler, no body reader, no binary stream, and no Doctor-specific
 *     loader is reachable from these routes.
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

const UTILS = read("app/api/radiology/_utils.ts");
const READ_ROUTES = {
  session: read("app/api/radiology/session/route.ts"),
  worklist: read("app/api/radiology/worklist/route.ts"),
  detail: read("app/api/radiology/requests/[requestId]/route.ts"),
};
// Slice 2: the only two write routes.
const WRITE_ROUTES = {
  schedule: read("app/api/radiology/requests/[requestId]/schedule/route.ts"),
  start: read("app/api/radiology/requests/[requestId]/start/route.ts"),
};
const ROUTES = { ...READ_ROUTES, ...WRITE_ROUTES };

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

test("exactly the three read routes and the two transition routes exist", () => {
  const root = new URL("../app/api/radiology/", import.meta.url);
  assert.ok(existsSync(root));
  assert.deepEqual(listRoutes(root).sort(), [
    "requests/[requestId]/route.ts",
    "requests/[requestId]/schedule/route.ts",
    "requests/[requestId]/start/route.ts",
    "session/route.ts",
    "worklist/route.ts",
  ]);
});

test("every read route exports GET and nothing else", () => {
  for (const [name, source] of Object.entries(READ_ROUTES)) {
    const emitted = code(source);
    assert.match(emitted, /export async function GET\(/, name);
    assert.doesNotMatch(emitted, /export async function (POST|PUT|PATCH|DELETE)\(/, name);
  }
});

test("each transition route exports POST and nothing else", () => {
  for (const [name, source] of Object.entries(WRITE_ROUTES)) {
    const emitted = code(source);
    assert.match(emitted, /export async function POST\(/, name);
    assert.doesNotMatch(emitted, /export async function (GET|PUT|PATCH|DELETE)\(/, name);
  }
});

test("transition routes are bodiless: the browser's body is never read or forwarded", () => {
  for (const [name, source] of Object.entries(WRITE_ROUTES)) {
    const emitted = code(source);
    assert.ok(emitted.includes("_request: Request"), `${name} must not read the request`);
    assert.doesNotMatch(emitted, /\.json\(\)|readJsonObject|request\.body|_request\./, name);
    assert.ok(emitted.includes("postOdooApi<RadTransitionResponse>("), name);
    assert.ok(emitted.includes("parseRequestId(requestId)"), name);
  }
  const utils = code(UTILS);
  assert.match(utils, /"POST",\s*\{\},/, "the POST helper sends a fixed empty object");
});

test("the radiology desk binds its own service label", () => {
  const emitted = code(UTILS);
  assert.ok(emitted.includes('const RADIOLOGY_SERVICE_LABEL = "radiology service"'));
  assert.ok(emitted.includes("RADIOLOGY_SERVICE_LABEL,"));
  for (const [name, source] of Object.entries(ROUTES)) {
    assert.ok(!code(source).includes("reception service"), name);
  }
});

test("the bound helpers issue exactly one GET and one bodiless POST", () => {
  const emitted = code(UTILS);
  assert.equal([...emitted.matchAll(/"GET",/g)].length, 1);
  assert.equal([...emitted.matchAll(/"POST",/g)].length, 1);
  assert.doesNotMatch(emitted, /"(PUT|PATCH|DELETE)"/);
  assert.doesNotMatch(emitted, /readJsonObject|streamOdooBinary/);
  assert.ok(emitted.includes("export function postOdooApi<T>(sessionId: string, path: string)"));
});

test("every route uses the BOUND helper from the radiology utils", () => {
  for (const [name, source] of Object.entries(ROUTES)) {
    const emitted = code(source);
    assert.match(emitted, /from "(\.\.\/){1,3}_utils"/, name);
    assert.doesNotMatch(emitted, /@\/app\/api\/reception\/_utils/, name);
    assert.doesNotMatch(emitted, /@\/app\/api\/doctor/, `${name} must not reuse Doctor loaders`);
    assert.ok(emitted.includes("requireOdooSession()"), name);
    assert.ok(emitted.includes("forwardOdooResult("), name);
    assert.ok(emitted.includes("handleRouteError("), name);
  }
});

test("each route targets its own upstream path", () => {
  assert.ok(code(UTILS).includes('RADIOLOGY_API = "/yoya-emr/api/v1/radiology"'));
  assert.ok(code(ROUTES.session).includes("`${RADIOLOGY_API}/session`"));
  assert.ok(code(ROUTES.worklist).includes("`${RADIOLOGY_API}/worklist${url.search}`"));
  assert.ok(code(ROUTES.detail).includes("`${RADIOLOGY_API}/requests/${parsed.value}`"));
  assert.ok(code(ROUTES.detail).includes("parseRequestId(requestId)"));
  assert.ok(code(ROUTES.schedule).includes("`${RADIOLOGY_API}/requests/${parsed.value}/schedule`"));
  assert.ok(code(ROUTES.start).includes("`${RADIOLOGY_API}/requests/${parsed.value}/start`"));
  assert.doesNotMatch(code(ROUTES.schedule), /\/start`/);
  assert.doesNotMatch(code(ROUTES.start), /\/schedule`/);
});

test("transition routes restate no clearance, billing or workflow rule", () => {
  for (const [name, source] of Object.entries(WRITE_ROUTES)) {
    assert.doesNotMatch(code(source), /billing|amount|clearance|state ===|lane/i, name);
  }
});

test("each route has its own transport fallback code", () => {
  assert.ok(ROUTES.session.includes('"radiology_session_failed"'));
  assert.ok(ROUTES.worklist.includes('"radiology_worklist_failed"'));
  assert.ok(ROUTES.detail.includes('"radiology_request_failed"'));
  assert.ok(ROUTES.schedule.includes('"radiology_schedule_failed"'));
  assert.ok(ROUTES.start.includes('"radiology_start_failed"'));
});

test("the utils stay server-only and name no host or port", () => {
  const emitted = code(UTILS);
  assert.ok(emitted.includes('import "server-only";'));
  for (const source of [UTILS, ...Object.values(ROUTES)]) {
    const text = code(source);
    for (const marker of ["http://", "https://", "8069", "8171", "localhost"]) {
      assert.ok(!text.includes(marker), marker);
    }
  }
});

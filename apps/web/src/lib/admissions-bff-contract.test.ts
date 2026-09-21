/**
 * THE ADMISSIONS BFF: route set and upstream shape, held at the source (the
 * technique pharmacy-bff-contract.test.ts uses).
 *
 *   * Five read routes (GET) and ONE mutation route (Slice 2): POST
 *     [id]/admit, whose body is rebuilt from exactly three fields.
 *   * Every route requires the existing Odoo session and forwards through the
 *     shared reception helpers (timeout, session cookie, fixed error mapping).
 *   * No host or port is named; the browser never reaches Odoo.
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

const UTILS = read("app/api/admissions/_utils.ts");
const ROUTES = {
  session: { source: read("app/api/admissions/session/route.ts"), upstream: "/session" },
  worklist: { source: read("app/api/admissions/worklist/route.ts"), upstream: "/worklist" },
  wards: { source: read("app/api/admissions/wards/route.ts"), upstream: "/wards" },
  beds: { source: read("app/api/admissions/beds/route.ts"), upstream: "/beds" },
  detail: { source: read("app/api/admissions/[id]/route.ts"), upstream: "/${parsed.value}" },
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

const ADMIT = read("app/api/admissions/[id]/admit/route.ts");

test("five read routes and the one admit route exist, and nothing else", () => {
  const root = new URL("../app/api/admissions/", import.meta.url);
  assert.ok(existsSync(root));
  assert.deepEqual(listRoutes(root).sort(), [
    "[id]/admit/route.ts",
    "[id]/route.ts",
    "beds/route.ts",
    "session/route.ts",
    "wards/route.ts",
    "worklist/route.ts",
  ]);
});

test("every route exports GET and nothing that writes", () => {
  for (const [name, { source }] of Object.entries(ROUTES)) {
    const emitted = code(source);
    assert.match(emitted, /export async function GET\(/, name);
    assert.doesNotMatch(emitted, /export async function (POST|PUT|PATCH|DELETE)\(/, name);
  }
});

test("every route requires the Odoo session and forwards through the shared helpers", () => {
  for (const [name, { source, upstream }] of Object.entries(ROUTES)) {
    const emitted = code(source);
    assert.ok(emitted.includes("await requireOdooSession()"), name);
    assert.ok(emitted.includes("if (!session.ok)"), name);
    assert.ok(emitted.includes("forwardOdooResult("), name);
    assert.ok(emitted.includes("handleRouteError("), name);
    assert.ok(emitted.includes(`\${ADMISSIONS_API}${upstream}`), `${name} -> ${upstream}`);
  }
});

test("the utils bind GET and one POST helper, keep the session server-side and name no host", () => {
  const emitted = code(UTILS);
  assert.match(UTILS, /^import "server-only";/m);
  assert.ok(emitted.includes('callOdooApiWithLabel<T>(sessionId, path, "GET", undefined, ADMISSIONS_SERVICE_LABEL)'));
  assert.ok(emitted.includes('callOdooApiWithLabel<T>(sessionId, path, "POST", body, ADMISSIONS_SERVICE_LABEL)'));
  assert.equal((emitted.match(/"POST"/g) ?? []).length, 1);
  assert.doesNotMatch(emitted, /"PUT"|"PATCH"|"DELETE"/);
  assert.ok(emitted.includes('errorResponse("admission_invalid_payload"'));
  assert.ok(emitted.includes('export const ADMISSIONS_API = "/yoya-emr/api/v1/admissions"'));
  for (const source of [UTILS, ADMIT, ...Object.values(ROUTES).map((r) => r.source)]) {
    assert.doesNotMatch(code(source), /https?:\/\/|localhost|:8069|:8171/);
  }
});

test("the detail route validates its id before calling upstream", () => {
  const emitted = code(ROUTES.detail.source);
  assert.ok(emitted.indexOf("parseAdmissionId(id)") < emitted.indexOf("callOdooApi<"));
  assert.ok(code(UTILS).includes("Number.isInteger(admissionId) || admissionId <= 0"));
});

test("the admit route is POST only, validates the id, and forwards the rebuilt body", () => {
  const emitted = code(ADMIT);
  assert.match(emitted, /export async function POST\(/);
  assert.doesNotMatch(emitted, /export async function (GET|PUT|PATCH|DELETE)\(/);
  assert.ok(emitted.includes("await requireOdooSession()"));
  assert.ok(emitted.indexOf("parseAdmissionId(id)") < emitted.indexOf("readMutationBody(request)"));
  assert.ok(emitted.indexOf("readMutationBody(request)") < emitted.indexOf("postOdooApiWithBody<"));
  assert.ok(emitted.includes("`${ADMISSIONS_API}/${parsed.value}/admit`"));
  assert.ok(emitted.includes("pickAdmitBody(body.body)"));
  assert.doesNotMatch(emitted, /body: body\.body|\.\.\.body/);
});

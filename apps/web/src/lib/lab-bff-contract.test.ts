/**
 * THE LABORATORY BFF's SERVICE IDENTITY AND ROUTE SHAPE, HELD AT THE SOURCE.
 *
 * THE UAT DEFECT THIS EXISTS FOR. A technician pressed Start processing and the
 * Laboratory Desk answered:
 *
 *     "The reception service returned an error."
 *
 * Two separate things produced that sentence, and only the second is a code
 * defect:
 *
 *   1. A STALE ODOO PROCESS was still bound to port 8171 alongside a newer one
 *      and answered the request. It predated Slice 2b, so the router had no
 *      /start-processing route and returned an HTML 404. That is a runtime
 *      condition, not something source can prevent -- but it is exactly the
 *      condition that exposed the second problem.
 *
 *   2. THE SHARED BFF HELPERS HARDCODED "reception service" in the one message
 *      they fall back to when Odoo's body is NOT the JSON envelope -- an HTML
 *      404 from the router, or an error page. Every workstation shares those
 *      helpers, so the Laboratory Desk named the wrong service and sent the
 *      technician looking in the wrong place.
 *
 * The label is now a parameter with a reception-flavoured DEFAULT, so no other
 * desk's wording changed, and the laboratory binds its own once in its _utils.
 *
 * WHY THIS IS A SOURCE ASSERTION. The fallback only fires on a non-JSON
 * upstream body, which no unit test can produce without standing up a broken
 * Odoo. Reading the source pins the property exactly where it regresses --
 * the technique order-wizard-contract.test.ts already uses for component
 * wiring.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

/**
 * Source with comments removed.
 *
 * The property under test is what a file EMITS, not what it explains. This
 * module's own docstrings quote the offending sentence in order to document
 * the defect, and a naive substring scan would flag that as the defect itself.
 */
function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const LAB_UTILS = read("app/api/laboratory/_utils.ts");
const RECEPTION_UTILS = read("app/api/reception/_utils.ts");
const LAB_ROUTES = [
  "session/route.ts",
  "worklist/route.ts",
  "requests/[requestId]/route.ts",
  "requests/[requestId]/collect/route.ts",
  "requests/[requestId]/start-processing/route.ts",
].map((name) => [name, read(`app/api/laboratory/${name}`)] as const);

/* ------------------------------------------------------------------ *
 * 1. The Laboratory Desk never names another workstation
 * ------------------------------------------------------------------ */

test("no laboratory file hardcodes another desk's service name", () => {
  const sources: ReadonlyArray<readonly [string, string]> = [
    ["_utils.ts", LAB_UTILS],
    ...LAB_ROUTES,
  ];
  for (const [name, source] of sources) {
    const emitted = code(source);
    assert.ok(
      !emitted.includes("The reception service returned"),
      `${name} must not tell a technician about the reception service`,
    );
    assert.ok(
      !emitted.includes("The clinical service returned"),
      `${name} must not name the clinical service either`,
    );
  }
});

test("the laboratory binds its own service label", () => {
  assert.ok(LAB_UTILS.includes('const LAB_SERVICE_LABEL = "laboratory service"'));
  assert.ok(
    LAB_UTILS.includes("callOdooApiWithLabel<T>(sessionId, path, method, body, LAB_SERVICE_LABEL)"),
    "the bound helper must pass the laboratory label through",
  );
});

test("every laboratory route uses the BOUND helper, not the shared one", () => {
  // A route that imported the shared helper directly would silently fall back
  // to "reception service" again.
  for (const [name, source] of LAB_ROUTES) {
    assert.ok(
      !source.includes("@/app/api/reception/_utils"),
      `${name} must import from the laboratory _utils, not reception's`,
    );
    if (source.includes("callOdooApi")) {
      assert.ok(
        source.includes('from "../_utils"') ||
          source.includes('from "../../_utils"') ||
          source.includes('from "../../../_utils"'),
        `${name} must resolve callOdooApi through the laboratory _utils`,
      );
    }
  }
});

/* ------------------------------------------------------------------ *
 * 2. Other workstations are untouched
 * ------------------------------------------------------------------ */

test("the shared default still says reception, so no other desk changed", () => {
  assert.ok(
    RECEPTION_UTILS.includes('const DEFAULT_SERVICE_LABEL = "reception service"'),
    "changing the default would silently reword cashier, front desk and doctor",
  );
});

test("the label is a parameter with a default, not a required argument", () => {
  // Every existing caller passes four arguments; a required fifth would break
  // all of them at once.
  assert.ok(
    RECEPTION_UTILS.includes("serviceLabel: string = DEFAULT_SERVICE_LABEL"),
    "the label must default so existing callers are unaffected",
  );
});

test("both fallback messages are built from the label", () => {
  assert.ok(RECEPTION_UTILS.includes("`The ${serviceLabel} returned an error.`"));
  assert.ok(
    RECEPTION_UTILS.includes("`The ${serviceLabel} returned an invalid response.`"),
    "the invalid-response fallback names the service too",
  );
});

/* ------------------------------------------------------------------ *
 * 3. The start-processing route is structurally parallel to collect
 * ------------------------------------------------------------------ */

test("start-processing mirrors collect exactly in shape", () => {
  const collect = LAB_ROUTES.find(([n]) => n.endsWith("collect/route.ts"))![1];
  const start = LAB_ROUTES.find(([n]) =>
    n.endsWith("start-processing/route.ts"),
  )![1];

  for (const required of [
    "requireOdooSession",
    "parseRequestId",
    "callOdooApi",
    "forwardOdooResult",
    "handleRouteError",
    "export async function POST",
    "context: { params: Promise<{ requestId: string }> }",
    "await context.params",
    '"POST",',
    "{},",
  ]) {
    assert.ok(collect.includes(required), `collect must use ${required}`);
    assert.ok(start.includes(required), `start-processing must use ${required}`);
  }
});

test("each route targets its own upstream path", () => {
  const collect = LAB_ROUTES.find(([n]) => n.endsWith("collect/route.ts"))![1];
  const start = LAB_ROUTES.find(([n]) =>
    n.endsWith("start-processing/route.ts"),
  )![1];

  assert.ok(collect.includes("${LAB_API}/requests/${parsed.value}/collect"));
  assert.ok(
    start.includes("${LAB_API}/requests/${parsed.value}/start-processing"),
  );
  assert.ok(
    !start.includes("/collect"),
    "start-processing must not post to the collect endpoint",
  );
});

test("each route has its own transport fallback code", () => {
  const collect = LAB_ROUTES.find(([n]) => n.endsWith("collect/route.ts"))![1];
  const start = LAB_ROUTES.find(([n]) =>
    n.endsWith("start-processing/route.ts"),
  )![1];
  assert.ok(collect.includes("lab_collect_failed"));
  assert.ok(start.includes("lab_start_processing_failed"));
});

test("no laboratory route reaches Odoo directly or names a port", () => {
  for (const [name, source] of LAB_ROUTES) {
    assert.ok(!source.includes("ODOO_BASE_URL"), `${name} must not read the URL`);
    assert.ok(!source.includes("localhost:8"), `${name} must not name a port`);
    assert.ok(
      !source.includes("fetch("),
      `${name} must go through callOdooApi, not a raw fetch`,
    );
  }
});

test("only the two known write routes exist in the laboratory BFF", () => {
  const writers = LAB_ROUTES.filter(([, source]) =>
    source.includes("export async function POST"),
  ).map(([name]) => name);
  assert.deepEqual(writers.sort(), [
    "requests/[requestId]/collect/route.ts",
    "requests/[requestId]/start-processing/route.ts",
  ]);
  for (const [name, source] of LAB_ROUTES) {
    for (const verb of ["export async function PATCH", "export async function DELETE", "export async function PUT"]) {
      assert.ok(!source.includes(verb), `${name} must not export ${verb}`);
    }
  }
});

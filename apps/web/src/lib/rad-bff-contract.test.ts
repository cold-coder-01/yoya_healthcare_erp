/**
 * THE RADIOLOGY BFF: service identity, route set and upstream shape, held at
 * the source (the technique lab-bff-contract.test.ts uses).
 *
 *   * The desk names ITS OWN service in fallback wording ("radiology service"),
 *     so a stale Odoo process never sends a user looking at reception.
 *   * Three GET routes, three BODILESS POST routes (schedule, start, open
 *     report) and two POST routes that forward the browser's JSON object
 *     unchanged (report save and enter) -- each to its own upstream path.
 *   * No binary stream and no Doctor-specific loader is reachable, and no route
 *     restates a billing or workflow rule.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import test from "node:test";

function read(relative: string): string {
  // Line endings normalized: the working tree may be CRLF.
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8").replace(/\r\n/g, "\n");
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
// Bodiless writes: the request is identified by the URL and nothing else.
const BODILESS_ROUTES = {
  schedule: read("app/api/radiology/requests/[requestId]/schedule/route.ts"),
  start: read("app/api/radiology/requests/[requestId]/start/route.ts"),
  report: read("app/api/radiology/requests/[requestId]/report/route.ts"),
  // Slice 5.
  validate: read("app/api/radiology/results/[resultId]/validate/route.ts"),
  release: read("app/api/radiology/results/[resultId]/release/route.ts"),
};
// Slice 3: the two routes that carry report text, as a pass-through.
const BODY_ROUTES = {
  save: read("app/api/radiology/results/[resultId]/save/route.ts"),
  enter: read("app/api/radiology/results/[resultId]/enter/route.ts"),
};
// Slice 4: images.
const UPLOAD = read("app/api/radiology/results/[resultId]/images/route.ts");
const IMAGE_BYTES = read("app/api/radiology/results/[resultId]/images/[imageId]/route.ts");
const IMAGE_REMOVE = read("app/api/radiology/results/[resultId]/images/[imageId]/remove/route.ts");
const WRITE_ROUTES = { ...BODILESS_ROUTES, ...BODY_ROUTES };
const ROUTES = {
  ...READ_ROUTES,
  ...WRITE_ROUTES,
  upload: UPLOAD,
  imageBytes: IMAGE_BYTES,
  imageRemove: IMAGE_REMOVE,
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

test("exactly the read, transition and report routes exist", () => {
  const root = new URL("../app/api/radiology/", import.meta.url);
  assert.ok(existsSync(root));
  assert.deepEqual(listRoutes(root).sort(), [
    "requests/[requestId]/report/route.ts",
    "requests/[requestId]/route.ts",
    "requests/[requestId]/schedule/route.ts",
    "requests/[requestId]/start/route.ts",
    "results/[resultId]/enter/route.ts",
    "results/[resultId]/images/[imageId]/remove/route.ts",
    "results/[resultId]/images/[imageId]/route.ts",
    "results/[resultId]/images/route.ts",
    "results/[resultId]/release/route.ts",
    "results/[resultId]/save/route.ts",
    "results/[resultId]/validate/route.ts",
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

test("each write route exports POST and nothing else", () => {
  for (const [name, source] of Object.entries(WRITE_ROUTES)) {
    const emitted = code(source);
    assert.match(emitted, /export async function POST\(/, name);
    assert.doesNotMatch(emitted, /export async function (GET|PUT|PATCH|DELETE)\(/, name);
  }
});

test("bodiless routes never read or forward the browser's body", () => {
  for (const [name, source] of Object.entries(BODILESS_ROUTES)) {
    const emitted = code(source);
    assert.ok(emitted.includes("_request: Request"), `${name} must not read the request`);
    assert.doesNotMatch(emitted, /\.json\(\)|readJsonObject|request\.body|_request\.|postOdooApiWithBody/, name);
    assert.match(emitted, /postOdooApi<Rad(Transition|Report|Signoff)Response>\(/, name);
    assert.match(emitted, /parse(Request|Result)Id\((request|result)Id\)/, name);
  }
  const utils = code(UTILS);
  assert.match(utils, /"POST",\s*\{\},/, "the bodiless POST helper sends a fixed empty object");
});

test("the report text routes forward the JSON object unchanged, and filter nothing", () => {
  for (const [name, source] of Object.entries(BODY_ROUTES)) {
    const emitted = code(source);
    assert.ok(emitted.includes("const body = await readJsonObject(request);"), name);
    assert.ok(emitted.includes("if (!body.ok) {"), name);
    assert.ok(emitted.includes("postOdooApiWithBody<RadReportResponse>("), name);
    assert.ok(emitted.includes("body.body,"), name);
    assert.ok(emitted.includes("parseResultId(resultId)"), name);
    // A pass-through: no second allow-list to drift from the API's.
    assert.doesNotMatch(emitted, /findings|impression|recommendations|result_summary|delete |Object\.keys|\.filter\(/, name);
  }
});

test("the radiology desk binds its own service label", () => {
  const emitted = code(UTILS);
  assert.ok(emitted.includes('const RADIOLOGY_SERVICE_LABEL = "radiology service"'));
  assert.equal([...emitted.matchAll(/RADIOLOGY_SERVICE_LABEL,/g)].length, 3);
  assert.ok(emitted.includes("postOdooMultipartWithLabel<T>(sessionId, path, form, RADIOLOGY_SERVICE_LABEL)"));
  for (const [name, source] of Object.entries(ROUTES)) {
    assert.ok(!code(source).includes("reception service"), name);
  }
});

test("the bound helpers issue one GET, one bodiless POST and one body POST", () => {
  const emitted = code(UTILS);
  assert.equal([...emitted.matchAll(/"GET",/g)].length, 1);
  assert.equal([...emitted.matchAll(/"POST",/g)].length, 2);
  assert.doesNotMatch(emitted, /"(PUT|PATCH|DELETE)"/);
  // Slice 4: streamOdooBinary is re-exported for the one image byte route.
  assert.ok(emitted.includes("export function postOdooMultipart<T>(sessionId: string, path: string, form: FormData)"));
  assert.ok(emitted.includes("export function postOdooApi<T>(sessionId: string, path: string)"));
  assert.ok(emitted.includes("export function postOdooApiWithBody<T>("));
  assert.ok(emitted.includes("export function parseResultId(raw: string)"));
});

test("every route uses the BOUND helper from the radiology utils", () => {
  for (const [name, source] of Object.entries(ROUTES)) {
    const emitted = code(source);
    assert.match(emitted, /from "(\.\.\/){1,5}_utils"/, name);
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
  assert.ok(code(ROUTES.report).includes("`${RADIOLOGY_API}/requests/${parsed.value}/report`"));
  assert.ok(code(ROUTES.save).includes("`${RADIOLOGY_API}/results/${parsed.value}/save`"));
  assert.ok(code(ROUTES.enter).includes("`${RADIOLOGY_API}/results/${parsed.value}/enter`"));
  assert.doesNotMatch(code(ROUTES.schedule), /\/start`/);
  assert.doesNotMatch(code(ROUTES.start), /\/schedule`/);
  assert.doesNotMatch(code(ROUTES.save), /\/enter`/);
  assert.doesNotMatch(code(ROUTES.enter), /\/save`/);
});

test("write routes restate no clearance, billing or workflow rule", () => {
  for (const [name, source] of Object.entries(WRITE_ROUTES)) {
    assert.doesNotMatch(code(source), /billing|amount|clearance|state ===|lane|radiologist/i, name);
  }
});

test("no cancel, reset, amendment or retraction route exists under radiology", () => {
  const root = new URL("../app/api/radiology/", import.meta.url);
  for (const route of listRoutes(root)) {
    assert.doesNotMatch(route, /cancel|reset|amend|retract/, route);
  }
});

test("each route has its own transport fallback code", () => {
  assert.ok(ROUTES.session.includes('"radiology_session_failed"'));
  assert.ok(ROUTES.worklist.includes('"radiology_worklist_failed"'));
  assert.ok(ROUTES.detail.includes('"radiology_request_failed"'));
  assert.ok(ROUTES.schedule.includes('"radiology_schedule_failed"'));
  assert.ok(ROUTES.start.includes('"radiology_start_failed"'));
  assert.ok(ROUTES.report.includes('"radiology_report_open_failed"'));
  assert.ok(ROUTES.save.includes('"radiology_report_save_failed"'));
  assert.ok(ROUTES.enter.includes('"radiology_report_enter_failed"'));
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

/* ------------------------------------------------------------------ *
 * Images (Slice 4)
 * ------------------------------------------------------------------ */

test("the upload route rebuilds the form from one file and a caption", () => {
  const emitted = code(UPLOAD);
  assert.match(emitted, /export async function POST\(/);
  assert.doesNotMatch(emitted, /export async function (GET|PUT|PATCH|DELETE)\(/);
  assert.ok(emitted.includes("incoming = await request.formData();"));
  assert.ok(emitted.includes('const ALLOWED_FIELDS = new Set(["file", "caption"]);'));
  assert.ok(emitted.includes('"radiology_image_field_not_allowed"'));
  assert.ok(emitted.includes("if (files.length !== 1 || !(file instanceof File)) {"));
  assert.ok(emitted.includes("if (file.size > RADIOLOGY_IMAGE_MAX_BYTES) {"));
  // A NEW form; the browser's Content-Type for the file is replaced.
  assert.ok(emitted.includes("const form = new FormData();"));
  assert.ok(emitted.includes('new Blob([await file.arrayBuffer()], { type: "application/octet-stream" }),'));
  assert.equal([...emitted.matchAll(/form\.append\(/g)].length, 2);
  assert.ok(emitted.includes("postOdooMultipart<RadImageResponse>("));
  assert.ok(emitted.includes("`${RADIOLOGY_API}/results/${parsed.value}/images`"));
  assert.ok(UPLOAD.includes('"radiology_image_upload_failed"'));
  assert.doesNotMatch(emitted, /incoming\.forEach|for \(const \[key/);
});

test("the byte route streams privately through the header allow-list", () => {
  const emitted = code(IMAGE_BYTES);
  assert.match(emitted, /export async function GET\(/);
  assert.doesNotMatch(emitted, /export async function (POST|PUT|PATCH|DELETE)\(/);
  assert.ok(emitted.includes("streamOdooBinary("));
  assert.ok(emitted.includes("`${RADIOLOGY_API}/results/${result.value}/images/${image.value}${disposition}`"));
  assert.ok(emitted.includes('const disposition = requested === "attachment" ? "?disposition=attachment" : "";'));
  assert.ok(emitted.includes('response.headers.set("Cache-Control", "private, no-store");'));
  assert.ok(emitted.includes('response.headers.set("X-Content-Type-Options", "nosniff");'));
  assert.ok(emitted.includes("parseImageId(imageId)"));
  assert.doesNotMatch(emitted, /\/doctor|DOCTOR_API|access_token|\/web\/content/);
  assert.ok(IMAGE_BYTES.includes('"radiology_image_load_failed"'));
});

test("the remove route is bodiless and targets its own upstream path", () => {
  const emitted = code(IMAGE_REMOVE);
  assert.ok(emitted.includes("_request: Request"));
  assert.doesNotMatch(emitted, /\.json\(\)|formData|readJsonObject|_request\./);
  assert.ok(emitted.includes("postOdooApi<RadImageResponse>("));
  assert.ok(emitted.includes("`${RADIOLOGY_API}/results/${result.value}/images/${image.value}/remove`"));
  assert.ok(IMAGE_REMOVE.includes('"radiology_image_remove_failed"'));
});

test("the one multipart helper sends the cookie server-side only", () => {
  const reception = code(read("app/api/reception/_utils.ts"));
  const start = reception.indexOf("export async function postOdooMultipart<T>(");
  const helper = reception.slice(start, reception.indexOf("\n}\n", start));
  assert.ok(helper.includes("headers: { Cookie: `session_id=${sessionId}` },"));
  assert.ok(helper.includes("body: form,"));
  assert.doesNotMatch(helper, /Content-Type|set-cookie|Set-Cookie/);
});

/* ------------------------------------------------------------------ *
 * Validation and release (Slice 5)
 * ------------------------------------------------------------------ */

test("validate and release are bodiless and target their own upstream paths", () => {
  const validate = code(BODILESS_ROUTES.validate);
  const release = code(BODILESS_ROUTES.release);
  assert.ok(validate.includes("`${RADIOLOGY_API}/results/${parsed.value}/validate`"));
  assert.ok(release.includes("`${RADIOLOGY_API}/results/${parsed.value}/release`"));
  assert.doesNotMatch(validate, /\/release`/);
  assert.doesNotMatch(release, /\/validate`/);
  assert.ok(BODILESS_ROUTES.validate.includes('"radiology_report_validate_failed"'));
  assert.ok(BODILESS_ROUTES.release.includes('"radiology_report_release_failed"'));
  for (const source of [validate, release]) {
    assert.ok(source.includes("postOdooApi<RadSignoffResponse>("));
    assert.doesNotMatch(source, /billing|amount|clearance|lane|state ===/i);
  }
});

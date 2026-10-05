/**
 * Consultation note: an HTTP error is not a connectivity failure (UAT
 * CONS00036).
 *
 * The live defect: the running Next dev server answered
 * POST /api/doctor/visits/:id/consultation/save with an HTML 404 page (its
 * route table had lost the nested routes). `response.json()` threw on the
 * HTML, and the catch-all told the doctor "Unable to reach the consultation
 * service." -- hiding a 404 behind a network message. These tests pin:
 *
 *   * a non-JSON answer becomes an error envelope carrying its HTTP status
 *   * the consultation screens use the connectivity message only when fetch
 *     itself fails
 *   * the save route exists, requires the Odoo session and forwards the body
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import { codeFromPayload, messageFromPayload, readJsonEnvelope } from "./api-error.ts";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

test("an HTML 404 is reported as HTTP 404, not as unreachable", async () => {
  const html = new Response("<!DOCTYPE html><html><body>404</body></html>", {
    status: 404,
    headers: { "content-type": "text/html" },
  });
  const payload = await readJsonEnvelope(html, "The consultation service");
  assert.equal(codeFromPayload(payload), "unexpected_response");
  const message = messageFromPayload(payload, "fallback");
  assert.match(message, /HTTP 404/);
  assert.match(message, /Nothing was saved/);
  assert.doesNotMatch(message, /unable to reach/i);
});

test("a JSON error keeps the server's own code and sentence", async () => {
  const conflict = new Response(
    JSON.stringify({ success: false, error: { code: "consultation_conflict", message: "Reload." } }),
    { status: 409, headers: { "content-type": "application/json" } },
  );
  const payload = await readJsonEnvelope(conflict, "The consultation service");
  assert.equal(codeFromPayload(payload), "consultation_conflict");
  assert.equal(messageFromPayload(payload, "x"), "Reload.");

  const ok = new Response(JSON.stringify({ success: true, data: { a: 1 } }), { status: 200 });
  assert.deepEqual(await readJsonEnvelope(ok, "x"), { success: true, data: { a: 1 } });
});

test("the consultation screens say 'unreachable' only for a rejected fetch", () => {
  const workspace = code(read("components/doctor/consultation/consultation-workspace.tsx"));
  const panel = code(read("components/doctor/doctor-patient-panel.tsx"));
  for (const source of [workspace, panel]) {
    assert.doesNotMatch(source, /await response\.json\(\)/);
    assert.match(source, /readJsonEnvelope\(/);
  }
  // Every connectivity message in the workspace is behind "was the server reached?".
  const unreachable = workspace.match(/"Unable to reach the consultation service\."/g) ?? [];
  const guarded = workspace.match(/reached \? "[^"]+" : "Unable to reach the consultation service\."/g) ?? [];
  assert.equal(unreachable.length, 3);
  assert.equal(guarded.length, 3);
});

test("the save route exists, requires the session and forwards the body to Odoo", () => {
  const path = "app/api/doctor/visits/[appointmentId]/consultation/save/route.ts";
  assert.ok(existsSync(new URL(`../${path}`, import.meta.url)));
  const route = code(read(path));
  assert.match(route, /export async function POST/);
  assert.match(route, /requireOdooSession\(\)/);
  assert.match(route, /session\.sessionId/);
  assert.match(route, /\$\{DOCTOR_API\}\/visits\/\$\{parsed\.value\}\/consultation\/save/);
  assert.match(route, /"POST",\s*body\.body/);
  // The completion route, the other nested one the dev server had lost.
  assert.ok(existsSync(new URL("../app/api/doctor/visits/[appointmentId]/consultation/complete/route.ts", import.meta.url)));
});

/**
 * ADMISSIONS SLICE 2: Admit to bed (Admissions Desk) and Request admission
 * (Doctor Desk).
 *
 *   * The pure helpers are exercised directly: paths, bodies, the reason
 *     rule, the idempotent-retry token and the reload rules.
 *   * The body pickers forward EXACTLY the allowed fields.
 *   * The components are held at the source (no DOM test stack exists):
 *     two-step confirmation, pointer-only irreversible click, a kept token on
 *     an unknown outcome, the doctor never choosing a bed, no transfer or
 *     discharge control anywhere.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import { pickAdmissionRequestBody, pickAdmitBody } from "../app/api/admissions/_body.ts";
import {
  ADMISSION_REASON_MAX,
  UNKNOWN_OUTCOME_NOTICE,
  admissionRequestBody,
  admissionRequestPath,
  admitBody,
  admitPath,
  admitSignature,
  cleanReason,
  isResolved,
  needsBedRefresh,
  needsReload,
  requestSignature,
  tokenFor,
} from "./admissions-desk-actions.ts";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const WORKSTATION = code(read("components/admissions/admissions-workstation.tsx"));
const DIALOG = code(read("components/admissions/admit-dialog.tsx"));
const CARD = code(read("components/doctor/doctor-admission-card.tsx"));
const PATIENT_PANEL = code(read("components/doctor/doctor-patient-panel.tsx"));
const CONSULTATION = code(read("components/doctor/consultation/consultation-workspace.tsx"));
const REQUEST_ROUTE = code(read("app/api/doctor/visits/[appointmentId]/admission-request/route.ts"));
const DESK_TYPES = read("types/admissions-desk.ts");

/* ------------------------------------------------------------------ *
 * Pure helpers
 * ------------------------------------------------------------------ */

test("paths are BFF paths only", () => {
  assert.equal(admitPath(12), "/api/admissions/12/admit");
  assert.equal(admissionRequestPath(7), "/api/doctor/visits/7/admission-request");
});

test("bodies carry exactly the fields the server accepts", () => {
  assert.deepEqual(admitBody(3, 44, "t"), { operation_token: "t", expected_revision: 3, bed_id: 44 });
  assert.deepEqual(Object.keys(admissionRequestBody("r", "t")).sort(), ["operation_token", "reason"]);
});

test("a reason is trimmed, required and bounded", () => {
  assert.equal(cleanReason("  sepsis  "), "sepsis");
  assert.equal(cleanReason("   "), null);
  assert.equal(cleanReason("x".repeat(ADMISSION_REASON_MAX)), "x".repeat(ADMISSION_REASON_MAX));
  assert.equal(cleanReason("x".repeat(ADMISSION_REASON_MAX + 1)), null);
});

test("a retry of the SAME unresolved request reuses its token; anything else mints", () => {
  let minted = 0;
  const mint = () => `new-${++minted}`;
  const pending = { kind: "admit" as const, targetId: 5, signature: admitSignature(1, 9), token: "kept" };
  assert.equal(tokenFor(pending, "admit", 5, admitSignature(1, 9), mint), "kept");
  assert.equal(tokenFor(pending, "admit", 5, admitSignature(1, 10), mint), "new-1", "different bed");
  assert.equal(tokenFor(pending, "admit", 5, admitSignature(2, 9), mint), "new-2", "different revision");
  assert.equal(tokenFor(pending, "admit", 6, admitSignature(1, 9), mint), "new-3", "different admission");
  assert.equal(tokenFor(pending, "request", 5, admitSignature(1, 9), mint), "new-4", "different kind");
  assert.equal(tokenFor(null, "admit", 5, admitSignature(1, 9), mint), "new-5");
  const request = { kind: "request" as const, targetId: 5, signature: requestSignature(" sepsis "), token: "r" };
  assert.equal(tokenFor(request, "request", 5, requestSignature("sepsis"), mint), "r", "whitespace is not a new request");
});

test("only a received AND parsed answer resolves the outcome", () => {
  assert.equal(isResolved(null, false), false);
  assert.equal(isResolved(502, false), false);
  assert.equal(isResolved(409, true), true);
  assert.equal(isResolved(200, true), true);
  assert.match(UNKNOWN_OUTCOME_NOTICE, /will not be applied twice/);
});

test("a revision conflict reloads the admission; a taken bed reloads the beds", () => {
  for (const code of ["admission_revision_conflict", "admission_invalid_state", "admission_not_found"]) {
    assert.equal(needsReload(code), true, code);
    assert.equal(needsBedRefresh(code), false, code);
  }
  for (const code of ["admission_bed_conflict", "admission_bed_unavailable"]) {
    assert.equal(needsBedRefresh(code), true, code);
    assert.equal(needsReload(code), false, code);
  }
  assert.equal(needsReload(null), false);
  assert.equal(needsBedRefresh("admission_operation_conflict"), false);
});

/* ------------------------------------------------------------------ *
 * BFF body pickers
 * ------------------------------------------------------------------ */

test("the BFF forwards exactly the admit fields and drops everything else", () => {
  const forwarded = pickAdmitBody({
    operation_token: "t",
    expected_revision: 1,
    bed_id: 4,
    ward_id: 9,
    state: "admitted",
    patient_id: 2,
  });
  assert.deepEqual(forwarded, { operation_token: "t", expected_revision: 1, bed_id: 4 });
});

test("the BFF forwards exactly the request fields: the doctor names no bed", () => {
  const forwarded = pickAdmissionRequestBody({ operation_token: "t", reason: "r", bed_id: 4, ward_id: 1 });
  assert.deepEqual(forwarded, { operation_token: "t", reason: "r" });
});

test("the doctor request route validates, rebuilds and forwards, POST only", () => {
  assert.match(REQUEST_ROUTE, /export async function POST\(/);
  assert.doesNotMatch(REQUEST_ROUTE, /export async function (GET|PUT|PATCH|DELETE)\(/);
  assert.ok(REQUEST_ROUTE.indexOf("parseAppointmentId(appointmentId)") < REQUEST_ROUTE.indexOf("readJsonObject(request)"));
  assert.ok(REQUEST_ROUTE.includes("pickAdmissionRequestBody(body.body)"));
  assert.ok(REQUEST_ROUTE.includes("`${DOCTOR_API}/visits/${parsed.value}/admission-request`"));
  assert.doesNotMatch(REQUEST_ROUTE, /https?:\/\/|localhost|:8069/);
});

/* ------------------------------------------------------------------ *
 * Admissions Desk: Admit to bed
 * ------------------------------------------------------------------ */

test("the admit dialog offers only the server's AVAILABLE, active, unflagged beds", () => {
  assert.ok(DIALOG.includes('bed.active && bed.state === "available" && !bed.needs_review'));
  assert.ok(WORKSTATION.includes('bedsPath({ wardId: null, state: "available" })'));
});

test("admitting is two steps and the irreversible click is pointer-only", () => {
  assert.ok(DIALOG.includes('useState<"choose" | "confirm">("choose")'));
  assert.ok(DIALOG.includes('onClick={() => setStep("confirm")}'));
  assert.ok(DIALOG.includes("if (event.detail === 0 || !selected) return;"));
  assert.ok(DIALOG.includes("onConfirm(selected.id)"));
  assert.ok(DIALOG.includes("backRef.current?.focus()"));
  assert.doesNotMatch(DIALOG, /onKey(Down|Up|Press)/);
  // The confirmation names what is about to happen.
  for (const label of ["Patient", "Admission", "Ward", "Room", "Bed", "Revision"]) {
    assert.ok(DIALOG.includes(`>${label}</dt>`), label);
  }
});

test("the workstation sends the loaded revision with a kept-on-unknown token", () => {
  assert.ok(WORKSTATION.includes("admitSignature(current.workflow_revision, bedId)"));
  assert.ok(WORKSTATION.includes('tokenFor(pendingRef.current, "admit", current.id, signature, () => crypto.randomUUID())'));
  assert.ok(WORKSTATION.includes("admitBody(current.workflow_revision, bedId, token)"));
  const unknown = WORKSTATION.indexOf("if (!isResolved(status, payload !== null))");
  const cleared = WORKSTATION.indexOf("pendingRef.current = null;");
  assert.ok(unknown > 0 && cleared > unknown, "the token is cleared only once the outcome is known");
  assert.ok(WORKSTATION.slice(unknown, cleared).includes("UNKNOWN_OUTCOME_NOTICE"));
});

test("success pins the server's admission and refreshes; refusals reload what went stale", () => {
  assert.ok(WORKSTATION.includes("const updated = payload.data.admission;"));
  assert.ok(WORKSTATION.includes("setDetail(updated);"));
  assert.ok(WORKSTATION.includes("setPinnedId(updated.id);"));
  assert.ok(WORKSTATION.includes("payload.data.operation.replayed"));
  assert.ok(WORKSTATION.includes("if (needsBedRefresh(code)) {"));
  assert.ok(WORKSTATION.includes("void loadAvailableBeds();"));
  assert.ok(WORKSTATION.includes("if (needsReload(code)) {"));
  assert.ok((WORKSTATION.match(/setRefreshToken\(\(value\) => value \+ 1\)/g) ?? []).length >= 2);
});

test("the typed capability and row carry the admit gate and the revision", () => {
  assert.match(DESK_TYPES, /admit: boolean;/);
  assert.match(DESK_TYPES, /assign_bed: boolean;/);
  assert.match(DESK_TYPES, /can_admit: boolean;/);
  assert.match(DESK_TYPES, /workflow_revision: number;/);
});

/* ------------------------------------------------------------------ *
 * Doctor Desk: Request admission
 * ------------------------------------------------------------------ */

test("the doctor card has two writes -- request and cancel own request -- and never picks a bed", () => {
  const writes = CARD.match(/method:\s*["'](POST|PUT|PATCH|DELETE)["']/g) ?? [];
  assert.deepEqual(writes, ['method: "POST"', 'method: "POST"']);
  assert.ok(CARD.includes("fetch(admissionRequestPath(appointmentId)"));
  assert.ok(CARD.includes("fetch(cancelRequestPath(admission.id)"));
  assert.ok(CARD.includes("admissionRequestBody(cleaned, token)"));
  assert.doesNotMatch(CARD, /bed_id|bedsPath|ward_id|role="radio"/);
});

test("the request is offered only when the server says can_request", () => {
  assert.ok(CARD.includes('summary.can_request && step === "idle"'));
  assert.ok(CARD.includes("summary.request_blocked_message"));
  assert.ok(CARD.includes("{summary.status_label}"));
});

test("requesting is two steps, pointer-only, with a kept-on-unknown token", () => {
  assert.ok(CARD.includes('onClick={() => setStep("confirm")}'));
  assert.ok(CARD.includes("disabled={!cleaned}"));
  assert.ok(CARD.includes("if (event.detail === 0) return;"));
  assert.ok(CARD.includes('tokenFor(pendingRef.current, "request", appointmentId, signature, () => crypto.randomUUID())'));
  const unknown = CARD.indexOf("if (!isResolved(status, payload !== null))");
  const cleared = CARD.indexOf("pendingRef.current = null;");
  assert.ok(unknown > 0 && cleared > unknown);
});

test("the card shows the server's result at once and is mounted on both doctor views", () => {
  assert.ok(CARD.includes("setReturned({ basis: initial, value: payload.data.admission })"));
  assert.ok(PATIENT_PANEL.includes("<DoctorAdmissionCard"));
  assert.ok(PATIENT_PANEL.includes("summary={detail.admission}"));
  assert.ok(CONSULTATION.includes("<DoctorAdmissionCard key={appointmentId} appointmentId={appointmentId} summary={detail.admission} compact />"));
});

/* ------------------------------------------------------------------ *
 * Scope: no transfer, no discharge, no billing
 * ------------------------------------------------------------------ */

test("no admissions surface offers discharge, and none names money", () => {
  const forbidden = /\b(Discharge|Cancel admission)\b/;
  const money = /\b(amount|price|tariff|daily_rate|invoice|ETB|Birr)\b/i;
  for (const [name, source] of [
    ["admit-dialog", DIALOG],
    ["doctor-admission-card", CARD],
    ["admissions-workstation", WORKSTATION],
  ] as const) {
    for (const match of source.matchAll(/<button[\s\S]*?<\/button>/g)) {
      assert.doesNotMatch(match[0], forbidden, `${name}: ${match[0].slice(0, 80)}`);
    }
    assert.doesNotMatch(source, money, name);
  }
  // Slice 3 adds transfer and cancel-request; discharge is Slice 4.
  assert.equal(existsSync(new URL("../app/api/admissions/[id]/transfer", import.meta.url)), true);
  assert.equal(existsSync(new URL("../app/api/admissions/[id]/cancel-request", import.meta.url)), true);
  assert.equal(existsSync(new URL("../app/api/admissions/[id]/discharge", import.meta.url)), false);
});

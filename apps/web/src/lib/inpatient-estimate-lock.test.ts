/**
 * INPATIENT ESTIMATE: a running forecast through the stay, LOCKED only once
 * the doctor's Request discharge has recorded medical readiness (UAT: Tesema,
 * ADM00004). The Doctor Desk is told THAT more advance is due at the Cashier,
 * never how much.
 *
 * Run with `npm test`. The wording is exercised directly; the component and
 * its wiring are held at the source (no DOM test stack exists).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  ADDITIONAL_ADVANCE_NOTICE,
  ESTIMATE_LOCK_LABELS,
  estimateLockLabel,
} from "./inpatient-estimate-lock.ts";

function code(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const ESTIMATE = code("components/doctor/doctor-inpatient-estimate.tsx");
const CARD = code("components/doctor/doctor-admission-card.tsx");
const REVIEW = code("components/doctor/doctor-discharge-review-dialog.tsx");

test("only medical readiness locks the estimate, and the wording says why", () => {
  assert.deepEqual(Object.keys(ESTIMATE_LOCK_LABELS), ["medically_ready"]);
  assert.equal(
    estimateLockLabel("medically_ready"),
    "Medical discharge has begun. Final settlement now uses actual delivered care.",
  );
  assert.equal(estimateLockLabel(null), null);
  assert.equal(estimateLockLabel(undefined), null);
});

test("G: locked -> no Revise / Give estimate and no editor", () => {
  assert.ok(ESTIMATE.includes("const locked = estimateLockLabel(data.locked_reason);"));
  assert.ok(ESTIMATE.includes("{data.can_edit && !locked && !editing ? ("));
  assert.ok(ESTIMATE.includes("{editing && !locked ? ("));
});

test("H: locked -> a read-only 'Estimate locked' state, the estimate still shown", () => {
  assert.ok(ESTIMATE.includes("Estimate locked"));
  assert.ok(ESTIMATE.includes("{locked ? <p"));
  // Amount, reason, revision, doctor and time render whether or not locked.
  const shown = ESTIMATE.slice(ESTIMATE.indexOf("{estimate.amount > 0 ? ("));
  for (const part of ["money(estimate.amount)", "estimate.reason", "Revision {estimate.revision}", "estimate.estimated_by", "estimate.estimated_at"]) {
    assert.ok(shown.includes(part), part);
  }
});

test("a stale screen refused with the lock reloads into the locked view", () => {
  const refusal = ESTIMATE.slice(ESTIMATE.indexOf('if (code === "admission_estimate_locked") {'));
  assert.ok(refusal.includes("setEditing(false);"));
  assert.ok(refusal.includes("setReloadKey((key) => key + 1);"));
  assert.ok(ESTIMATE.includes("}, [appointmentId, reloadKey]);"));
});

test("additional advance: the Doctor Desk says THAT, never how much", () => {
  assert.equal(ADDITIONAL_ADVANCE_NOTICE, "Additional advance required at the Cashier.");
  assert.ok(ESTIMATE.includes("{!locked && data.additional_advance_required ? ("));
  assert.ok(ESTIMATE.includes("{ADDITIONAL_ADVANCE_NOTICE}"));
  // The Doctor no-money contract: no Cashier figure is read here.
  for (const forbidden of [
    "advance_received", "remaining_due", "refundable", ".outstanding", "advance.received",
    ".settlement", "settlement_", "/api/cashier",
  ]) {
    assert.ok(!ESTIMATE.includes(forbidden), forbidden);
  }
});

test("revision history: earlier revisions listed, the current one is the headline", () => {
  assert.ok(ESTIMATE.includes("(data.history ?? []).filter((row) => row.revision !== estimate.revision)"));
  assert.ok(ESTIMATE.includes("Earlier revisions ({earlier.length})"));
  for (const part of ["row.revision", "money(row.amount)", "row.estimated_by", "row.estimated_at", "row.reason"]) {
    assert.ok(ESTIMATE.includes(part), part);
  }
});

test("lock boundary: opening Review discharge locks nothing; success re-reads the estimate", () => {
  // The review is a screen: no estimate call, no mutation of its own.
  assert.doesNotMatch(REVIEW, /admission-estimate|fetch\(/);
  // Going back returns to the summary editor; the estimate section stays mounted.
  assert.ok(CARD.includes('const closeDischargeReview = useCallback(() => setStep("discharge_write"), []);'));
  // The estimate re-fetches when the status changes, so after Request
  // discharge succeeds (discharge_pending) it shows the server's lock.
  assert.ok(CARD.includes('key={`${admission?.id ?? "none"}:${summary.status}`}'));
  assert.match(CARD, /summary\.status === "discharge_pending"\) \? \(\s*<DoctorInpatientEstimate/);
});

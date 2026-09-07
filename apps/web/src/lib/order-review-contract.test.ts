/**
 * What the Laboratory and Radiology order-review cards must still show -- and
 * must never show.
 *
 * WHY THIS IS A SOURCE ASSERTION. A readability pass edits class names all over
 * these two panels, and the risk it carries is not a crash but a QUIET LOSS:
 * a field dropped while re-weighting a line, or a billing value pulled in while
 * "making the card more informative". Neither would fail a typecheck, and this
 * project ships no DOM test stack to render the cards in.
 *
 * Reading the source is crude, but it pins both properties exactly where they
 * can regress, and it is the same technique the Odoo suite uses to hold the
 * billing boundary (test_doctor_laboratory_api asserts the controller never
 * opens a charge model). A test that fails when a panel stops rendering the
 * clinical indication is worth more than no test at all.
 *
 * THE CONFIDENTIALITY PROPERTY IS THE IMPORTANT ONE. The Doctor payload carries
 * a boolean clearance verdict and nothing priced; these panels must not
 * reintroduce money by reaching for a field the serializer never sends.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function panel(name: string): string {
  return readFileSync(
    new URL(`../components/doctor/consultation/${name}`, import.meta.url),
    "utf8",
  );
}

const LABORATORY = panel("laboratory-panel.tsx");
const RADIOLOGY = panel("radiology-panel.tsx");

/*
  Money and payer vocabulary. Matched case-insensitively against the whole
  panel, so a prose mention counts too -- these files have no reason to discuss
  a tariff even in a comment, and a comment about one is usually the first sign
  someone is about to render it.

  'charge' is deliberately absent: both panels legitimately say "no charge is
  created here" when explaining the boundary they keep.
*/
const FORBIDDEN = [
  "amount",
  "balance",
  "outstanding",
  "receipt",
  "payer",
  "sponsor",
  "tariff",
  "invoice",
  "credit_limit",
  "default_price",
  "billing_service",
  "amount_due",
  "prepayment",
];

/* ------------------------------------------------------------------ *
 * Everything the review card carries is still rendered
 * ------------------------------------------------------------------ */

test("the laboratory order card still renders every clinical field", () => {
  for (const field of [
    "request_code", // the reference
    "status_label", // the server's own status wording
    "priority",
    "diagnosis",
    "clinical_indication",
    "cancellable",
  ]) {
    assert.ok(
      LABORATORY.includes(`order.${field}`),
      `laboratory-panel no longer renders order.${field}`,
    );
  }
  // The test names themselves come through the summary helper.
  assert.ok(LABORATORY.includes("orderTestSummary(order)"));
});

test("the radiology order card still renders every clinical field", () => {
  for (const field of [
    "request_code",
    "status_label",
    "priority",
    "diagnosis",
    "clinical_indication",
    "instructions", // patient preparation -- radiology-only
    "cancellable",
  ]) {
    assert.ok(
      RADIOLOGY.includes(`order.${field}`),
      `radiology-panel no longer renders order.${field}`,
    );
  }
  assert.ok(RADIOLOGY.includes("orderExamSummary(order)"));
  // Contrast is preparation the patient has to be told about.
  assert.ok(RADIOLOGY.includes("orderNeedsContrast(order)"));
});

/* ------------------------------------------------------------------ *
 * Cancellation is still wired
 * ------------------------------------------------------------------ */

test("laboratory cancellation is still offered and still calls the cancel route", () => {
  assert.ok(LABORATORY.includes("order.cancellable"), "cancel control dropped");
  assert.ok(
    LABORATORY.includes("/orders/laboratory/${order.id}/cancel"),
    "laboratory cancel route no longer called",
  );
  // The confirm step survives: cancelling an order is not a one-click act.
  assert.ok(LABORATORY.includes("confirmCancelId"));
});

test("radiology cancellation is still offered and still calls the cancel route", () => {
  assert.ok(RADIOLOGY.includes("order.cancellable"), "cancel control dropped");
  assert.ok(
    RADIOLOGY.includes("/orders/radiology/${order.id}/cancel"),
    "radiology cancel route no longer called",
  );
  assert.ok(RADIOLOGY.includes("confirmCancelId"));
});

/* ------------------------------------------------------------------ *
 * Nothing priced reached the clinical surface
 * ------------------------------------------------------------------ */

test("no billing or payer vocabulary appears in either order panel", () => {
  for (const [name, source] of [
    ["laboratory-panel", LABORATORY],
    ["radiology-panel", RADIOLOGY],
  ] as const) {
    const lower = source.toLowerCase();
    for (const term of FORBIDDEN) {
      assert.ok(
        !lower.includes(term),
        `'${term}' appeared in ${name}: the Doctor Desk shows a clearance verdict, never a sum`,
      );
    }
  }
});

test("the panels read a boolean verdict, never a figure", () => {
  // `awaiting_clearance` is the ONE billing-derived thing a clinician sees: a
  // status key carrying no amount, no payer and no allocation.
  assert.ok(LABORATORY.includes("awaiting_clearance"));
  assert.ok(RADIOLOGY.includes("awaiting_clearance"));
});

/* ------------------------------------------------------------------ *
 * The readability pass itself
 * ------------------------------------------------------------------ */

test("no Doctor Desk clinical text is pinned below the readable floor", () => {
  /*
    The desk was built with ad-hoc `text-[9px]` .. `text-[13px]` utilities. They
    are now expressed through the clinical type scale in globals.css, whose
    smallest tier is 11px and which starts clinical content at 13px. A new
    arbitrary pixel size here is how the floor gets quietly lowered again.
  */
  for (const [name, source] of [
    ["laboratory-panel", LABORATORY],
    ["radiology-panel", RADIOLOGY],
  ] as const) {
    const arbitrary = source.match(/text-\[\d+(?:\.\d+)?px\]/g);
    assert.equal(
      arbitrary,
      null,
      `${name} reintroduced arbitrary font sizes: ${arbitrary?.join(", ")}`,
    );
  }
});

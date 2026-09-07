/**
 * Cashier Desk presentation helpers.
 *
 * Written against `node:test` and `node:assert`, both Node built-ins.
 * Run with `npm test`.
 *
 * THE PROPERTY EVERY TEST HERE DEFENDS: this module formats, and decides
 * nothing. The server owns money, lanes, blocking reasons and service
 * vocabulary; a helper that reproduced any of them would be a second answer
 * waiting to disagree with the one the cashier is acting on.
 *
 * `serviceCategorySummary` is the newest place that temptation appears. It
 * renders the generic categories holding a visit at the window -- Laboratory,
 * Radiology, Medication, Procedure -- and it MUST take those words from the
 * payload rather than mapping the key itself, or a category this build has not
 * heard of renders blank instead of rendering itself.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { CashierCollectability, CashierServiceCategory } from "@/types/cashier";

import {
  blockedMessage,
  cashierLabel,
  laneLabel,
  money,
  serviceCategorySummary,
} from "./cashier-format.ts";

const categories = (...entries: CashierServiceCategory[]) => entries;

test("service categories render the server's own labels", () => {
  assert.equal(
    serviceCategorySummary(
      categories(
        { key: "laboratory", label: "Laboratory" },
        { key: "pharmacy", label: "Medication" },
      ),
    ),
    "Laboratory · Medication",
  );
});

test("a category this build has never heard of still renders", () => {
  // The whole reason the label is not mapped locally. A future service_type
  // ('physiotherapy', say) must appear at the window, not vanish from it.
  assert.equal(
    serviceCategorySummary(
      categories({ key: "physiotherapy", label: "Physiotherapy" }),
    ),
    "Physiotherapy",
  );
});

test("no categories renders nothing rather than an empty badge", () => {
  assert.equal(serviceCategorySummary([]), null);
  assert.equal(serviceCategorySummary(null), null);
  assert.equal(serviceCategorySummary(undefined), null);
  assert.equal(
    serviceCategorySummary(categories({ key: "other", label: "   " })),
    null,
    "A whitespace label is not a label.",
  );
});

test("the in-consultation state is labelled as the fact it is", () => {
  // The active-service lane shows this beside a patient who is with a doctor.
  // Paying does not change it, and the desk must not imply that it does.
  assert.equal(cashierLabel("in_consultation"), "In consultation");
  assert.equal(cashierLabel("confirmed"), "Checked in");
});

test("an unknown state is title-cased rather than dropped", () => {
  assert.equal(cashierLabel("some_new_state"), "Some New State");
  assert.equal(cashierLabel(null), "-");
});

test("lane labels are the operational vocabulary, shared by both lanes", () => {
  assert.equal(laneLabel("collect"), "Awaiting payment");
  assert.equal(laneLabel("partial"), "Part paid");
  assert.equal(laneLabel("blocked"), "Blocked");
});

test("money never invents a figure", () => {
  assert.equal(money(550), "550.00");
  assert.equal(money(1500.5), "1,500.50");
  // A missing figure is zero on screen, never NaN.
  assert.equal(money(null), "0.00");
  assert.equal(money(undefined), "0.00");
});

test("a blocked verdict is quoted, never reworded", () => {
  const verdict: CashierCollectability = {
    collectable: false,
    lane: "blocked",
    reason: "A sponsor share has been recorded but not authorized.",
    reason_code: "sponsor_authorization_pending",
  };
  assert.equal(blockedMessage(verdict), verdict.reason);
});

test("a collectable visit has no blocking message", () => {
  assert.equal(
    blockedMessage({
      collectable: true,
      lane: "collect",
      reason: null,
      reason_code: null,
    }),
    null,
  );
});

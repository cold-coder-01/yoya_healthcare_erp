import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CASHIER_ROUTE,
  INSURANCE_CREDIT_ROUTE,
  CLINICAL_ROUTE,
  FRONT_DESK_ROUTE,
  RECEPTION_ROUTE,
  canUseCashier,
  canUseInsuranceCredit,
  landingRouteForRoles,
  parseReceptionRoles,
  type ReceptionRoles,
} from "./reception-roles.ts";

/**
 * Post-login routing.
 *
 * Run with `npm test` -- Node's built-in runner, executing TypeScript directly.
 * No test framework is installed in this project and none is added here: this
 * module imports nothing, so it needs no resolver, no transform and no config.
 *
 * THE REGRESSION THESE TESTS EXIST FOR: a pure Cashier held no front-desk and
 * no reception-side role, so they fell through BOTH branches of
 * landingRouteForRoles by elimination and landed on /triage -- the clinical
 * Evaluation Queue, a workspace they cannot act in. `roles.cashier` was parsed
 * all along and read by nothing.
 */

function roles(overrides: Partial<ReceptionRoles> = {}): ReceptionRoles {
  return {
    receptionist: false,
    cashier: false,
    accountant: false,
    manager: false,
    system_administrator: false,
    emergency_authorizer: false,
    front_desk_nurse: false,
    insurance_officer: false,
    ...overrides,
  };
}

test("a pure cashier lands on the cashier desk", () => {
  assert.equal(landingRouteForRoles(roles({ cashier: true })), CASHIER_ROUTE);
});

test("a front desk nurse still lands on the front desk", () => {
  assert.equal(
    landingRouteForRoles(roles({ front_desk_nurse: true })),
    FRONT_DESK_ROUTE,
  );
});

test("front desk keeps precedence over cashier", () => {
  assert.equal(
    landingRouteForRoles(roles({ front_desk_nurse: true, cashier: true })),
    FRONT_DESK_ROUTE,
  );
});

test("a receptionist still lands on the reception queue", () => {
  assert.equal(
    landingRouteForRoles(roles({ receptionist: true })),
    RECEPTION_ROUTE,
  );
});

test("manager behaviour is unchanged by the cashier branch", () => {
  // canUseReception includes manager, and it is evaluated first -- so a manager
  // who also holds Cashier still lands on reception, exactly as before.
  assert.equal(landingRouteForRoles(roles({ manager: true })), RECEPTION_ROUTE);
  assert.equal(
    landingRouteForRoles(roles({ manager: true, cashier: true })),
    RECEPTION_ROUTE,
  );
});

test("system administrator behaviour is unchanged", () => {
  assert.equal(
    landingRouteForRoles(roles({ system_administrator: true })),
    RECEPTION_ROUTE,
  );
});

test("a plain nurse still falls back to the clinical workspace", () => {
  // No reception-side role at all: the by-elimination path that was correct
  // for nurses all along, and wrong only for cashiers.
  assert.equal(landingRouteForRoles(roles()), CLINICAL_ROUTE);
});

test("an unknown or missing role payload falls back to clinical", () => {
  assert.equal(landingRouteForRoles(null), CLINICAL_ROUTE);
});

test("an accountant lands on the cashier desk rather than clinical", () => {
  // They hold operational intake rights and no reception role; /triage was
  // strictly worse for them than the desk they can actually use.
  assert.equal(landingRouteForRoles(roles({ accountant: true })), CASHIER_ROUTE);
});

test("canUseCashier mirrors the server's CASHIER_DESK_GROUPS", () => {
  assert.equal(canUseCashier(roles({ cashier: true })), true);
  assert.equal(canUseCashier(roles({ accountant: true })), true);
  assert.equal(canUseCashier(roles({ manager: true })), true);
  assert.equal(canUseCashier(roles({ system_administrator: true })), true);
  // Deliberately excluded, mirroring the server's exclusion of the Cashier from
  // the front-desk worklist. The two workstations do not read each other.
  assert.equal(canUseCashier(roles({ front_desk_nurse: true })), false);
  assert.equal(canUseCashier(roles({ receptionist: true })), false);
  assert.equal(canUseCashier(null), false);
});

test("parseReceptionRoles surfaces the cashier flag", () => {
  const parsed = parseReceptionRoles({ cashier: true, receptionist: false });
  assert.equal(parsed?.cashier, true);
  // Strict === true: a payload from an un-upgraded Odoo never grants a role.
  assert.equal(parseReceptionRoles({ cashier: "yes" })?.cashier, false);
});

test("a pure insurance officer lands on the insurance/credit desk", () => {
  assert.equal(
    landingRouteForRoles(roles({ insurance_officer: true })),
    INSURANCE_CREDIT_ROUTE,
  );
});

test("an officer who is also a cashier keeps the cashier desk", () => {
  // No-regression: that user lands on /cashier today, and the insurance branch
  // sits BELOW cashier precisely so adding it moves nobody.
  assert.equal(
    landingRouteForRoles(roles({ insurance_officer: true, cashier: true })),
    CASHIER_ROUTE,
  );
});

test("manager and admin landing does not regress when they hold the officer group", () => {
  assert.equal(
    landingRouteForRoles(roles({ manager: true, insurance_officer: true })),
    RECEPTION_ROUTE,
  );
  assert.equal(
    landingRouteForRoles(
      roles({ system_administrator: true, insurance_officer: true }),
    ),
    RECEPTION_ROUTE,
  );
});

test("front desk keeps precedence over the officer branch", () => {
  assert.equal(
    landingRouteForRoles(roles({ front_desk_nurse: true, insurance_officer: true })),
    FRONT_DESK_ROUTE,
  );
});

test("canUseInsuranceCredit mirrors the server's INSURANCE_CREDIT_GROUPS", () => {
  assert.equal(canUseInsuranceCredit(roles({ insurance_officer: true })), true);
  assert.equal(canUseInsuranceCredit(roles({ manager: true })), true);
  assert.equal(canUseInsuranceCredit(roles({ system_administrator: true })), true);
  // Absent from the server tuple, so absent here: the party who books the
  // receivable does not decide it.
  assert.equal(canUseInsuranceCredit(roles({ accountant: true })), false);
  assert.equal(canUseInsuranceCredit(roles({ cashier: true })), false);
  assert.equal(canUseInsuranceCredit(null), false);
});

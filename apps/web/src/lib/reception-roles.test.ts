import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CASHIER_ROUTE,
  DOCTOR_ROUTE,
  INSURANCE_CREDIT_ROUTE,
  LABORATORY_ROUTE,
  CLINICAL_ROUTE,
  FRONT_DESK_ROUTE,
  RADIOLOGY_ROUTE,
  RECEPTION_ROUTE,
  canUseCashier,
  canUseDoctorDesk,
  canUseInsuranceCredit,
  canUseRadiologyDesk,
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
    doctor: false,
    lab_technician: false,
    radiology_technician: false,
    radiologist: false,
    ...overrides,
  };
}

/**
 * A MANAGER AS ODOO ACTUALLY REPORTS THEM.
 *
 * group_hospital_manager carries implied_ids = receptionist + doctor + nurse,
 * so role_flags() sets BOTH manager and doctor for one. Tests that build a
 * manager with only `{manager: true}` are testing a user Odoo cannot produce,
 * and would not catch a precedence regression that moved managers to /doctor.
 */
function managerRoles(overrides: Partial<ReceptionRoles> = {}): ReceptionRoles {
  return roles({ manager: true, receptionist: true, doctor: true, ...overrides });
}

function adminRoles(overrides: Partial<ReceptionRoles> = {}): ReceptionRoles {
  return roles({
    system_administrator: true,
    manager: true,
    receptionist: true,
    doctor: true,
    ...overrides,
  });
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

/* ------------------------------------------------------------------ *
 * Doctor Desk
 * ------------------------------------------------------------------ */

test("a pure doctor lands on the doctor desk", () => {
  assert.equal(landingRouteForRoles(roles({ doctor: true })), DOCTOR_ROUTE);
});

test("a plain nurse still lands on /triage and is never routed to the doctor desk", () => {
  // THE regression this branch must not cause. A nurse holds no reception-side
  // role, so before the doctor branch existed they reached /triage by
  // elimination -- and they must still, because `doctor` is real group
  // membership and is never inferred from the absence of another role.
  assert.equal(landingRouteForRoles(roles()), CLINICAL_ROUTE);
  assert.equal(landingRouteForRoles(roles({ emergency_authorizer: true })), CLINICAL_ROUTE);
});

test("a manager as Odoo really reports them still lands on reception", () => {
  // group_hospital_manager IMPLIES group_hospital_doctor, so a real manager
  // carries doctor === true. Precedence, not flag width, is what keeps them on
  // /reception -- canUseReception claims them four branches before the doctor
  // branch is reached.
  assert.equal(landingRouteForRoles(managerRoles()), RECEPTION_ROUTE);
  assert.equal(landingRouteForRoles(adminRoles()), RECEPTION_ROUTE);
});

test("every existing landing role outranks the doctor branch", () => {
  // No user who has an operational landing page today may be moved to the
  // Doctor Desk by the addition of this flag.
  assert.equal(
    landingRouteForRoles(roles({ front_desk_nurse: true, doctor: true })),
    FRONT_DESK_ROUTE,
  );
  assert.equal(
    landingRouteForRoles(roles({ receptionist: true, doctor: true })),
    RECEPTION_ROUTE,
  );
  assert.equal(
    landingRouteForRoles(roles({ cashier: true, doctor: true })),
    CASHIER_ROUTE,
  );
  assert.equal(
    landingRouteForRoles(roles({ insurance_officer: true, doctor: true })),
    INSURANCE_CREDIT_ROUTE,
  );
});

test("canUseDoctorDesk mirrors the server's DOCTOR_DESK_GROUPS", () => {
  assert.equal(canUseDoctorDesk(roles({ doctor: true })), true);
  assert.equal(canUseDoctorDesk(roles({ manager: true })), true);
  assert.equal(canUseDoctorDesk(roles({ system_administrator: true })), true);
  // Absent from the server tuple: the desk is not a second door into the
  // nursing or cash surfaces.
  assert.equal(canUseDoctorDesk(roles({ front_desk_nurse: true })), false);
  assert.equal(canUseDoctorDesk(roles({ receptionist: true })), false);
  assert.equal(canUseDoctorDesk(roles({ cashier: true })), false);
  assert.equal(canUseDoctorDesk(roles({ accountant: true })), false);
  assert.equal(canUseDoctorDesk(null), false);
});

test("parseReceptionRoles surfaces the doctor flag strictly", () => {
  assert.equal(parseReceptionRoles({ doctor: true })?.doctor, true);
  // An un-upgraded Odoo that sends no doctor key must not grant the desk.
  assert.equal(parseReceptionRoles({ cashier: true })?.doctor, false);
  assert.equal(parseReceptionRoles({ doctor: "yes" })?.doctor, false);
});

/* ------------------------------------------------------------------ *
 * Laboratory Desk routing
 *
 * THE REGRESSION THESE EXIST FOR, and it is the same shape as the cashier
 * one above: a Lab Technician held no reception-side role and not the doctor
 * group, so they fell through EVERY branch by elimination and landed on
 * /triage -- the clinical Evaluation Queue, for which they hold no ACL at all.
 * The Laboratory Desk was reachable only by typing the URL.
 * ------------------------------------------------------------------ */

test("a pure lab technician lands on the laboratory desk", () => {
  assert.equal(
    landingRouteForRoles(roles({ lab_technician: true })),
    LABORATORY_ROUTE,
  );
});

test("a lab technician no longer falls through to the clinical queue", () => {
  // The exact regression: before the branch existed this returned /triage.
  assert.notEqual(
    landingRouteForRoles(roles({ lab_technician: true })),
    CLINICAL_ROUTE,
  );
});

test("a doctor who is also a lab technician keeps the doctor desk", () => {
  // The lab branch sits BELOW doctor so nobody is silently relocated.
  assert.equal(
    landingRouteForRoles(roles({ doctor: true, lab_technician: true })),
    DOCTOR_ROUTE,
  );
});

test("a manager is unaffected by the lab branch", () => {
  // Odoo cannot even produce this user -- manager does not imply lab
  // technician -- but if one were granted both, reception still claims them.
  assert.equal(landingRouteForRoles(managerRoles()), RECEPTION_ROUTE);
  assert.equal(
    landingRouteForRoles(managerRoles({ lab_technician: true })),
    RECEPTION_ROUTE,
  );
});

test("an admin is unaffected by the lab branch", () => {
  assert.equal(
    landingRouteForRoles(adminRoles({ lab_technician: true })),
    RECEPTION_ROUTE,
  );
});

test("a front desk nurse who is also a lab technician keeps the front desk", () => {
  assert.equal(
    landingRouteForRoles(roles({ front_desk_nurse: true, lab_technician: true })),
    FRONT_DESK_ROUTE,
  );
});

test("a plain nurse still lands on the clinical queue", () => {
  // Absence of the lab flag must never route someone to the bench.
  assert.equal(landingRouteForRoles(roles()), CLINICAL_ROUTE);
});

test("parseReceptionRoles surfaces the lab technician flag", () => {
  const parsed = parseReceptionRoles({ lab_technician: true });
  assert.equal(parsed?.lab_technician, true);
  assert.equal(parsed?.doctor, false);
});

test("a payload from an older Odoo without the lab flag reads false", () => {
  // Strict === true, so a server that predates the flag never grants the bench.
  const parsed = parseReceptionRoles({ receptionist: true });
  assert.equal(parsed?.lab_technician, false);
  assert.equal(landingRouteForRoles(parsed), RECEPTION_ROUTE);
});

test("a non-boolean lab flag is not accepted as true", () => {
  assert.equal(parseReceptionRoles({ lab_technician: "yes" })?.lab_technician, false);
  assert.equal(parseReceptionRoles({ lab_technician: 1 })?.lab_technician, false);
});

test("the landing route is a default, never a permission", () => {
  // /lab/* is gated server-side by may_lab_desk(). Routing decides where a
  // user STARTS; it cannot grant a desk, which is why a forged flag is
  // harmless -- the desk still answers 403.
  assert.equal(LABORATORY_ROUTE, "/laboratory");
});

/* ------------------------------------------------------------------ *
 * Radiology Desk routing
 *
 * The same shape of regression again: a pure Radiology Technician or
 * Radiologist holds no reception-side, doctor or laboratory role, and without a
 * branch they would fall through to /triage.
 * ------------------------------------------------------------------ */

test("a pure radiology technician lands on the radiology desk", () => {
  assert.equal(
    landingRouteForRoles(roles({ radiology_technician: true })),
    RADIOLOGY_ROUTE,
  );
});

test("a pure radiologist lands on the radiology desk", () => {
  assert.equal(landingRouteForRoles(roles({ radiologist: true })), RADIOLOGY_ROUTE);
  assert.equal(
    landingRouteForRoles(roles({ radiology_technician: true, radiologist: true })),
    RADIOLOGY_ROUTE,
  );
});

test("a lab technician is never routed to radiology", () => {
  assert.equal(landingRouteForRoles(roles({ lab_technician: true })), LABORATORY_ROUTE);
  // Holding both keeps the landing page a lab technician already has.
  assert.equal(
    landingRouteForRoles(roles({ lab_technician: true, radiology_technician: true })),
    LABORATORY_ROUTE,
  );
});

test("doctor, manager, admin and front desk keep their landing pages", () => {
  assert.equal(
    landingRouteForRoles(roles({ doctor: true, radiologist: true })),
    DOCTOR_ROUTE,
  );
  assert.equal(
    landingRouteForRoles(managerRoles({ radiology_technician: true })),
    RECEPTION_ROUTE,
  );
  assert.equal(
    landingRouteForRoles(adminRoles({ radiologist: true })),
    RECEPTION_ROUTE,
  );
  assert.equal(
    landingRouteForRoles(roles({ front_desk_nurse: true, radiologist: true })),
    FRONT_DESK_ROUTE,
  );
});

test("a plain nurse still lands on the clinical queue", () => {
  assert.equal(landingRouteForRoles(roles({})), CLINICAL_ROUTE);
});

test("canUseRadiologyDesk mirrors the server's RAD_DESK_GROUPS", () => {
  assert.equal(canUseRadiologyDesk(roles({ radiology_technician: true })), true);
  assert.equal(canUseRadiologyDesk(roles({ radiologist: true })), true);
  assert.equal(canUseRadiologyDesk(roles({ manager: true })), true);
  assert.equal(canUseRadiologyDesk(roles({ system_administrator: true })), true);
  for (const denied of [
    { lab_technician: true },
    { doctor: true },
    { receptionist: true },
    { front_desk_nurse: true },
    { cashier: true },
    { accountant: true },
    { insurance_officer: true },
    {},
  ]) {
    assert.equal(canUseRadiologyDesk(roles(denied)), false, JSON.stringify(denied));
  }
  assert.equal(canUseRadiologyDesk(null), false);
});

test("the radiology flags are parsed strictly", () => {
  const parsed = parseReceptionRoles({ radiology_technician: true, radiologist: true });
  assert.equal(parsed?.radiology_technician, true);
  assert.equal(parsed?.radiologist, true);
  assert.equal(parsed?.lab_technician, false);
  // An Odoo that predates the flags, or sends a non-boolean, grants nothing.
  assert.equal(parseReceptionRoles({ receptionist: true })?.radiologist, false);
  assert.equal(parseReceptionRoles({ radiologist: "yes" })?.radiologist, false);
  assert.equal(parseReceptionRoles({ radiology_technician: 1 })?.radiology_technician, false);
  assert.equal(RADIOLOGY_ROUTE, "/radiology");
});

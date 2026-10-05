/**
 * THE DEDICATED WARD NURSE: login lands on /admissions, /triage sends them
 * there, and the legacy clinical shell is never rendered for them -- while
 * every other role's landing page is exactly what it was.
 *
 * Run with `npm test`. The routing logic is exercised directly; the redirect
 * and shell guarantees are held at the source (no DOM test stack exists).
 *
 * THE BUG. The reception session carried no `nurse` flag, so a plain Hospital
 * Nurse matched no branch of landingRouteForRoles and fell through to
 * CLINICAL_ROUTE (/triage) by elimination -- the legacy Clinical UAT shell,
 * one click away from the desk they actually work.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  ADMISSIONS_ROUTE,
  CASHIER_ROUTE,
  CLINICAL_ROUTE,
  DOCTOR_ROUTE,
  FRONT_DESK_ROUTE,
  LABORATORY_ROUTE,
  PHARMACY_ROUTE,
  RADIOLOGY_ROUTE,
  RECEPTION_ROUTE,
  canUseAdmissionsDesk,
  canUseClinical,
  isWardOnlyNurse,
  landingRouteForRoles,
  parseReceptionRoles,
  type ReceptionRoles,
} from "./reception-roles.ts";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

function roles(overrides: Partial<ReceptionRoles> = {}): ReceptionRoles {
  return {
    receptionist: false, cashier: false, accountant: false, manager: false,
    system_administrator: false, emergency_authorizer: false, front_desk_nurse: false,
    insurance_officer: false, doctor: false, lab_technician: false,
    radiology_technician: false, radiologist: false, pharmacist: false, nurse: false,
    ...overrides,
  };
}

/* Users as ODOO ACTUALLY REPORTS THEM: implied groups included. */
const WARD_NURSE = roles({ nurse: true });
const MANAGER = roles({ manager: true, receptionist: true, doctor: true, nurse: true });
const ADMIN = roles({ system_administrator: true, manager: true, receptionist: true, doctor: true, nurse: true });
const FRONT_DESK_NURSE = roles({ front_desk_nurse: true, nurse: true });
const USER_2 = roles({
  system_administrator: true, manager: true, receptionist: true, doctor: true, nurse: true,
  accountant: true, lab_technician: true, pharmacist: true,
});

/* ------------------------------ 1. landing ------------------------------ */

test("1. a dedicated ward nurse lands on /admissions", () => {
  assert.equal(isWardOnlyNurse(WARD_NURSE), true);
  assert.equal(landingRouteForRoles(WARD_NURSE), ADMISSIONS_ROUTE);
  assert.equal(ADMISSIONS_ROUTE, "/admissions");
});

test("the nurse flag is parsed strictly; its absence never makes anyone ward-only", () => {
  const parsed = parseReceptionRoles({ nurse: true, receptionist: false });
  assert.equal(parsed?.nurse, true);
  assert.equal(parseReceptionRoles({ receptionist: false })?.nurse, false);
  assert.equal(parseReceptionRoles({ nurse: "true" })?.nurse, false);
  // An Odoo instance that predates the flag keeps today's landing page.
  assert.equal(landingRouteForRoles(roles()), CLINICAL_ROUTE);
  assert.equal(isWardOnlyNurse(null), false);
  assert.equal(landingRouteForRoles(null), CLINICAL_ROUTE);
});

test("ward-only is conservative: ANY second role keeps the broader landing", () => {
  const second: Array<[Partial<ReceptionRoles>, string]> = [
    [{ front_desk_nurse: true }, FRONT_DESK_ROUTE],
    [{ receptionist: true }, RECEPTION_ROUTE],
    [{ manager: true, receptionist: true, doctor: true }, RECEPTION_ROUTE],
    [{ system_administrator: true, manager: true, receptionist: true, doctor: true }, RECEPTION_ROUTE],
    [{ cashier: true }, CASHIER_ROUTE],
    [{ accountant: true }, "/accountant"],
    [{ doctor: true }, DOCTOR_ROUTE],
    [{ lab_technician: true }, LABORATORY_ROUTE],
    [{ radiology_technician: true }, RADIOLOGY_ROUTE],
    [{ radiologist: true }, RADIOLOGY_ROUTE],
    [{ pharmacist: true }, PHARMACY_ROUTE],
    [{ insurance_officer: true }, "/insurance-credit"],
    [{ emergency_authorizer: true }, CLINICAL_ROUTE],
  ];
  for (const [extra, expected] of second) {
    const user = roles({ nurse: true, ...extra });
    assert.equal(isWardOnlyNurse(user), false, JSON.stringify(extra));
    assert.equal(landingRouteForRoles(user), expected, JSON.stringify(extra));
  }
});

/* ------------------------------ 4-9. nobody else moves ------------------------------ */

test("4. manager and admin keep their landing and are offered the desk as a link", () => {
  for (const user of [MANAGER, ADMIN, USER_2]) {
    assert.equal(isWardOnlyNurse(user), false);
    assert.equal(landingRouteForRoles(user), RECEPTION_ROUTE);
    assert.equal(canUseAdmissionsDesk(user), true);
    assert.equal(canUseClinical(user), true);
  }
});

test("5-9. doctor, reception, pharmacy, radiology and laboratory routing unchanged", () => {
  assert.equal(landingRouteForRoles(roles({ doctor: true })), DOCTOR_ROUTE);
  assert.equal(landingRouteForRoles(roles({ receptionist: true })), RECEPTION_ROUTE);
  assert.equal(landingRouteForRoles(FRONT_DESK_NURSE), FRONT_DESK_ROUTE);
  assert.equal(landingRouteForRoles(roles({ pharmacist: true })), PHARMACY_ROUTE);
  assert.equal(landingRouteForRoles(roles({ radiology_technician: true })), RADIOLOGY_ROUTE);
  assert.equal(landingRouteForRoles(roles({ radiologist: true })), RADIOLOGY_ROUTE);
  assert.equal(landingRouteForRoles(roles({ lab_technician: true })), LABORATORY_ROUTE);
});

test("12. denied Admissions roles are never landed on the desk", () => {
  for (const user of [
    roles({ pharmacist: true }), roles({ lab_technician: true }), roles({ cashier: true }),
    roles({ accountant: true }), roles({ radiology_technician: true }),
  ]) {
    assert.notEqual(landingRouteForRoles(user), ADMISSIONS_ROUTE);
    assert.equal(canUseAdmissionsDesk(user), false);
  }
  // ...and the desk still says so in words if they type the URL.
  assert.ok(code(read("components/admissions/admissions-workstation.tsx")).includes("This is not your workstation."));
});

/* ------------------------------ 2, 3, 10, 11. shell and redirect ------------------------------ */

const TRIAGE_LAYOUT = read("app/triage/layout.tsx");
const ADMISSIONS_LAYOUT = read("app/admissions/layout.tsx");
const LOGIN = read("app/login/page.tsx");
const RECEPTION_SIDEBAR = read("components/reception/reception-sidebar.tsx");

test("2. /triage redirects a ward-only nurse to /admissions on the SERVER", () => {
  const emitted = code(TRIAGE_LAYOUT);
  assert.doesNotMatch(TRIAGE_LAYOUT, /^"use client"/m);
  assert.ok(emitted.includes("isWardOnlyNurse(await loadReceptionRoles())"));
  assert.ok(emitted.includes("redirect(ADMISSIONS_ROUTE)"));
  // Every page under /triage renders inside this layout.
  assert.ok(read("app/triage/page.tsx").includes("ClinicalShell"));
  assert.ok(read("app/triage/[appointmentId]/page.tsx").includes("ClinicalShell"));
});

test("3. the legacy clinical shell is rendered only under /triage, behind that guard", () => {
  const users = ["app/triage/page.tsx", "app/triage/[appointmentId]/page.tsx"];
  for (const path of users) {
    assert.ok(read(path).includes("ClinicalShell"), path);
  }
  assert.doesNotMatch(ADMISSIONS_LAYOUT, /ClinicalShell|ClinicalSidebar|ReceptionSidebar/);
  // The shared sidebar offers a ward-only nurse no Evaluation Queue either.
  assert.equal(canUseClinical(WARD_NURSE), false);
  assert.ok(code(RECEPTION_SIDEBAR).includes("visible: showClinical && !showFrontDesk"));
});

test("10. no redirect loop: the only redirect goes one way, and /admissions never redirects", () => {
  assert.doesNotMatch(code(ADMISSIONS_LAYOUT), /redirect\(|router\.(push|replace)\(/);
  assert.doesNotMatch(code(read("app/admissions/page.tsx")), /redirect\(/);
  assert.equal((code(TRIAGE_LAYOUT).match(/redirect\(/g) ?? []).length, 1);
  assert.notEqual(ADMISSIONS_ROUTE, CLINICAL_ROUTE);
});

test("11. /admissions stands alone and a ward-only nurse can sign out from it", () => {
  assert.ok(code(read("app/admissions/page.tsx")).includes("<AdmissionsWorkstation />"));
  assert.ok(code(ADMISSIONS_LAYOUT).includes("Admissions Desk"));
  assert.ok(code(ADMISSIONS_LAYOUT).includes("<AdmissionsUserMenu"));
  const menu = code(read("components/admissions/admissions-user-menu.tsx"));
  assert.ok(menu.includes('fetch("/api/auth/logout", { method: "POST" })'));
  assert.ok(menu.includes('router.push("/login")'));
});

test("ONE resolver: login routes through landingRouteForRoles and nothing else", () => {
  const emitted = code(LOGIN);
  assert.ok(emitted.includes("destination = landingRouteForRoles("));
  assert.doesNotMatch(emitted, /\/admissions|isWardOnlyNurse/);
});

test("the shared sidebar offers the Admissions Desk to multi-workstation roles", () => {
  const emitted = code(RECEPTION_SIDEBAR);
  assert.ok(emitted.includes("canUseAdmissionsDesk(roles) && !showFrontDesk"));
  assert.ok(emitted.includes('label: "Admissions Desk", href: ADMISSIONS_ROUTE'));
});

test("the server flag and the client contract agree", () => {
  const server = readFileSync(
    new URL("../../../odoo/custom_addons/yoya_emr_api/services/reception_scope.py", import.meta.url),
    "utf8",
  );
  const roleFlags = server.slice(server.indexOf("def role_flags"), server.indexOf("def _in_any"));
  assert.ok(roleFlags.includes('"nurse": user.has_group(GROUP_NURSE)'));
  const contract = readFileSync(
    new URL("../../../odoo/custom_addons/yoya_emr_api/tests/test_front_desk_session_roles.py", import.meta.url),
    "utf8",
  );
  assert.match(contract, /EXPECTED_ROLE_KEYS = \{[\s\S]*"nurse",[\s\S]*\}/);
});

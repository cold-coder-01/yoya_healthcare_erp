/**
 * FRONT DESK <-> ADMISSIONS: the sidebarless headers carry a role-aware pair
 * of tabs, so a receptionist never has to type /admissions -- and a ward nurse,
 * a doctor or a bench role is offered nothing new.
 *
 * Run with `npm test`. The role logic is exercised directly; the layout wiring
 * is held at the source (no DOM test stack exists).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  ADMISSIONS_ROUTE,
  FRONT_DESK_ROUTE,
  canUseAdmissionsDesk,
  canUseFrontOfHouseNav,
  frontOfHouseNavItems,
  landingRouteForRoles,
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
const RECEPTIONIST = roles({ receptionist: true });
const FRONT_DESK_NURSE = roles({ front_desk_nurse: true, nurse: true });
const MANAGER = roles({ manager: true, receptionist: true, doctor: true, nurse: true });
const ADMIN = roles({ system_administrator: true, manager: true, receptionist: true, doctor: true, nurse: true });
const WARD_NURSE = roles({ nurse: true });
const DOCTOR = roles({ doctor: true });

const UNAUTHORIZED: ReadonlyArray<readonly [string, ReceptionRoles]> = [
  ["pharmacist", roles({ pharmacist: true })],
  ["lab technician", roles({ lab_technician: true })],
  ["radiology technician", roles({ radiology_technician: true })],
  ["radiologist", roles({ radiologist: true })],
  ["cashier", roles({ cashier: true })],
  ["accountant", roles({ accountant: true })],
  ["insurance officer", roles({ insurance_officer: true })],
  ["emergency authorizer", roles({ emergency_authorizer: true })],
];

const hrefs = (r: ReceptionRoles | null, at: string) =>
  frontOfHouseNavItems(r, at).map((item) => item.href);

/* ------------------------------ reception ------------------------------ */

test("reception: Front Desk offers Admissions, and it goes to /admissions", () => {
  for (const user of [RECEPTIONIST, FRONT_DESK_NURSE]) {
    const items = frontOfHouseNavItems(user, FRONT_DESK_ROUTE);
    const admissions = items.find((item) => item.label === "Admissions");
    assert.ok(admissions);
    assert.equal(admissions.href, "/admissions");
    assert.equal(admissions.current, false);
    assert.equal(items.find((item) => item.current)?.href, FRONT_DESK_ROUTE);
  }
});

test("reception: the Admissions Desk offers a return to Front Desk", () => {
  for (const user of [RECEPTIONIST, FRONT_DESK_NURSE]) {
    const items = frontOfHouseNavItems(user, ADMISSIONS_ROUTE);
    const back = items.find((item) => item.label === "Front Desk");
    assert.ok(back);
    assert.equal(back.href, "/front-desk");
    assert.equal(back.current, false);
    assert.equal(items.find((item) => item.current)?.href, ADMISSIONS_ROUTE);
  }
});

/* ------------------------------ manager / admin ------------------------------ */

test("manager and admin get the pair on both desks", () => {
  for (const user of [MANAGER, ADMIN]) {
    assert.deepEqual(hrefs(user, FRONT_DESK_ROUTE), [FRONT_DESK_ROUTE, ADMISSIONS_ROUTE]);
    assert.deepEqual(hrefs(user, ADMISSIONS_ROUTE), [FRONT_DESK_ROUTE, ADMISSIONS_ROUTE]);
  }
});

/* ------------------------------ ward nurse ------------------------------ */

test("ward nurse: still lands on /admissions and is offered no Front Desk", () => {
  assert.equal(landingRouteForRoles(WARD_NURSE), ADMISSIONS_ROUTE);
  assert.equal(canUseAdmissionsDesk(WARD_NURSE), true);
  assert.equal(canUseFrontOfHouseNav(WARD_NURSE), false);
  assert.deepEqual(frontOfHouseNavItems(WARD_NURSE, ADMISSIONS_ROUTE), []);
});

/* ------------------------------ doctor ------------------------------ */

test("doctor: no Admissions navigation, though the server lets them read the desk", () => {
  assert.equal(canUseAdmissionsDesk(DOCTOR), true);
  assert.equal(canUseFrontOfHouseNav(DOCTOR), false);
  assert.deepEqual(frontOfHouseNavItems(DOCTOR, FRONT_DESK_ROUTE), []);
  assert.deepEqual(frontOfHouseNavItems(DOCTOR, ADMISSIONS_ROUTE), []);
  // Wide flags alone (as a manager would carry them) grant nothing.
  assert.equal(canUseFrontOfHouseNav(roles({ doctor: true, nurse: true })), false);
});

/* ------------------------------ unauthorized ------------------------------ */

test("pharmacy, lab, radiology, cashier, accountant, insurance, unknown: no navigation", () => {
  for (const [name, user] of UNAUTHORIZED) {
    assert.equal(canUseFrontOfHouseNav(user), false, name);
    assert.deepEqual(frontOfHouseNavItems(user, FRONT_DESK_ROUTE), [], name);
    assert.deepEqual(frontOfHouseNavItems(user, ADMISSIONS_ROUTE), [], name);
  }
  assert.deepEqual(frontOfHouseNavItems(null, FRONT_DESK_ROUTE), []);
});

/* ------------------------------ server agreement ------------------------------ */

test("every role offered the pair is in BOTH server gates", () => {
  const server = readFileSync(
    new URL("../../../odoo/custom_addons/yoya_emr_api/services/reception_scope.py", import.meta.url),
    "utf8",
  );
  const tuple = (name: string) => {
    const start = server.indexOf(`${name} = (`);
    return server.slice(start, server.indexOf(")", start));
  };
  const frontDesk = tuple("FRONT_DESK_GROUPS");
  const admissions = tuple("ADMISSIONS_DESK_GROUPS");
  for (const group of ["GROUP_RECEPTIONIST", "GROUP_MANAGER", "GROUP_SYSADMIN"]) {
    assert.ok(frontDesk.includes(group), group);
    assert.ok(admissions.includes(group), group);
  }
  // A Front Desk Nurse reaches the Admissions gate through the Nurse group it implies.
  assert.ok(frontDesk.includes("GROUP_FRONT_DESK_NURSE"));
  assert.ok(admissions.includes("GROUP_NURSE"));
});

/* ------------------------------ layout wiring ------------------------------ */

const NAV = read("components/navigation/workstation-nav.tsx");
const FRONT_DESK_LAYOUT = read("app/front-desk/layout.tsx");
const ADMISSIONS_LAYOUT = read("app/admissions/layout.tsx");

test("both headers render the tabs from the session flags, each marking itself current", () => {
  const front = code(FRONT_DESK_LAYOUT);
  assert.ok(front.includes("frontOfHouseNavItems(session?.roles ?? null, FRONT_DESK_ROUTE)"));
  assert.ok(front.includes('<WorkstationNav items={navItems} accent="emerald" />'));
  const admissions = code(ADMISSIONS_LAYOUT);
  assert.ok(admissions.includes("frontOfHouseNavItems(session?.roles ?? null, ADMISSIONS_ROUTE)"));
  assert.ok(admissions.includes('<WorkstationNav items={navItems} accent="sky" />'));
});

test("the Admissions header keeps its label and no longer claims to be read only", () => {
  const admissions = code(ADMISSIONS_LAYOUT);
  assert.ok(admissions.includes("Admissions Desk"));
  assert.doesNotMatch(admissions, /read only/i);
});

test("the nav is links only: no display-name check, no guard, nothing for an empty list", () => {
  const emitted = code(NAV);
  assert.doesNotMatch(NAV, /^"use client"/m);
  assert.ok(emitted.includes("if (items.length === 0) {\n    return null;"));
  assert.ok(emitted.includes('aria-current={item.current ? "page" : undefined}'));
  assert.doesNotMatch(emitted, /redirect\(|router\.|fetch\(/);
  for (const source of [NAV, FRONT_DESK_LAYOUT, ADMISSIONS_LAYOUT]) {
    assert.doesNotMatch(code(source), /userName\s*===|\.includes\(\s*["'](Reception|Nurse|Doctor)/);
  }
  assert.doesNotMatch(code(ADMISSIONS_LAYOUT), /redirect\(/);
  assert.doesNotMatch(code(FRONT_DESK_LAYOUT), /redirect\(/);
});

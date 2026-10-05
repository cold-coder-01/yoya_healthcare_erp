/**
 * WORKSTATION FULLSCREEN: one shared toggle in every desk header.
 *
 * Run with `npm test`. The Fullscreen API logic is driven with a fake
 * document; the component and its placement are held at the source (no DOM
 * test stack exists).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  FULLSCREEN_CHANGE,
  fullscreenLabel,
  fullscreenSupported,
  isFullscreen,
  subscribeFullscreen,
  toggleFullscreen,
  type FullscreenDocument,
} from "./fullscreen.ts";
import { ADMISSIONS_ROUTE, FRONT_DESK_ROUTE, frontOfHouseNavItems, type ReceptionRoles } from "./reception-roles.ts";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8");
}

function code(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

/** A browser-like document: request/exit flip state and fire fullscreenchange. */
function fakeDocument() {
  const listeners = new Set<() => void>();
  const calls: string[] = [];
  const root = {};
  const doc = {
    fullscreenEnabled: true,
    fullscreenElement: null as unknown,
    documentElement: {
      requestFullscreen: async () => {
        calls.push("request");
        doc.fullscreenElement = root;
        listeners.forEach((listener) => listener());
      },
    },
    exitFullscreen: async () => {
      calls.push("exit");
      doc.fullscreenElement = null;
      listeners.forEach((listener) => listener());
    },
    addEventListener: (type: string, listener: () => void) => {
      assert.equal(type, FULLSCREEN_CHANGE);
      listeners.add(listener);
    },
    removeEventListener: (_type: string, listener: () => void) => {
      listeners.delete(listener);
    },
    /** The browser's own Esc: no call from us, just the event. */
    pressEscape() {
      doc.fullscreenElement = null;
      listeners.forEach((listener) => listener());
    },
  };
  return { doc, calls, listeners };
}

const TOGGLE = read("components/workstation/fullscreen-toggle.tsx");

/* ------------------------------ behaviour ------------------------------ */

test("the toggle calls requestFullscreen on the document element", async () => {
  const { doc, calls } = fakeDocument();
  assert.equal(fullscreenSupported(doc), true);
  await toggleFullscreen(doc);
  assert.deepEqual(calls, ["request"]);
  assert.equal(isFullscreen(doc), true);
});

test("while fullscreen, the toggle calls exitFullscreen", async () => {
  const { doc, calls } = fakeDocument();
  await toggleFullscreen(doc);
  await toggleFullscreen(doc);
  assert.deepEqual(calls, ["request", "exit"]);
  assert.equal(isFullscreen(doc), false);
});

test("fullscreenchange drives the state, including the browser's own Esc", async () => {
  const { doc, listeners } = fakeDocument();
  const seen: boolean[] = [];
  const unsubscribe = subscribeFullscreen(doc, () => seen.push(isFullscreen(doc)));
  await toggleFullscreen(doc);
  doc.pressEscape();
  assert.deepEqual(seen, [true, false]);
  assert.deepEqual([true, false].map(fullscreenLabel), ["Exit fullscreen", "Enter fullscreen"]);
  unsubscribe();
  assert.equal(listeners.size, 0);
});

test("an unsupported browser never crashes and is offered no button", async () => {
  const missing: Array<FullscreenDocument | null | undefined> = [
    null,
    undefined,
    {},
    { fullscreenEnabled: false, documentElement: { requestFullscreen: async () => {} }, exitFullscreen: async () => {} },
    { fullscreenEnabled: true, documentElement: {} },
    { fullscreenEnabled: true, documentElement: null },
  ];
  for (const doc of missing) {
    assert.equal(fullscreenSupported(doc), false);
    assert.equal(isFullscreen(doc), false);
    await toggleFullscreen(doc);
    subscribeFullscreen(doc, () => {})();
  }
  assert.ok(code(TOGGLE).includes("if (!supported) {\n    return null;"));
});

test("a refused request is swallowed and leaves the state alone", async () => {
  const { doc } = fakeDocument();
  doc.documentElement.requestFullscreen = async () => {
    throw new TypeError("Permissions check failed");
  };
  await toggleFullscreen(doc);
  assert.equal(isFullscreen(doc), false);
});

/* ------------------------------ component ------------------------------ */

test("the button is a real, labelled, keyboard-reachable button that never touches keys", () => {
  const emitted = code(TOGGLE);
  assert.match(TOGGLE, /^"use client"/m);
  assert.ok(emitted.includes('type="button"'));
  assert.ok(emitted.includes("aria-label={label}"));
  assert.ok(emitted.includes("title={label}"));
  assert.ok(emitted.includes("aria-pressed={active}"));
  assert.ok(emitted.includes("onClick={() => void toggleFullscreen(document)}"));
  assert.ok(emitted.includes("(onChange) => subscribeFullscreen(document, onChange)"));
  // Esc belongs to the browser; no key handling, no navigation, no reload.
  assert.doesNotMatch(emitted, /keydown|onKeyDown|Escape|preventDefault|tabIndex=\{-1\}|<div[^>]*onClick/);
  assert.doesNotMatch(emitted, /location\.|router\.|reload\(|fetch\(/);
});

/* ------------------------------ placement ------------------------------ */

const HEADERS: ReadonlyArray<readonly [string, string, RegExp]> = [
  ["Front Desk", "app/front-desk/layout.tsx", /<FrontDeskUserMenu /],
  ["Admissions / Ward Nurse", "app/admissions/layout.tsx", /<AdmissionsUserMenu /],
  ["Doctor Desk", "app/doctor/layout.tsx", /\{userName \? \(/],
  ["Pharmacy", "app/pharmacy/layout.tsx", /\{userName \? \(/],
  ["Laboratory", "app/laboratory/layout.tsx", /\{userName \? \(/],
  ["Radiology", "app/radiology/layout.tsx", /\{userName \? \(/],
  ["Cashier", "app/cashier/layout.tsx", /<FrontDeskUserMenu /],
  ["Insurance / Credit", "app/insurance-credit/layout.tsx", /<FrontDeskUserMenu /],
  ["Reception / Manager / Admin / Triage (TopHeader)", "components/clinical/top-header.tsx", /onClick=\{logout\}/],
];

test("every workstation header renders the toggle once, immediately before the user control", () => {
  for (const [name, path, user] of HEADERS) {
    const emitted = code(read(path));
    const header = emitted.slice(emitted.indexOf("<header"), emitted.indexOf("</header>"));
    assert.equal((header.match(/<FullscreenToggle \/>/g) ?? []).length, 1, name);
    const toggleAt = header.indexOf("<FullscreenToggle />");
    const userAt = header.search(user);
    assert.ok(userAt > toggleAt, `${name}: toggle before user control`);
    // Nothing else sits between them.
    assert.doesNotMatch(header.slice(toggleAt + "<FullscreenToggle />".length, userAt), /<(?!\/?div|button)[A-Za-z]/, name);
    assert.ok(emitted.includes('import FullscreenToggle from "@/components/workstation/fullscreen-toggle";'), name);
  }
});

/* ------------------------------ navigation ------------------------------ */

test("existing workstation navigation is intact", () => {
  const receptionist = { receptionist: true } as Partial<ReceptionRoles>;
  const roles = {
    cashier: false, accountant: false, manager: false, system_administrator: false,
    emergency_authorizer: false, front_desk_nurse: false, insurance_officer: false, doctor: false,
    lab_technician: false, radiology_technician: false, radiologist: false, pharmacist: false, nurse: false,
    ...receptionist,
  } as ReceptionRoles;
  assert.deepEqual(
    frontOfHouseNavItems(roles, FRONT_DESK_ROUTE).map((item) => item.href),
    [FRONT_DESK_ROUTE, ADMISSIONS_ROUTE],
  );
  for (const path of ["app/front-desk/layout.tsx", "app/admissions/layout.tsx"]) {
    const emitted = code(read(path));
    assert.ok(emitted.includes("<WorkstationNav items={navItems}"), path);
    assert.ok(emitted.indexOf("<WorkstationNav") < emitted.indexOf("<FullscreenToggle />"), path);
  }
});

/**
 * Admissions Desk display helpers. Run with `npm test` (node:test, no DOM).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// TYPE-ONLY: erased at runtime, so no path alias has to resolve.
import type {
  AdmissionClearance,
  AdmissionEncounter,
  BedBoardRow,
} from "@/types/admissions-desk";

import {
  ACTIVE_LANE_KEY,
  ADMISSION_ACTIVE_LANE_ORDER,
  ADMISSION_LANE_ORDER,
  ALL_LANE_KEY,
  admissionPath,
  ageSexLabel,
  bedsPath,
  billingMarker,
  clearanceLabel,
  emptyQueueMessage,
  encounterLabel,
  formatLengthOfStay,
  groupBeds,
  initials,
  laneCode,
  laneLabel,
  laneParam,
  locationLabel,
  resolveSelection,
  worklistPath,
} from "./admissions-desk-format.ts";

const TYPES_SOURCE = readFileSync(new URL("../types/admissions-desk.ts", import.meta.url), "utf8");
const SERIALIZER_SOURCE = readFileSync(
  new URL(
    "../../../odoo/custom_addons/yoya_emr_api/services/admissions_desk_serializers.py",
    import.meta.url,
  ),
  "utf8",
);

function tsArray(name: string): string[] {
  const match = TYPES_SOURCE.match(new RegExp(`export const ${name} = \\[([\\s\\S]*?)\\] as const`));
  assert.ok(match, `${name} must be declared in types/admissions-desk.ts`);
  return [...match[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

function pyTuple(name: string): string[] {
  const match = SERIALIZER_SOURCE.match(new RegExp(`^${name} = \\(([\\s\\S]*?)\\)`, "m"));
  assert.ok(match, `${name} must be declared in admissions_desk_serializers.py`);
  return [...match[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

/* ------------------------------ vocabulary ------------------------------ */

test("lane order agrees across the format file, the types file and the server", () => {
  assert.deepEqual([...ADMISSION_LANE_ORDER], tsArray("ADMISSION_LANES"));
  assert.deepEqual([...ADMISSION_LANE_ORDER], pyTuple("LANE_ORDER"));
  assert.deepEqual([...ADMISSION_ACTIVE_LANE_ORDER], tsArray("ADMISSION_ACTIVE_LANES"));
  assert.deepEqual([...ADMISSION_ACTIVE_LANE_ORDER], pyTuple("ACTIVE_LANES"));
});

test("every lane has a label and a text code", () => {
  for (const lane of ADMISSION_LANE_ORDER) {
    assert.notEqual(laneLabel(lane), lane, lane);
    assert.ok(laneCode(lane).length > 0);
  }
  assert.equal(laneLabel("needs_review"), "Needs review");
  assert.equal(laneLabel(ACTIVE_LANE_KEY), "Open work");
  assert.equal(laneLabel(ALL_LANE_KEY), "All");
});

/* ------------------------------ values ------------------------------ */

test("length of stay", () => {
  assert.equal(formatLengthOfStay(null), "—");
  assert.equal(formatLengthOfStay({ days: 3, hours: 4, ongoing: true }), "3d 4h");
  assert.equal(formatLengthOfStay({ days: 0, hours: 0, ongoing: false }), "0d 0h");
});

test("age and sex", () => {
  assert.equal(ageSexLabel(null), "—");
  assert.equal(ageSexLabel({ id: 1, name: "x", mrn: null, age: 22, gender: "male" }), "22y M");
  assert.equal(ageSexLabel({ id: 1, name: "x", mrn: null, age: null, gender: null }), "—");
});

test("location prefers codes and survives missing parts", () => {
  assert.equal(
    locationLabel({
      ward: { id: 1, code: "MED-WARD", name: "Medical Ward" },
      room: { id: 1, code: "R101", name: "Room 101" },
      bed: { id: 1, code: "BED-101A", name: "Bed 101-A", state: "available" },
    }),
    "MED-WARD / R101 / BED-101A",
  );
  assert.equal(locationLabel({ ward: { id: 1, code: null, name: "Ward" }, room: null, bed: null }), "Ward");
  assert.equal(locationLabel({ ward: null, room: null, bed: null }), "No location");
});

/* ------------------------------ legacy and clearance ------------------------------ */

const LEGACY_ENCOUNTER: AdmissionEncounter = {
  available: false, restricted: false, legacy: true, reference: null,
  state: null, state_label: null, type: null, type_label: null,
};

test("a legacy admission's visit reads as legacy, never as a fabricated one", () => {
  assert.equal(encounterLabel(LEGACY_ENCOUNTER), "No linked visit (legacy admission)");
  assert.equal(
    encounterLabel({ ...LEGACY_ENCOUNTER, legacy: false, available: true, restricted: true }),
    "Visit linked — not visible to your role",
  );
  assert.equal(
    encounterLabel({
      available: true, restricted: false, legacy: false, reference: "ENC0001",
      state: "active", state_label: "Active", type: "inpatient", type_label: "Inpatient",
    }),
    "ENC0001 · Inpatient · Active",
  );
});

test("a NULL clearance verdict is never shown as cleared", () => {
  const legacy: AdmissionClearance = {
    billing_blocked: null,
    clearance_state: "not_applicable",
    clearance_message: "This admission is not linked to a visit, so its financial clearance cannot be determined.",
    admission_clearance_required: false,
    discharge_clearance_required: false,
  };
  const label = clearanceLabel(legacy);
  assert.equal(label.tone, "neutral");
  assert.doesNotMatch(label.text, /cleared\.$/i);
  assert.equal(billingMarker(null), "N/A");
  assert.equal(billingMarker(false), null);
  assert.equal(billingMarker(true), "CLEARANCE");

  assert.equal(clearanceLabel({ ...legacy, billing_blocked: false, clearance_state: "cleared", clearance_message: null }).tone, "ok");
  const blocked = clearanceLabel({ ...legacy, billing_blocked: true, clearance_state: "pending", clearance_message: "Pending." });
  assert.equal(blocked.tone, "warn");
  assert.equal(blocked.text, "Pending.");
});

/* ------------------------------ selection and grouping ------------------------------ */

test("selection keeps a listed row, else falls back to the first", () => {
  const rows = [{ id: 4 }, { id: 9 }];
  assert.equal(resolveSelection(rows, 9), 9);
  assert.equal(resolveSelection(rows, 5), 4);
  assert.equal(resolveSelection([], 5), null);
});

function bed(id: number, ward: number, room: number): BedBoardRow {
  return {
    id, code: `B${id}`, name: `Bed ${id}`, type: "standard", state: "available", state_label: "Available",
    active: true, occupied: false,
    ward: { id: ward, code: `W${ward}`, name: `Ward ${ward}` },
    room: { id: room, code: `R${room}`, name: `Room ${room}` },
    can_view_admission: false, admission: null, flags: [], needs_review: false,
  };
}

test("beds group ward > room in server order", () => {
  const grouped = groupBeds([bed(1, 1, 10), bed(2, 1, 10), bed(3, 1, 11), bed(4, 2, 20)]);
  assert.deepEqual(grouped.map((w) => w.ward?.id), [1, 2]);
  assert.deepEqual(grouped[0].rooms.map((r) => r.room?.id), [10, 11]);
  assert.deepEqual(grouped[0].rooms[0].beds.map((b) => b.id), [1, 2]);
});

test("initials only from a name the server sent", () => {
  assert.equal(initials("Ketema Zeleke"), "KZ");
  assert.equal(initials(null), "");
});

test("empty census message fits the scope", () => {
  assert.equal(emptyQueueMessage("all_wards", "admitted"), "No admitted admissions.");
  assert.equal(emptyQueueMessage("permitted_departments", "active"), "No open work admissions on your wards.");
  assert.equal(emptyQueueMessage("own_patients", "discharged"), "No discharged admissions for your patients.");
});

/* ------------------------------ BFF paths ------------------------------ */

test("every path is a BFF path; the active lane defers to the server default", () => {
  assert.equal(laneParam(ACTIVE_LANE_KEY), null);
  assert.equal(worklistPath({ lane: ACTIVE_LANE_KEY, wardId: null, q: null }), "/api/admissions/worklist");
  assert.equal(
    worklistPath({ lane: "discharged", wardId: 3, q: "ADM 1" }),
    "/api/admissions/worklist?lane=discharged&ward_id=3&q=ADM+1",
  );
  assert.equal(worklistPath({ lane: ALL_LANE_KEY, wardId: null, q: null }), "/api/admissions/worklist?lane=all");
  assert.equal(admissionPath(7), "/api/admissions/7");
  assert.equal(bedsPath({ wardId: null }), "/api/admissions/beds");
  assert.equal(bedsPath({ wardId: 2, state: "occupied" }), "/api/admissions/beds?ward_id=2&state=occupied");
});

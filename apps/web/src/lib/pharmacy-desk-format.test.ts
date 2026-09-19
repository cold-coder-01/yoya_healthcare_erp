/**
 * Pharmacy Desk display helpers. Run with `npm test` (node:test, no DOM).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// TYPE-ONLY: erased at runtime, so no path alias has to resolve.
import type {
  PharmacyDispenseDetail,
  PharmacyDispenseLine,
  PharmacyQueueRow,
  PharmacyWorklistSummary,
} from "@/types/pharmacy-desk";

import {
  ACTIVE_LANE_KEY,
  PHARMACY_ACTIVE_LANE_ORDER,
  PHARMACY_LANE_ORDER,
  PHARMACY_SESSION_PATH,
  clearanceNotice,
  deskRoleLabel,
  detailIsLoading,
  dispensePath,
  laneCountLabel,
  laneStatuses,
  lineProgressLabel,
  matchesSearch,
  medicineLabel,
  pharmacyLaneCode,
  pharmacyLaneLabel,
  qtyLabel,
  reasonNotice,
  resolveSelection,
  stockLabel,
  visibleDetail,
  worklistPath,
} from "./pharmacy-desk-format.ts";

const TYPES_SOURCE = readFileSync(new URL("../types/pharmacy-desk.ts", import.meta.url), "utf8");
const SERIALIZER_SOURCE = readFileSync(
  new URL(
    "../../../odoo/custom_addons/yoya_emr_api/services/pharmacy_desk_serializers.py",
    import.meta.url,
  ),
  "utf8",
);

function constFromTypes(name: string): string[] {
  const match = TYPES_SOURCE.match(new RegExp(`export const ${name} = \\[([^\\]]*)\\]`));
  assert.ok(match, `${name} must be declared in types/pharmacy-desk.ts`);
  return [...match[1].matchAll(/"([^"]+)"/g)].map((entry) => entry[1]);
}

function pyTuple(name: string): string[] {
  const match = SERIALIZER_SOURCE.match(new RegExp(`${name} = \\(([^)]*)\\)`));
  assert.ok(match, `${name} must be declared in the serializer`);
  return [...match[1].matchAll(/"([^"]+)"/g)].map((entry) => entry[1]);
}

function row(overrides: Partial<PharmacyQueueRow> = {}): PharmacyQueueRow {
  return {
    id: 1,
    dispense_code: "DISP00001",
    state: "draft",
    state_label: "Draft",
    lane: "awaiting_preparation",
    lane_label: "Awaiting preparation",
    reason: null,
    reason_message: null,
    priority: "routine",
    priority_label: "Routine",
    dispense_date: "2026-09-18T08:00:00",
    patient: { id: 5, name: "Abebe Kebede", mrn: "MRN-001", age: 34, gender: "male" },
    prescription: { id: 9, code: "RX0009", state: "confirmed", state_label: "Confirmed", date: "2026-09-18" },
    prescriber: { id: 3, name: "Dr. Hana" },
    billing_blocked: false,
    stock_short: false,
    line_count: 2,
    lines_complete: 0,
    medicines_summary: "Amoxicillin · Paracetamol",
    ...overrides,
  };
}

function line(overrides: Partial<PharmacyDispenseLine> = {}): PharmacyDispenseLine {
  return {
    id: 1,
    medicine: { id: 2, name: "Amoxicillin", code: "AMX", strength: "500mg", dosage_form: "capsule", dosage_form_label: "Capsule" },
    dosage: "500mg",
    frequency: "tid",
    duration: "5 days",
    route: "Oral",
    instruction: null,
    prescribed_quantity: 15,
    intended_quantity: 10,
    delivered_quantity: 5,
    consumed_quantity: 5,
    remaining_quantity: 10,
    pending_increment: 5,
    billing_mapped: true,
    charge_linked: true,
    inventory_mapped: true,
    stock_basis: "increment",
    stock_sufficient: true,
    ...overrides,
  };
}

test("the lane vocabularies agree across browser, types and server", () => {
  assert.deepEqual([...PHARMACY_ACTIVE_LANE_ORDER], constFromTypes("PHARMACY_DESK_ACTIVE_LANES"));
  assert.deepEqual([...PHARMACY_LANE_ORDER], constFromTypes("PHARMACY_DESK_LANES"));
  assert.deepEqual([...PHARMACY_ACTIVE_LANE_ORDER], pyTuple("PHARMACY_DESK_ACTIVE_LANES"));
});

test("every lane has a label and a text code", () => {
  for (const lane of [ACTIVE_LANE_KEY, ...PHARMACY_LANE_ORDER]) {
    assert.notEqual(pharmacyLaneLabel(lane), lane === ACTIVE_LANE_KEY ? "" : lane);
    assert.ok(pharmacyLaneCode(lane).length >= 3);
  }
  assert.equal(pharmacyLaneCode("anomaly"), "REV");
  assert.equal(pharmacyLaneLabel("something_new"), "something_new");
});

test("lane statuses: All active asks for every active lane, a lane for itself", () => {
  assert.deepEqual([...laneStatuses(ACTIVE_LANE_KEY)], [...PHARMACY_ACTIVE_LANE_ORDER]);
  assert.deepEqual([...laneStatuses("completed")], ["completed"]);
  assert.deepEqual([...laneStatuses("bogus")], [...PHARMACY_ACTIVE_LANE_ORDER]);
});

test("lane counts come from the server summary, unknown is a dash", () => {
  const summary = { awaiting_preparation: 3, active: 7, blocked: null } as unknown as PharmacyWorklistSummary;
  assert.equal(laneCountLabel(summary, "awaiting_preparation"), "3");
  assert.equal(laneCountLabel(summary, ACTIVE_LANE_KEY), "7");
  assert.equal(laneCountLabel(summary, "blocked"), "—");
  assert.equal(laneCountLabel(null, "completed"), "—");
});

test("the clearance notice is amount-free and only for its lane", () => {
  const notice = clearanceNotice(row({ lane: "awaiting_clearance", billing_blocked: true }));
  assert.ok(notice);
  assert.doesNotMatch(notice, /\d/);
  assert.equal(clearanceNotice(row({ lane: "ready_to_validate" })), null);
});

test("anomaly and blocked render the server's sentence, others nothing", () => {
  const message = "This dispense is marked dispensed, but its delivered and consumed quantities do not show the full prescription handed over.";
  assert.deepEqual(reasonNotice(row({ lane: "anomaly", reason: "dispensed_unreconciled", reason_message: message })), { tone: "red", text: message });
  assert.equal(reasonNotice(row({ lane: "blocked", reason: "stock_insufficient", reason_message: null }))?.tone, "amber");
  assert.equal(reasonNotice(row({ lane: "anomaly", reason_message: null }))?.tone, "red");
  assert.equal(reasonNotice(row({ lane: "completed" })), null);
});

test("quantities are formatted as quantities, never money", () => {
  assert.equal(qtyLabel(10), "10");
  assert.equal(qtyLabel(2.5), "2.5");
  assert.equal(qtyLabel(0.1234), "0.123");
  assert.equal(qtyLabel(null), "—");
});

test("line helpers", () => {
  assert.equal(medicineLabel(line()), "Amoxicillin 500mg");
  assert.equal(medicineLabel(line({ medicine: null })), "Unknown medicine");
  assert.equal(stockLabel(line()), "In stock for intended");
  assert.equal(stockLabel(line({ stock_sufficient: false, stock_basis: "remaining" })), "Short for remaining");
  assert.equal(stockLabel(line({ inventory_mapped: false })), "Not mapped");
  assert.equal(stockLabel(line({ stock_basis: null, stock_sufficient: null })), "—");
  assert.equal(lineProgressLabel(row({ lines_complete: 1, line_count: 2 })), "1 / 2");
});

test("role label prefers the counter role", () => {
  assert.equal(deskRoleLabel({ pharmacist: true, manager: true, system_admin: false }), "Pharmacist");
  assert.equal(deskRoleLabel({ pharmacist: false, manager: true, system_admin: true }), "System Administrator");
  assert.equal(deskRoleLabel({ pharmacist: false, manager: true, system_admin: false }), "Manager");
  assert.equal(deskRoleLabel(null), null);
});

test("local search narrows on identity fields only", () => {
  assert.ok(matchesSearch(row(), "abebe"));
  assert.ok(matchesSearch(row(), "RX0009"));
  assert.ok(matchesSearch(row(), "paracetamol"));
  assert.ok(matchesSearch(row(), "hana"));
  assert.ok(!matchesSearch(row(), "nothing-like-this"));
  assert.ok(matchesSearch(row(), "   "));
});

test("selection and detail are derived", () => {
  const rows = [row({ id: 1 }), row({ id: 2 })];
  assert.equal(resolveSelection(rows, 2), 2);
  assert.equal(resolveSelection(rows, 99), 1);
  assert.equal(resolveSelection([], 1), null);
  const detail = { ...row({ id: 2 }), lines: [] } as unknown as PharmacyDispenseDetail;
  assert.equal(visibleDetail(detail, 2), detail);
  assert.equal(visibleDetail(detail, 1), null);
  assert.equal(detailIsLoading(null, true, null), false);
  assert.equal(detailIsLoading(2, true, detail), false);
  assert.equal(detailIsLoading(2, true, null), true);
});

test("every path is a BFF path", () => {
  assert.equal(PHARMACY_SESSION_PATH, "/api/pharmacy/session");
  assert.equal(worklistPath({}), "/api/pharmacy/worklist");
  assert.equal(
    worklistPath({ status: ["blocked", "anomaly"], q: " Abebe ", date: "2026-09-18", limit: 50 }),
    "/api/pharmacy/worklist?status=blocked%2Canomaly&date=2026-09-18&q=Abebe&limit=50",
  );
  assert.equal(worklistPath({ q: "   " }), "/api/pharmacy/worklist");
  assert.equal(dispensePath(7), "/api/pharmacy/dispenses/7");
});

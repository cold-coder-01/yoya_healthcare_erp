/**
 * ADMISSIONS QUICK PREVIEW: a read-only, one-viewport summary built ONLY from
 * the AdmissionDetail the desk already holds.
 *
 * Run with `npm test`. The view model is exercised directly with fixtures; the
 * dialog, the panel wiring and the reusable primitives are held at the source
 * (no DOM test stack exists).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { AdmissionDetail } from "@/types/admissions-desk";

import { NOT_VISIBLE, buildAdmissionPreview } from "./admission-preview.ts";
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

const ref = (id: number, code: string) => ({ id, code, name: code });

function detail(overrides: Partial<AdmissionDetail> = {}): AdmissionDetail {
  return {
    id: 41,
    reference: "ADM/2026/0041",
    state: "transferred",
    state_label: "Transferred",
    workflow_revision: 7,
    can_admit: false,
    can_transfer: true,
    can_cancel_request: false,
    medical_discharge_ready: true,
    lane: "discharge_pending",
    lane_label: "Ready for discharge",
    review_reasons: [],
    patient: { id: 9, name: "Almaz Tesfaye", mrn: "MRN-000912", age: 47, gender: "female" },
    physician: { id: 3, name: "Dr. Bekele" },
    location: {
      ward: ref(2, "SURG-WARD"),
      room: ref(20, "SR201"),
      bed: { ...ref(201, "BED-201A"), state: "occupied" },
    },
    admitted_at: "2026-09-20 08:15:00",
    expected_discharge_at: null,
    discharged_at: null,
    length_of_stay: { days: 4, hours: 3, ongoing: true },
    encounter: {
      available: true, restricted: false, legacy: false, reference: "ENC/2026/0555",
      state: "in_progress", state_label: "In progress", type: "inpatient", type_label: "Inpatient",
    },
    transfer_count: 2,
    billing_blocked: false,
    admission_reason: "Post-operative observation.",
    diagnosis: { id: 5, name: "Acute appendicitis", code: "K35", restricted: false },
    bed_ownership: { bed_state: "occupied", bed_records_this_admission: true, consistent: true },
    transfers: [
      // Newest first, as the server orders them.
      {
        id: 12, transferred_at: "2026-09-22 10:00:00",
        from: { ward: ref(1, "MED-WARD"), room: ref(11, "R103"), bed: ref(113, "BED-103B") },
        to: { ward: ref(2, "SURG-WARD"), room: ref(20, "SR201"), bed: ref(201, "BED-201A") },
        transferred_by: "Nurse Hana", reason: "Surgery",
      },
      {
        id: 11, transferred_at: "2026-09-21 09:00:00",
        from: { ward: ref(1, "MED-WARD"), room: ref(10, "R102"), bed: ref(102, "BED-102A") },
        to: { ward: ref(1, "MED-WARD"), room: ref(11, "R103"), bed: ref(113, "BED-103B") },
        transferred_by: "Nurse Hana", reason: "Isolation",
      },
    ],
    nursing: {
      rounds: { count: 6, latest_at: "2026-09-24 06:00:00" },
      notes: { count: 3, latest_at: "2026-09-23 20:00:00" },
      care_plans: { count: 1, latest_at: "2026-09-20 09:00:00" },
      medication_administrations: { count: 14, latest_at: "2026-09-24 07:00:00" },
    },
    linkages: {
      procedure_requests: { count: 1, latest_at: "2026-09-22 11:00:00" },
      inventory_movements: { count: 4, latest_at: null },
      pending_laboratory_requests: 2,
      pending_radiology_requests: 0,
    },
    clearance: {
      billing_blocked: false, clearance_state: "cleared", clearance_message: null,
      admission_clearance_required: true, discharge_clearance_required: true,
    },
    financial: {
      financial_state: "due", billing_blocked: false, settlement_required: true, refund_due: false,
      review_reasons: [],
    },
    discharge: {
      medical_ready: true,
      medical_ready_at: "2026-09-24 07:30:00",
      medical_ready_by: "Dr. Bekele",
      blocking: [{ code: "lab_unfinished", message: "Laboratory work unfinished" }],
      warnings: [{ code: "pharmacy_unfinished", message: "Pharmacy dispense unfinished" }],
      can_finalize_discharge: false,
    },
    ...overrides,
  };
}

const PANEL = read("components/admissions/admission-detail-panel.tsx");
const WORKSTATION = read("components/admissions/admissions-workstation.tsx");
const DIALOG = read("components/admissions/admission-preview-dialog.tsx");
const PRIMITIVES = read("components/workstation/workstation-preview.tsx");
const MODEL = read("lib/admission-preview.ts");
const PREVIEW_SOURCES: ReadonlyArray<readonly [string, string]> = [
  ["admission-preview-dialog.tsx", DIALOG],
  ["workstation-preview.tsx", PRIMITIVES],
  ["admission-preview.ts", MODEL],
];

/* 1. button */

test("1. Preview is offered on every loaded admission, first in the action row", () => {
  const panel = code(PANEL);
  assert.ok(panel.includes("{onRequestPreview ? ("));
  assert.ok(panel.includes("onClick={onRequestPreview}"));
  // Not gated on any capability or affordance: it renders only this payload.
  const block = panel.slice(panel.indexOf("{onRequestPreview ? ("), panel.indexOf("{mayAdmit && detail.can_admit"));
  assert.doesNotMatch(block, /may[A-Z]|can_|capabilities/);
  assert.ok(block.includes(">\n              Preview\n"));
  assert.ok(code(WORKSTATION).includes("onRequestPreview={openPreview}"));
});

/* 2. correct patient */

test("2. Preview renders the SELECTED admission the pane shows, and nothing it fetched", () => {
  const workstation = code(WORKSTATION);
  assert.ok(workstation.includes("{previewOpen && shownDetail ? ("));
  assert.ok(workstation.includes("<AdmissionPreviewDialog detail={shownDetail} onClose={closePreview} />"));
  assert.ok(code(DIALOG).includes("buildAdmissionPreview(detail)"));
  const preview = buildAdmissionPreview(detail());
  assert.equal(preview.title, "Almaz Tesfaye");
  assert.equal(buildAdmissionPreview(detail({ patient: { id: 2, name: "Other Patient", mrn: null, age: null, gender: null } })).title, "Other Patient");
});

/* 3. identity, location, status */

test("3. identity, location and status render", () => {
  const preview = buildAdmissionPreview(detail());
  assert.deepEqual(preview.identifiers, [
    { label: "MRN", value: "MRN-000912" },
    { label: "Admission", value: "ADM/2026/0041" },
    { label: "Encounter", value: "ENC/2026/0555" },
    { label: "Age / sex", value: "47y F" },
  ]);
  assert.equal(preview.lane, "discharge_pending");
  assert.equal(preview.stateLabel, "Transferred");
  const location = Object.fromEntries(preview.location.map((field) => [field.label, field.value]));
  assert.equal(location.Ward, "SURG-WARD");
  assert.equal(location.Room, "SR201");
  assert.equal(location.Bed, "BED-201A");
  assert.equal(location["Bed state"], "Occupied");
  assert.equal(location["Length of stay"], "4d 3h");
  assert.notEqual(location.Admitted, "—");
  assert.ok(code(DIALOG).includes("<AdmissionLanePill lane={preview.lane} />"));
});

/* 4. counters */

test("4. the operational counters render, and a withheld one says so", () => {
  const preview = buildAdmissionPreview(detail());
  const work = Object.fromEntries(preview.work.map((item) => [item.label, item.value]));
  assert.deepEqual(work, {
    "Nursing rounds": "6", "Nursing notes": "3", "Care plans": "1", "Medication admin.": "14",
    Procedures: "1", "Pending lab": "2", "Pending imaging": "0", Transfers: "2",
  });
  const hidden = buildAdmissionPreview(detail({
    nursing: { rounds: null, notes: null, care_plans: null, medication_administrations: null },
    linkages: { procedure_requests: null, inventory_movements: null, pending_laboratory_requests: null, pending_radiology_requests: null },
  }));
  for (const item of hidden.work.filter((entry) => entry.label !== "Transfers")) {
    assert.equal(item.value, "—", item.label);
    assert.equal(item.note, NOT_VISIBLE, item.label);
  }
});

/* 5, 6. readiness and warnings */

test("5. medical readiness renders in the server's terms", () => {
  assert.equal(buildAdmissionPreview(detail()).discharge.label, "Medically ready — awaiting administrative discharge");
  const notReady = detail({
    discharge: { ...detail().discharge!, medical_ready: false, medical_ready_at: null, medical_ready_by: null },
  });
  assert.equal(buildAdmissionPreview(notReady).discharge.label, "Not medically ready");
  assert.equal(buildAdmissionPreview(detail({ discharge: null, state: "draft", lane: "draft" })).discharge.label, "Not applicable");
});

test("6. blocking checks and warnings render, apart", () => {
  const { discharge } = buildAdmissionPreview(detail());
  assert.deepEqual(discharge.blocking, [{ key: "lab_unfinished", text: "Laboratory work unfinished" }]);
  assert.deepEqual(discharge.warnings, [{ key: "pharmacy_unfinished", text: "Pharmacy dispense unfinished" }]);
  const dialog = code(DIALOG);
  assert.ok(dialog.includes('title="Blocking" items={preview.discharge.blocking}'));
  assert.ok(dialog.includes('title="Warnings" items={preview.discharge.warnings}'));
});

/* 7, 8. financial */

test("7. the financial section is one of the six state words", () => {
  const cases = [
    ["covered", "Covered"], ["due", "Payment required"], ["refundable", "Refund due"],
    ["pending", "Pending"], ["needs_review", "Needs review"], ["not_applicable", "Not applicable"],
  ] as const;
  for (const [state, label] of cases) {
    const preview = buildAdmissionPreview(detail({ financial: { ...detail().financial, financial_state: state } }));
    assert.equal(preview.financial.label, label, state);
  }
});

test("8. no monetary field or value is introduced", () => {
  const money = /actual_delivered|prepayment|remaining_due|refundable_amount|amount|payer_amount|price|rate\b|currency|\bETB\b|Br\b/i;
  for (const [name, source] of PREVIEW_SOURCES) {
    assert.doesNotMatch(code(source).replace(/Refund due/g, ""), money, name);
  }
  // The rendered model holds no digits in the financial section at all.
  const financial = JSON.stringify(buildAdmissionPreview(detail()).financial);
  assert.doesNotMatch(financial, /\d/);
  // Even a payload smuggling a figure is never read.
  const smuggled = detail({ financial: { ...detail().financial, ...({ remaining_due: 1234.5 } as object) } });
  assert.doesNotMatch(JSON.stringify(buildAdmissionPreview(smuggled)), /1234/);
});

/* 9, 12. redaction */

test("9. Reception's diagnosis redaction is kept", () => {
  const redacted = buildAdmissionPreview(detail({ diagnosis: { id: null, name: null, code: null, restricted: true } }));
  assert.equal(redacted.clinical.diagnosis.value, NOT_VISIBLE);
  assert.equal(redacted.clinical.diagnosis.muted, true);
  assert.doesNotMatch(JSON.stringify(redacted), /appendicitis|K35/);
});

test("12. a Ward Nurse gains nothing: what the detail API hides stays hidden", () => {
  const scoped = buildAdmissionPreview(detail({
    diagnosis: { id: null, name: null, code: null, restricted: true },
    encounter: { available: true, restricted: true, legacy: false, reference: null, state: null, state_label: null, type: null, type_label: null },
    nursing: { rounds: null, notes: null, care_plans: null, medication_administrations: null },
  }));
  assert.equal(scoped.identifiers.find((item) => item.label === "Encounter")?.value, NOT_VISIBLE);
  assert.equal(scoped.clinical.diagnosis.value, NOT_VISIBLE);
  assert.ok(scoped.work.filter((item) => item.label.startsWith("Nursing")).every((item) => item.value === "—"));
  // No second source: no fetch, no BFF path, no Odoo address anywhere in the preview.
  for (const [name, source] of PREVIEW_SOURCES) {
    assert.doesNotMatch(code(source), /fetch\(|\/api\/|yoya-emr|admissionPath|useSWR|axios/, name);
  }
});

/* 10. no mutation */

test("10. opening and closing Preview mutates nothing and cannot submit", () => {
  for (const [name, source] of PREVIEW_SOURCES) {
    const emitted = code(source);
    assert.doesNotMatch(emitted, /method:\s*["'](POST|PUT|PATCH|DELETE)["']|<form|type="submit"|onSubmit/, name);
  }
  const workstation = code(WORKSTATION);
  assert.ok(workstation.includes("const openPreview = useCallback(() => setPreviewOpen(true), []);"));
  assert.ok(workstation.includes("const closePreview = useCallback(() => setPreviewOpen(false), []);"));
  // X, Close and Escape all route to onClose.
  const primitives = code(PRIMITIVES);
  assert.ok(primitives.includes('aria-label="Close preview"'));
  assert.ok(primitives.includes('if (event.key === "Escape") onClose();'));
  assert.equal((primitives.match(/onClick=\{onClose\}/g) ?? []).length, 2);
  assert.ok(primitives.includes('role="dialog"') && primitives.includes('aria-modal="true"'));
});

/* 11. coexistence */

test("11. Preview shares the row with Admit, Transfer, Finalize and Cancel, whose gates are unchanged", () => {
  const panel = code(PANEL);
  const row = panel.slice(panel.indexOf('<div className="mt-2 flex flex-wrap items-center gap-2">'), panel.indexOf("{actionMessage ? ("));
  const order = ["onRequestPreview ? (", "{mayAdmit && detail.can_admit && onRequestAdmit ? (",
    "{mayTransfer && detail.can_transfer && onRequestTransfer ? (",
    "{mayDischarge && detail.discharge?.can_finalize_discharge && onRequestFinalize ? (",
    "{mayCancelRequest && detail.can_cancel_request && onRequestCancel ? ("];
  let last = -1;
  for (const marker of order) {
    const at = row.indexOf(marker);
    assert.ok(at > last, marker);
    last = at;
  }
  // The preview dialog is independent of every workflow dialog's gate.
  assert.ok(code(WORKSTATION).includes("{finalizeOpen && shownDetail && mayDischarge ? ("));
});

/* 13, 14. shell */

const roles = (overrides: Partial<ReceptionRoles>): ReceptionRoles => ({
  receptionist: false, cashier: false, accountant: false, manager: false,
  system_administrator: false, emergency_authorizer: false, front_desk_nurse: false,
  insurance_officer: false, doctor: false, lab_technician: false,
  radiology_technician: false, radiologist: false, pharmacist: false, nurse: false,
  ...overrides,
});

test("13. Front Desk | Admissions navigation is intact", () => {
  assert.deepEqual(
    frontOfHouseNavItems(roles({ receptionist: true }), ADMISSIONS_ROUTE).map((item) => item.href),
    [FRONT_DESK_ROUTE, ADMISSIONS_ROUTE],
  );
  assert.deepEqual(frontOfHouseNavItems(roles({ nurse: true }), ADMISSIONS_ROUTE), []);
  assert.ok(code(read("app/admissions/layout.tsx")).includes('<WorkstationNav items={navItems} accent="sky" />'));
});

test("14. the stale READ ONLY badge does not return", () => {
  assert.doesNotMatch(code(read("app/admissions/layout.tsx")), /read only/i);
});

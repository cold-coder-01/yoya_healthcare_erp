/**
 * DOCTOR DESK DISCHARGE REVIEW: "Review discharge" opens a modal wizard, not
 * an inline card in the consultation strip (where a long summary was clipped).
 *
 * Run with `npm test`. The view model is exercised directly with fixtures; the
 * dialog and the card wiring are held at the source (no DOM test stack exists).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { DoctorAdmissionSummary } from "@/types/admissions-desk";

import { DISCHARGE_REVIEW_ACTION, buildDischargeReview } from "./doctor-discharge-review.ts";

function code(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const CARD = code("components/doctor/doctor-admission-card.tsx");
const DIALOG = code("components/doctor/doctor-discharge-review-dialog.tsx");

const ref = (id: number, code: string) => ({ id, code, name: code });

const ADMISSION: NonNullable<DoctorAdmissionSummary["admission"]> = {
  id: 41,
  reference: "ADM/2026/0041",
  state: "admitted",
  state_label: "Admitted",
  workflow_revision: 6,
  requested_at: "2026-09-28 07:00:00",
  admitted_at: "2026-09-28 09:30:00",
  location: {
    ward: ref(2, "MED-WARD"),
    room: ref(20, "MR201"),
    bed: { ...ref(201, "BED-201A"), state: "occupied" },
  },
  length_of_stay: { days: 5, hours: 2, ongoing: true },
  medical_discharge_at: null,
  discharged_at: null,
};

const WARNINGS = [
  { code: "radiology_open", message: "Imaging on this visit is not finished." },
  { code: "pharmacy_open", message: "A Pharmacy dispense on this visit is not finished." },
];

const CONTEXT = {
  patientName: "Tesema Getahun",
  patientMrn: "MRN-000777",
  encounterName: "ENC/2026/0912",
  physicianName: "Dr. Bekele",
};

/** A realistic, long summary: forty numbered lines. */
const LONG_SUMMARY = Array.from(
  { length: 40 },
  (_, i) => `${i + 1}. Day ${i + 1}: afebrile, tolerating oral intake, wound clean and dry, mobilising independently.`,
).join("\n");

test("the review names patient, MRN, encounter and admission in the header", () => {
  const review = buildDischargeReview(ADMISSION, WARNINGS, CONTEXT, LONG_SUMMARY);
  assert.equal(review.title, "Review discharge");
  assert.deepEqual(review.identifiers, [
    { label: "Patient", value: "Tesema Getahun" },
    { label: "MRN", value: "MRN-000777" },
    { label: "Encounter", value: "ENC/2026/0912" },
    { label: "Admission", value: "ADM/2026/0041" },
  ]);
});

test("the admission context: location, admitted, length of stay, physician", () => {
  const review = buildDischargeReview(ADMISSION, [], CONTEXT, "Home.");
  const byLabel = Object.fromEntries(review.admissionContext.map((f) => [f.label, f.value]));
  assert.equal(byLabel["Ward / room / bed"], "MED-WARD / MR201 / BED-201A");
  assert.notEqual(byLabel["Admitted"], "—");
  assert.equal(byLabel["Length of stay"], "5d 2h");
  assert.equal(byLabel["Responsible physician"], "Dr. Bekele");
});

test("absent identity stays a dash, never invented", () => {
  const review = buildDischargeReview(
    { ...ADMISSION, admitted_at: null, length_of_stay: null },
    [],
    { patientName: null, patientMrn: null, encounterName: null, physicianName: null },
    "Home.",
  );
  for (const field of [...review.identifiers.slice(0, 3), ...review.admissionContext.slice(1)]) {
    assert.equal(field.value, "—", field.label);
  }
});

test("a long discharge summary is carried in full, first line to last", () => {
  const review = buildDischargeReview(ADMISSION, WARNINGS, CONTEXT, LONG_SUMMARY);
  assert.equal(review.summary, LONG_SUMMARY);
  assert.ok(review.summary.startsWith("1. Day 1:"));
  assert.ok(review.summary.endsWith("mobilising independently."));
  assert.ok(review.summary.includes("40. Day 40:"));
  // And the dialog renders it unclamped, wrapped, with no horizontal overflow.
  const block = DIALOG.slice(DIALOG.indexOf('aria-label="Discharge summary"'));
  const classes = block.slice(0, block.indexOf(">"));
  assert.match(classes, /whitespace-pre-wrap/);
  assert.match(classes, /break-words/);
  assert.doesNotMatch(classes, /line-clamp|truncate|max-h-/);
  assert.ok(DIALOG.includes("{review.summary}"));
});

test("revision, readiness action and warnings are carried through", () => {
  const review = buildDischargeReview(ADMISSION, WARNINGS, CONTEXT, "Home.");
  assert.equal(review.revision, 6);
  assert.equal(review.action, DISCHARGE_REVIEW_ACTION);
  assert.deepEqual(review.warnings, [
    { key: "radiology_open", text: "Imaging on this visit is not finished." },
    { key: "pharmacy_open", text: "A Pharmacy dispense on this visit is not finished." },
  ]);
});

test("Review discharge opens the modal; no inline review in the card", () => {
  assert.ok(CARD.includes('setStep("discharge_confirm");'));
  const confirm = CARD.slice(CARD.indexOf('{step === "discharge_confirm" && admission && cleanedDischarge ? ('));
  assert.ok(confirm.trimStart().startsWith('{step === "discharge_confirm"'));
  assert.ok(confirm.includes("<DoctorDischargeReviewDialog"));
  // The old inline review is gone from the card entirely.
  assert.doesNotMatch(CARD, /aria-label="Confirm discharge request"/);
  assert.doesNotMatch(CARD, /Declare this patient medically ready for discharge\?/);
  assert.doesNotMatch(CARD, /<dl[^>]*>[\s\S]*?\{cleanedDischarge\}/);
  // A real modal: dialog role, aria-modal, labelled by its title.
  assert.ok(DIALOG.includes('role="dialog"'));
  assert.ok(DIALOG.includes('aria-modal="true"'));
  assert.ok(DIALOG.includes("aria-labelledby={titleId}"));
  assert.ok(DIALOG.includes("fixed inset-0"));
});

test("the dialog reuses the shared workstation primitives, not a new modal system", () => {
  assert.ok(DIALOG.includes('from "@/components/workstation/workstation-preview"'));
  for (const name of ["PreviewHeader", "PreviewSection", "PreviewField", "PreviewWarning"]) {
    assert.ok(DIALOG.includes(`<${name}`), name);
  }
});

test("constrained height; the BODY is the one scroll and opens at the top", () => {
  assert.match(DIALOG, /max-h-\[88vh\]/);
  const body = DIALOG.slice(DIALOG.indexOf("ref={bodyRef}"));
  const classes = body.slice(0, body.indexOf(">"));
  assert.match(classes, /min-h-0 flex-1 overflow-y-auto overflow-x-hidden/);
  assert.ok(DIALOG.includes("bodyRef.current.scrollTop = 0"));
  // Header and footer never scroll away.
  assert.equal((DIALOG.match(/shrink-0 items-(start|center)[^"]*border-(b|t) border-slate-200/g) ?? []).length, 2);
  // Mounted only while reviewing, so every open is a fresh scroll position.
  assert.ok(CARD.includes('{step === "discharge_confirm" && admission && cleanedDischarge ? ('));
});

test("Go back, X and Escape return to the editor and keep the summary", () => {
  assert.ok(CARD.includes('const closeDischargeReview = useCallback(() => setStep("discharge_write"), []);'));
  assert.ok(CARD.includes("onClose={closeDischargeReview}"));
  // Closing never clears the typed summary.
  const close = CARD.slice(CARD.indexOf("const closeDischargeReview"), CARD.indexOf("if (!summary) return null;"));
  assert.doesNotMatch(close, /setDischargeSummary/);
  assert.ok(DIALOG.includes('aria-label="Close discharge review"'));
  assert.ok(DIALOG.includes('if (event.key === "Escape" && !busy) onClose();'));
  assert.equal((DIALOG.match(/onClick=\{onClose\}/g) ?? []).length, 2, "X and Go back");
  assert.ok(DIALOG.includes(">\n            Go back\n"));
  // Focus starts inside the dialog, and Tab cannot escape it.
  assert.ok(DIALOG.includes("backRef.current?.focus();"));
  assert.ok(DIALOG.includes('if (event.key !== "Tab") return;'));
  // Nothing closes mid-request.
  assert.equal((DIALOG.match(/disabled=\{busy\}/g) ?? []).length, 3);
});

test("Request discharge stays purple, pointer-only, and calls the existing mutation", () => {
  const footer = DIALOG.slice(DIALOG.indexOf("<footer"));
  assert.match(footer, /bg-violet-700 px-3 cl-meta font-bold text-white hover:bg-violet-800/);
  assert.ok(footer.includes('{busy ? "Requesting…" : "Request discharge"}'));
  assert.ok(footer.includes("if (event.detail === 0) return;"));
  // No fetch, path or token in the dialog: the card's mutation is the only one.
  assert.doesNotMatch(DIALOG, /fetch\(|dischargeRequestPath|operation_token|tokenFor/);
  assert.ok(CARD.includes("onConfirm={() => void requestDischarge()}"));
  assert.equal((CARD.match(/fetch\(dischargeRequestPath\(appointmentId\)/g) ?? []).length, 1);
});

test("success closes the modal and shows the server's medical-readiness state", () => {
  const request = CARD.slice(CARD.indexOf("async function requestDischarge()"), CARD.indexOf("async function cancelRequest()"));
  const success = request.slice(request.indexOf("if (payload && payload.success) {"));
  // The server's returned summary replaces the visit's, so the status pill
  // reads "Medically ready - awaiting administrative discharge" at once.
  assert.ok(success.includes("setReturned({ basis: initial, value: payload.data.admission });"));
  assert.ok(success.indexOf('setStep("idle");') > -1, "step leaves discharge_confirm: the modal unmounts");
  assert.ok(CARD.includes("{summary.status_label}"));
});

test("warnings render inside the modal and never block", () => {
  assert.ok(DIALOG.includes("review.warnings.length ?"));
  assert.ok(DIALOG.includes('<PreviewWarning tone="warn"'));
  assert.ok(DIALOG.includes("items={review.warnings}"));
  const confirm = DIALOG.slice(DIALOG.indexOf("<footer"));
  assert.doesNotMatch(confirm, /warnings/);
  assert.ok(CARD.includes("summary.discharge_warnings,"));
});

test("a request notice shows in the dialog, not behind it", () => {
  assert.ok(CARD.includes("notice={notice}"));
  assert.ok(CARD.includes('{notice && step !== "discharge_confirm" ? ('));
  assert.ok(DIALOG.includes("{notice.text}"));
});

/**
 * DECISION 3: an open consultation blocks discharge, and the Admissions Desk
 * names the doctor who holds it.
 *
 * Run with `npm test`. The wording helpers are exercised directly; the three
 * components are held at the source (no DOM test stack exists).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { needsReload } from "./admissions-desk-actions.ts";
import {
  CONSULTATION_OPEN_CODE,
  admissionsDeskHoldText,
  consultationHoldFromPayload,
  doctorDeskHoldText,
  parseConsultationHold,
} from "./consultation-discharge-gate.ts";

function code(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const HOLD = {
  reference: "CONS00033",
  doctor_name: "Dr. Hana Bekele",
  started_at: "2026-09-16T07:58:59",
  archived: false,
};

const ERROR_409 = {
  success: false,
  error: {
    code: CONSULTATION_OPEN_CODE,
    message:
      "This visit's consultation is still open. The doctor must complete it before the patient can be discharged.",
    consultation: HOLD,
  },
};

test("a 409 admission_consultation_open carries the holder", () => {
  assert.deepEqual(consultationHoldFromPayload(ERROR_409), HOLD);
});

test("another error code carries no holder", () => {
  const other = { success: false, error: { ...ERROR_409.error, code: "admission_settlement_required" } };
  assert.equal(consultationHoldFromPayload(other), null);
  assert.equal(consultationHoldFromPayload(null), null);
  assert.equal(consultationHoldFromPayload({ success: true, data: {} }), null);
});

test("an unusable holder is ignored, archived defaults to false", () => {
  assert.equal(parseConsultationHold({}), null);
  assert.equal(parseConsultationHold("CONS1"), null);
  assert.equal(parseConsultationHold({ reference: "CONS1", archived: "yes" })?.archived, false);
});

test("the Admissions Desk names the consultation and the holding doctor", () => {
  const text = admissionsDeskHoldText(HOLD);
  assert.match(text, /CONS00033/);
  assert.match(text, /held by Dr\. Hana Bekele/);
  assert.match(text, /must complete it before discharge/);
  assert.doesNotMatch(text, /archived/);
});

test("an archived consultation needs an administrator to restore it", () => {
  const text = admissionsDeskHoldText({ ...HOLD, archived: true });
  assert.match(text, /archived but not completed/);
  assert.match(text, /administrator must restore it/);
  assert.match(text, /Dr\. Hana Bekele/);
});

test("without a holder the Admissions Desk still says what blocks", () => {
  assert.match(admissionsDeskHoldText(null), /consultation is still open/);
});

test("the Doctor Desk is told to complete it, never that it is done for them", () => {
  const text = doctorDeskHoldText(HOLD);
  assert.match(text, /Complete the consultation/);
  assert.match(text, /never completed automatically/);
  assert.match(doctorDeskHoldText({ ...HOLD, archived: true }), /administrator to restore it/);
});

test("the stale screen reloads after the refusal so the holder is shown", () => {
  assert.equal(needsReload(CONSULTATION_OPEN_CODE), true);
});

test("all three desks render the holder from the server's data", () => {
  const panel = code("components/admissions/admission-detail-panel.tsx");
  assert.match(panel, /admissionsDeskHoldText\(check\.consultation \?\? null\)/);

  const workstation = code("components/admissions/admissions-workstation.tsx");
  assert.match(workstation, /admissionsDeskHoldText\(consultationHoldFromPayload\(payload\)\)/);

  const card = code("components/doctor/doctor-admission-card.tsx");
  assert.match(card, /doctorDeskHoldText\(consultationHoldFromPayload\(payload\)\)/);
  assert.match(card, /summary\?\.discharge_blocking\?\.length/);
});

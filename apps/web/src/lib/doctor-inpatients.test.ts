/**
 * Doctor Desk: the Inpatients lane and "search all my visits" (decisions 8, 9).
 *
 * Run with `npm test`. The rules are exercised directly; the workstation, the
 * bucket bar and the two BFF routes are held at the source (no DOM test stack).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { DoctorInpatient, DoctorQueueRow } from "@/types/doctor";

import { DOCTOR_BUCKETS } from "./doctor-format.ts";
import {
  DATELESS_SEARCH_PROMPT,
  INPATIENTS_LANE,
  INPATIENTS_PATH,
  emptyQueueText,
  inpatientConsultationText,
  inpatientDischargeText,
  inpatientWhereText,
  inpatientsByVisit,
  matchesSearch,
  truncatedText,
  visitSource,
} from "./doctor-inpatients.ts";

function code(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const ROW = {
  appointment_id: 10012,
  appointment_code: "APP10013",
  appointment_date: "2026-09-16 07:50:42",
  state: "in_consultation",
  patient: { id: 7, name: "Selam Tesfaye", mrn: "HMS11834", age: 30, gender: "female" },
} as unknown as DoctorQueueRow;

function inpatient(overrides: Partial<DoctorInpatient> = {}): DoctorInpatient {
  return {
    admission_id: 2,
    reference: "ADM00001",
    state: "admitted",
    state_label: "Admitted",
    patient: { id: 7, name: "Selam Tesfaye", mrn: "HMS11834", age: 30, gender: "female" },
    admitted_at: "2026-09-21 14:52:40",
    location: {
      ward: { id: 1, code: null, name: "Medical Ward" },
      room: { id: 2, code: null, name: "Room 101" },
      bed: { id: 3, code: null, name: "Bed 101-A", state: "occupied" },
    },
    length_of_stay: { days: 52, hours: 3, ongoing: true },
    consultation: {
      reference: "CONS00033",
      doctor_name: "Dr. Hana Bekele",
      started_at: "2026-09-16 07:58:59",
      archived: false,
    },
    discharge: {
      medically_ready: false,
      medical_ready_at: null,
      blocking: [
        { code: "not_medically_ready", message: "The doctor has not declared the patient medically ready." },
        { code: "consultation_open", message: "The visit's consultation is still open." },
      ],
      warnings: [],
      can_request_discharge: false,
    },
    ...overrides,
  } as DoctorInpatient;
}

/* ---------------- where the list comes from ---------------- */

test("a date set keeps today's behaviour exactly: the day's queue", () => {
  assert.deepEqual(visitSource("2026-11-13", ""), {
    kind: "day",
    path: "/api/doctor/queue?date=2026-11-13",
  });
  // The search box still only narrows the loaded day; it never changes the call.
  assert.deepEqual(visitSource("2026-11-13", "selam"), visitSource("2026-11-13", ""));
});

test("a cleared date searches every date once the term has two letters", () => {
  assert.deepEqual(visitSource("", "se"), { kind: "search", path: "/api/doctor/visit-search?q=se" });
  assert.equal(visitSource("", "  Selam T ").kind, "search");
  assert.match((visitSource("", "Selam T") as { path: string }).path, /q=Selam%20T$/);
});

test("a cleared date with a short term fetches nothing and never sends an empty date", () => {
  for (const term of ["", " ", "s", " s "]) {
    assert.deepEqual(visitSource("", term), { kind: "prompt" });
  }
  assert.equal(DATELESS_SEARCH_PROMPT, "Type at least 2 letters to search all your visits");
  assert.equal(emptyQueueText("prompt").body, DATELESS_SEARCH_PROMPT);
});

/* ---------------- the browser's filter only narrows ---------------- */

test("the day and the inpatient lane filter in the browser; search mode does not", () => {
  assert.equal(matchesSearch(ROW, "hms118", "day"), true);
  assert.equal(matchesSearch(ROW, "nobody", "day"), false);
  // The server already matched (consultation references included).
  assert.equal(matchesSearch(ROW, "CONS00033", "search"), true);
  // The lane matches the admission and consultation references too.
  assert.equal(matchesSearch(ROW, "adm00001", INPATIENTS_LANE, inpatient()), true);
  assert.equal(matchesSearch(ROW, "cons00033", INPATIENTS_LANE, inpatient()), true);
  assert.equal(matchesSearch(ROW, "", "day"), true);
});

/* ---------------- the inpatient row ---------------- */

test("the row names the bed, the stay and the open consultation", () => {
  const stay = inpatient();
  assert.equal(inpatientWhereText(stay), "Medical Ward / Room 101 / Bed 101-A · 52d 3h");
  assert.match(inpatientConsultationText(stay) ?? "", /^CONS00033 open since /);
  assert.equal(inpatientConsultationText(inpatient({ consultation: null })), null);
  assert.equal(
    inpatientConsultationText(
      inpatient({ consultation: { ...stay.consultation!, archived: true } }),
    ),
    "CONS00033 archived, not completed",
  );
});

test("discharge status: not ready with the blocking reasons, or medically ready", () => {
  const notReady = inpatientDischargeText(inpatient());
  assert.equal(notReady.ready, false);
  assert.equal(notReady.label, "Not medically ready");
  assert.equal(notReady.reasons.length, 2);

  const ready = inpatientDischargeText(
    inpatient({
      discharge: {
        medically_ready: true,
        medical_ready_at: "2026-11-13 08:00:00",
        blocking: [],
        warnings: [],
        can_request_discharge: false,
      },
    }),
  );
  assert.deepEqual(ready, { ready: true, label: "Medically ready", reasons: [] });
});

test("rows are keyed by their visit so selecting one opens that visit", () => {
  const map = inpatientsByVisit([{ visit: ROW, inpatient: inpatient() }]);
  assert.equal(map.get(10012)?.reference, "ADM00001");
});

test("empty and truncated states speak for their source", () => {
  assert.equal(emptyQueueText(INPATIENTS_LANE).body, "No current inpatients under your care.");
  assert.match(truncatedText("search"), /newest matches only/);
  assert.match(truncatedText(INPATIENTS_LANE), /Inpatient limit reached/);
  assert.match(truncatedText("day"), /Queue limit reached/);
});

/* ---------------- held at the source ---------------- */

test("the Inpatients lane is not a bucket: the day's buckets are unchanged", () => {
  assert.deepEqual(
    DOCTOR_BUCKETS.map((bucket) => bucket.key),
    ["all", "wait", "review", "open", "finished"],
  );
  const bar = code("components/doctor/doctor-bucket-bar.tsx");
  assert.match(bar, /onChange\(INPATIENTS_LANE\)/);
  assert.match(bar, /inpatientCount \?\? "–"/);
});

test("the workstation loads inpatients without the date and never sends an empty one", () => {
  const ws = code("components/doctor/doctor-workstation.tsx");
  assert.ok(!ws.includes("/api/doctor/queue?date=${date}"), "the raw date is sent again");
  assert.match(ws, /visitSource\(date, debouncedSearch\)/);
  assert.match(ws, /fetch\(INPATIENTS_PATH/);
  assert.match(ws, /\}, \[refreshToken, inpatientToken\]\);/);
  assert.match(ws, /onAdmissionChanged=\{reloadInpatients\}/);
  assert.equal(INPATIENTS_PATH, "/api/doctor/inpatients");
});

test("the date field starts on today and clearing it offers a way back", () => {
  const ws = code("components/doctor/doctor-workstation.tsx");
  assert.match(ws, /useState\(hospitalToday\(\)\)/);
  const filters = code("components/doctor/doctor-filters.tsx");
  assert.match(filters, /onDateChange\(hospitalToday\(\)\)/);
  assert.match(filters, /DATELESS_SEARCH_PROMPT/);
});

test("both BFF routes forward to their own Odoo endpoint and add no scope", () => {
  const inpatients = code("app/api/doctor/inpatients/route.ts");
  assert.match(inpatients, /`\$\{DOCTOR_API\}\/inpatients\$\{url\.search\}`/);
  const search = code("app/api/doctor/visit-search/route.ts");
  assert.match(search, /`\$\{DOCTOR_API\}\/visit-search\$\{url\.search\}`/);
  for (const body of [inpatients, search]) {
    assert.ok(!body.includes("doctor_id"), "a BFF route names a doctor");
  }
});

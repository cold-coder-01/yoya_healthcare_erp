/**
 * Doctor Desk: the Inpatients lane and "search all my visits".
 *
 * Business decisions 8 and 9 (docs/business-decisions.md). Pure rules and
 * wording only. Odoo decides who is an inpatient of whom, which visits a search
 * may reach and whether discharge is blocked; the browser picks which endpoint
 * to call and names what the server returned.
 */
// TYPE-ONLY imports, so node:test runs this with no resolver.
import type { DoctorBucket } from "./doctor-format";
import type { DoctorInpatient, DoctorInpatientRow, DoctorQueueRow } from "@/types/doctor";

import { formatLengthOfStay, locationLabel } from "./admissions-desk-format.ts";
import { formatHospitalDateTime } from "./clinical-format.ts";

/** The lane beside All / Wait / Review / Open / Finished. Not a bucket: it
 *  ignores the date and has its own source. */
export const INPATIENTS_LANE = "inpatients";
export type DoctorLane = DoctorBucket | typeof INPATIENTS_LANE;

export const DOCTOR_SEARCH_MIN_LENGTH = 2;
export const DATELESS_SEARCH_PROMPT = "Type at least 2 letters to search all your visits";

/**
 * Where the visit list comes from.
 *
 *   day      a date is set: today's behaviour exactly (/queue?date=...).
 *   search   the date is cleared and the term is long enough: every date.
 *   prompt   the date is cleared and the term is too short: nothing is
 *            fetched. The browser NEVER sends an empty date (which Odoo reads
 *            as "today" while the field looks blank).
 *
 * The Inpatients lane has its own source (/api/doctor/inpatients) and is
 * loaded whatever this returns.
 */
export type VisitSource =
  | { kind: "day"; path: string }
  | { kind: "search"; path: string }
  | { kind: "prompt" };

export function visitSource(date: string, search: string): VisitSource {
  const day = date.trim();
  if (day) {
    return { kind: "day", path: `/api/doctor/queue?date=${encodeURIComponent(day)}` };
  }
  const term = search.trim();
  if (term.length >= DOCTOR_SEARCH_MIN_LENGTH) {
    return { kind: "search", path: `/api/doctor/visit-search?q=${encodeURIComponent(term)}` };
  }
  return { kind: "prompt" };
}

export const INPATIENTS_PATH = "/api/doctor/inpatients";

function haystack(row: DoctorQueueRow, inpatient?: DoctorInpatient | null): string {
  return [
    row.patient.name,
    row.patient.mrn ?? "",
    row.appointment_code ?? "",
    inpatient?.reference ?? "",
    inpatient?.consultation?.reference ?? "",
  ]
    .join(" ")
    .toLowerCase();
}

/**
 * The browser's text filter. It only ever NARROWS rows Odoo returned. In
 * search mode the server already matched the term (including consultation
 * references the row does not carry), so the browser does not filter again.
 */
export function matchesSearch(
  row: DoctorQueueRow,
  term: string,
  source: VisitSource["kind"] | typeof INPATIENTS_LANE,
  inpatient?: DoctorInpatient | null,
): boolean {
  const needle = term.trim().toLowerCase();
  if (!needle || source === "search") return true;
  return haystack(row, inpatient).includes(needle);
}

/** appointment_id -> the stay, for the queue to render the inpatient line. */
export function inpatientsByVisit(rows: DoctorInpatientRow[]): Map<number, DoctorInpatient> {
  return new Map(rows.map((row) => [row.visit.appointment_id, row.inpatient]));
}

/** "Medical Ward / R101 / Bed 101-A · 52d 3h". */
export function inpatientWhereText(inpatient: DoctorInpatient): string {
  return `${locationLabel(inpatient.location)} · ${formatLengthOfStay(inpatient.length_of_stay)}`;
}

/** "CONS00033 open since …", "… archived, not completed", or null. */
export function inpatientConsultationText(inpatient: DoctorInpatient): string | null {
  const hold = inpatient.consultation;
  if (!hold) return null;
  const subject = hold.reference ?? "Consultation";
  if (hold.archived) return `${subject} archived, not completed`;
  return hold.started_at
    ? `${subject} open since ${formatHospitalDateTime(hold.started_at, "—")}`
    : `${subject} open`;
}

/** Discharge status for the row: ready, or not ready and why. */
export function inpatientDischargeText(inpatient: DoctorInpatient): {
  ready: boolean;
  label: string;
  reasons: string[];
} {
  const { discharge } = inpatient;
  const reasons = discharge.blocking
    .filter((check) => !(discharge.medically_ready && check.code === "not_medically_ready"))
    .map((check) => check.message);
  if (discharge.medically_ready) {
    return { ready: true, label: "Medically ready", reasons };
  }
  return { ready: false, label: "Not medically ready", reasons };
}

/** The list's empty state, by where its rows come from. */
export function emptyQueueText(
  source: VisitSource["kind"] | typeof INPATIENTS_LANE,
): { title: string; body: string } {
  if (source === INPATIENTS_LANE) {
    return { title: "No current inpatients", body: "No current inpatients under your care." };
  }
  if (source === "prompt") {
    return { title: "Search all your visits", body: DATELESS_SEARCH_PROMPT };
  }
  if (source === "search") {
    return { title: "No visits found", body: "None of your visits on any date match this search." };
  }
  return { title: "No patients here", body: "Nothing matches the current filters." };
}

/** The truncation banner, by source. */
export function truncatedText(source: VisitSource["kind"] | typeof INPATIENTS_LANE): string {
  if (source === "search") return "Showing the newest matches only. Refine your search.";
  if (source === INPATIENTS_LANE) return "Inpatient limit reached. Not every inpatient is shown.";
  return "Queue limit reached. Narrow the filters to see every patient.";
}

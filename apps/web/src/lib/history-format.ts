/**
 * Longitudinal history display decisions.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * is authorization: which episodes exist, which are visible and which carry a
 * released result are all decided by Slice 9A whatever this file says.
 *
 * THE THREE DECISIONS THIS FILE OWNS
 * ----------------------------------
 *   1. AN UNRECORDED VITAL IS NOT ZERO. The server already converts the
 *      unrecorded-zero to null; this file's job is never to convert it back.
 *      `vitalsLine` omits a null rather than printing "0", because a printed
 *      SpO2 of 0% is not a missing reading, it is a dead patient.
 *
 *   2. A COUNT OF ZERO IS NOT AN INDICATOR. A row that renders
 *      "Dx 0 · Lab 0 · Rad 0 · Rx 0" is a dashboard, not a chart line. Only
 *      non-zero counts earn a chip.
 *
 *   3. AN EMPTY SECTION IS NOT RENDERED. The detail viewer asks this file
 *      whether a section has anything to say before it draws a heading, so a
 *      prior episode with no imaging shows no imaging heading at all.
 */
// TYPE-ONLY, and it has to stay that way -- TypeScript erases these, which is
// what lets node:test run history-format.test.ts with no resolver and no
// transform. A runtime import from `@/types/...` would make this untestable.
import type {
  DoctorHistoryDetailResponse,
  HistoryTriage,
  HistoryVisit,
  HistoryVisitCounts,
} from "@/types/doctor-history";

/**
 * The visit states in which a doctor holds an ACTIVE care relationship.
 *
 * MIRRORS Slice 9A's ACTIVE_CARE_APPOINTMENT_STATES, which is the authority.
 * Restated here rather than derived, because the browser cannot see the
 * server's constant; the contract test below the fold in history-contract
 * pins the two values so a change on either side is a visible edit.
 *
 * THIS IS AN AFFORDANCE, NOT AUTHORIZATION, and the distinction is the whole
 * reason it is safe to have a copy of the policy on this side at all. It
 * decides only whether the desk OFFERS the History tab. Whether the records
 * may actually be read is decided by Odoo's record rules on every request, and
 * a browser that asked anyway would get the same flat 404 as a stranger. If
 * these two values ever drift, the failure mode is a tab that opens onto
 * "not available" -- never a tab that shows something it should not.
 */
export const ACTIVE_CARE_VISIT_STATES = ["confirmed", "in_consultation"] as const;

/**
 * Whether the History tab is worth offering for a visit in this state.
 *
 * WHY `confirmed` MATTERS HERE. Longitudinal access opens as soon as the visit
 * is confirmed -- before triage, before clearance, before the consultation is
 * started. That is deliberate on the server: a doctor about to see a patient
 * benefits from their past precisely while deciding what to do, and reading a
 * record is not an act that needs financial clearance. The desk was hiding it
 * until consultation started, which made an authorized surface unreachable.
 *
 * `done` is absent for the same reason the server excludes it: access LAPSES
 * when the episode closes. A completed visit's History tab is left exactly as
 * it was -- it renders, and the server answers whether it may still be read.
 */
export function hasActiveCareRelationship(
  visitState: string | null | undefined,
): boolean {
  return (ACTIVE_CARE_VISIT_STATES as readonly string[]).includes(
    visitState ?? "",
  );
}

/** Shown when the patient has no qualifying prior episode at all. */
export const NO_HISTORY_TEXT =
  "No prior clinical history is available for this patient.";

/**
 * Shown when the history API refuses.
 *
 * ONE SENTENCE FOR EVERY REFUSAL, deliberately. Slice 9A answers 404 for an
 * episode that does not exist, one belonging to another patient, and one the
 * caller may no longer reach because the care relationship lapsed. Rendering
 * three different messages would tell a curious reader which of those it was,
 * and re-open the disclosure the flat 404 exists to close.
 */
export const HISTORY_UNAVAILABLE_TEXT =
  "This patient's history is not available.";

/** Shown when one opened episode cannot be loaded. */
export const EPISODE_UNAVAILABLE_TEXT = "This visit is no longer available.";

/** Shown inside an opened episode that recorded no narrative. */
export const NO_NOTE_TEXT = "No clinical note was recorded for this visit.";

/** The read-only badge the history viewer carries. */
export const HISTORY_READ_ONLY_TEXT = "Read only";

/**
 * The compact clinical-content chips on an episode row.
 *
 * ZERO COUNTS ARE DROPPED, not rendered as "0". A chart line should say what
 * happened at that visit, and "Rad 0" says nothing a doctor can use.
 *
 * ORDERED BY WHAT A DOCTOR SCANS FOR: what was concluded, then what was
 * measured, then what was given.
 */
export function contentChips(counts: HistoryVisitCounts | null | undefined) {
  if (!counts) return [];
  const chips: { key: string; label: string }[] = [];
  const push = (key: string, label: string, value: number) => {
    if (value > 0) chips.push({ key, label: `${label} ${value}` });
  };
  push("diagnoses", "Dx", counts.diagnoses);
  push("laboratory", "Lab", counts.laboratory);
  push("radiology", "Rad", counts.radiology);
  push("medications", "Rx", counts.medications);
  push("images", "Img", counts.images);
  return chips;
}

/**
 * The abnormality note on an episode row, or null.
 *
 * CRITICAL WINS, AND IT CARRIES ITS WORD. The server counts both from the
 * laboratory's own `abnormal_flag`; nothing here reads a value. The word is
 * present so the signal survives for a doctor who cannot distinguish the
 * colours, or who is reading a printout -- the same rule the Results tab
 * follows.
 *
 * A COUNT, NEVER A VALUE. Which analyte, and what it read, is inside the
 * episode. The row exists to say that opening it is worth the click.
 */
export function abnormalNote(
  counts: HistoryVisitCounts | null | undefined,
): { text: string; tone: "critical" | "warn" } | null {
  if (!counts) return null;
  if (counts.critical_results > 0) {
    return {
      text: `${counts.critical_results} critical`,
      tone: "critical",
    };
  }
  if (counts.abnormal_results > 0) {
    return {
      text: `${counts.abnormal_results} abnormal`,
      tone: "warn",
    };
  }
  return null;
}

export function abnormalNoteClass(tone: "critical" | "warn"): string {
  return tone === "critical"
    ? "border-red-400 bg-red-50 text-red-900"
    : "border-amber-400 bg-amber-50 text-amber-900";
}

/**
 * The provenance line under an episode's date: who saw the patient, where, and
 * in what kind of encounter.
 *
 * EMPTY PARTS ARE DROPPED rather than rendered as a dangling separator; a
 * legacy episode may carry no department, and a placeholder tells a doctor
 * nothing.
 */
export function visitProvenance(visit: {
  doctor?: string | null;
  department?: string | null;
  encounter_type_label?: string | null;
}): string | null {
  const parts = [
    visit.encounter_type_label,
    visit.doctor,
    visit.department,
  ].filter((part): part is string => Boolean(part && part.trim()));
  return parts.length ? parts.join(" · ") : null;
}

/**
 * The headline diagnosis as one line: "Migraine without aura (G43.0)".
 *
 * The code is parenthesised only when there is one; a disease with no ICD code
 * must not render an empty pair of brackets.
 */
export function primaryDiagnosisText(
  visit: Pick<HistoryVisit, "primary_diagnosis">,
): string | null {
  const primary = visit.primary_diagnosis;
  if (!primary || !primary.name) return null;
  return primary.code ? `${primary.name} (${primary.code})` : primary.name;
}

/**
 * The compact vital snapshot: "BP 120/80 · Pulse 83 · Temp 37.2 · SpO2 98%".
 *
 * A NULL IS OMITTED, NEVER ZEROED. See the module header; this is the single
 * most important rule in this file.
 *
 * BLOOD PRESSURE NEEDS BOTH HALVES. A systolic with no diastolic is not a
 * blood pressure, and "120/" is not a reading, so the pair is rendered only
 * when both were recorded.
 */
export function vitalsLine(triage: HistoryTriage | null | undefined): string | null {
  if (!triage) return null;
  const parts: string[] = [];
  if (triage.systolic_bp !== null && triage.diastolic_bp !== null) {
    parts.push(`BP ${triage.systolic_bp}/${triage.diastolic_bp}`);
  }
  if (triage.heart_rate !== null) parts.push(`Pulse ${triage.heart_rate}`);
  if (triage.temperature !== null) parts.push(`Temp ${triage.temperature}`);
  if (triage.respiratory_rate !== null) {
    parts.push(`Resp ${triage.respiratory_rate}`);
  }
  if (triage.spo2 !== null) parts.push(`SpO2 ${triage.spo2}%`);
  if (triage.bmi !== null) parts.push(`BMI ${triage.bmi}`);
  return parts.length ? parts.join(" · ") : null;
}

/** True when the triage record carries at least one recorded value. */
export function hasTriageContent(
  triage: HistoryTriage | null | undefined,
): boolean {
  if (!triage) return false;
  return Boolean(
    vitalsLine(triage) ||
      (triage.chief_complaint && triage.chief_complaint.trim()) ||
      triage.triage_priority_label ||
      triage.pain_level,
  );
}

/**
 * True when the note recorded any narrative at all.
 *
 * A consultation row can exist with every narrative field blank -- the model
 * has no completeness constraint -- and a viewer that drew six empty headings
 * for it would read as a broken screen.
 */
export function hasNoteContent(
  note: DoctorHistoryDetailResponse["note"],
): boolean {
  if (!note) return false;
  return NOTE_FIELDS.some((field) => {
    const value = note[field];
    return Boolean(value && value.trim());
  });
}

/**
 * The narrative fields, in clinical reading order.
 *
 * Restated here rather than imported from the consultation contract because
 * this is a READING order for a finished record, and the editor's order is a
 * WRITING order. They coincide today; tying them together would make a future
 * change to one silently reorder the other.
 */
export const NOTE_FIELDS = [
  "presenting_complaint",
  "history_of_presenting_illness",
  "review_of_systems",
  "examination_findings",
  "assessment",
  "plan",
] as const;

export const NOTE_LABELS: Record<(typeof NOTE_FIELDS)[number], string> = {
  presenting_complaint: "Presenting complaint",
  history_of_presenting_illness: "History of presenting illness",
  review_of_systems: "Review of systems",
  examination_findings: "Examination",
  assessment: "Assessment",
  plan: "Plan",
};

/**
 * THE ONLY URL THE BROWSER EVER BUILDS FOR HISTORICAL IMAGERY.
 *
 * It is a SEPARATE function from imageContentPath, and separate on purpose.
 * Slice 9A serves historical bytes from a dedicated history-scoped route
 * because the current-visit results route must never accept a historical
 * appointment; routing history imagery through the Results path would be
 * exactly the leak that route refuses. Both appointments travel in the URL:
 * the current one proves the care relationship, the historical one names the
 * episode, and Odoo checks the pairing rather than the image id alone.
 *
 * No Odoo origin, no /web/content path and no access token can appear here,
 * because the payload carries no URL at all -- only an id.
 */
export function historyImageContentPath(
  appointmentId: number,
  historicalAppointmentId: number,
  imageId: number,
  disposition?: "attachment",
): string {
  const base =
    `/api/doctor/visits/${appointmentId}` +
    `/history/${historicalAppointmentId}/images/${imageId}`;
  return disposition === "attachment" ? `${base}?disposition=attachment` : base;
}

/**
 * Which detail sections have anything to say.
 *
 * The viewer asks this before it draws a heading, so an episode with no
 * imaging shows no imaging section rather than an empty box. VISIT is always
 * present: an episode always has identity.
 */
export function presentSections(detail: DoctorHistoryDetailResponse | null) {
  if (!detail) return [] as string[];
  const sections: string[] = ["visit"];
  if (hasTriageContent(detail.triage)) sections.push("triage");
  if (hasNoteContent(detail.note)) sections.push("note");
  if (detail.diagnoses.length) sections.push("diagnoses");
  if (detail.medications.length) sections.push("medications");
  if (detail.laboratory.length) sections.push("laboratory");
  if (detail.radiology.length) sections.push("radiology");
  return sections;
}

/**
 * True when the episode carried no clinical content beyond its identity.
 *
 * Slice 9A already refuses to LIST an episode with no clinical substance, so
 * this should be unreachable through the worklist. It is here because the
 * viewer must still say something honest if it ever is reached, rather than
 * render a header over blank space.
 */
export function isEmptyEpisode(detail: DoctorHistoryDetailResponse | null) {
  return presentSections(detail).length <= 1;
}

/**
 * "Showing 2 of 7 visits", or null when everything is on screen.
 *
 * Present only when it tells the doctor something: with every visit loaded,
 * the count is noise.
 */
export function historyProgressText(
  shown: number,
  total: number,
): string | null {
  if (!total || shown >= total) return null;
  return `Showing ${shown} of ${total} visits`;
}

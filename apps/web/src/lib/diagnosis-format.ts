/**
 * Diagnosis display vocabulary and payload construction.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * is authorization: the one-primary rule, the completion freeze and the scope
 * are all enforced by hospital.patient.diagnosis whatever this file says.
 */
// TYPE-ONLY, and it has to stay that way -- TypeScript erases these, which is
// what lets node:test run diagnosis-format.test.ts with no resolver and no
// transform. A runtime import from `@/types/...` would make this untestable.
import type {
  DiagnosisAddRequest,
  DiagnosisForm,
  DiagnosisUpdateRequest,
  DoctorDiagnosis,
} from "@/types/doctor-diagnosis";

/** The section keys the consultation workspace can show. */
export const CONSULTATION_SECTIONS = [
  { key: "note", label: "Note", live: true },
  { key: "diagnosis", label: "Diagnosis", live: true },
  { key: "orders", label: "Orders", live: true },
  { key: "results", label: "Results", live: false },
  { key: "history", label: "History", live: false },
] as const;

export type ConsultationSection = (typeof CONSULTATION_SECTIONS)[number]["key"];

/** True for a section this slice actually implements. */
export function isLiveSection(key: string): boolean {
  return CONSULTATION_SECTIONS.some(
    (section) => section.key === key && section.live,
  );
}

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

const LABELS: Record<string, string> = {
  primary: "Primary",
  secondary: "Secondary",
  differential: "Differential",
  history: "History",
  provisional: "Provisional",
  final: "Final",
  mild: "Mild",
  moderate: "Moderate",
  severe: "Severe",
  critical: "Critical",
  active: "Active",
  resolved: "Resolved",
  chronic: "Chronic",
  suspected: "Suspected",
};

export function diagnosisLabel(value: string | null | undefined, fallback = "—") {
  if (!value) return fallback;
  return LABELS[value] ?? value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * Clinical reading order: the primary first, then supporting diagnoses, then
 * what is still being ruled out. A doctor scanning this list wants the headline
 * at the top, and `history` sorts last because only legacy rows carry it.
 */
const TYPE_ORDER: Record<string, number> = {
  primary: 0,
  secondary: 1,
  differential: 2,
  history: 3,
};

export function sortDiagnoses(rows: DoctorDiagnosis[]): DoctorDiagnosis[] {
  return [...rows].sort((a, b) => {
    const rankA = TYPE_ORDER[a.diagnosis_type ?? ""] ?? 9;
    const rankB = TYPE_ORDER[b.diagnosis_type ?? ""] ?? 9;
    if (rankA !== rankB) return rankA - rankB;
    return a.id - b.id;
  });
}

/** Groups in reading order, empty groups omitted. */
export function groupDiagnoses(rows: DoctorDiagnosis[]) {
  const sorted = sortDiagnoses(rows);
  const order = ["primary", "secondary", "differential", "history"];
  return order
    .map((type) => ({
      type,
      label: diagnosisLabel(type),
      rows: sorted.filter((row) => row.diagnosis_type === type),
    }))
    .filter((group) => group.rows.length > 0);
}

/** Disease name with its code, when the catalogue carries one. */
export function diseaseLabel(row: DoctorDiagnosis): string {
  if (!row.disease) return "Unknown diagnosis";
  return row.disease.code
    ? `${row.disease.name} (${row.disease.code})`
    : row.disease.name;
}

/* ------------------------------------------------------------------ *
 * Payloads
 * ------------------------------------------------------------------ */

export const EMPTY_FORM: DiagnosisForm = {
  diagnosis_type: "secondary",
  certainty: "provisional",
  severity: "",
  status: "active",
  notes: "",
};

/** The form as it stands on an existing row, for the edit control. */
export function formFromDiagnosis(row: DoctorDiagnosis): DiagnosisForm {
  return {
    diagnosis_type: (row.diagnosis_type ?? "secondary") as DiagnosisForm["diagnosis_type"],
    certainty: (row.certainty ?? "provisional") as DiagnosisForm["certainty"],
    severity: row.severity ?? "",
    status: row.status ?? "",
    notes: row.notes ?? "",
  };
}

/**
 * The add body.
 *
 * `request_token` IS ALWAYS SENT. It is what makes a double-clicked Add return
 * the first diagnosis instead of filing a second one -- and, for a primary
 * diagnosis, what stops the retry being answered with "a primary already
 * exists", blaming the doctor for the browser's second request.
 *
 * Empty optional fields are OMITTED rather than sent as "": the server treats
 * an empty string as an explicit clear, which on a create would store a blank
 * where the model's own default belongs.
 */
export function buildAddPayload(
  diseaseId: number,
  form: DiagnosisForm,
  requestToken: string,
): DiagnosisAddRequest {
  const payload: DiagnosisAddRequest = {
    disease_id: diseaseId,
    request_token: requestToken,
    diagnosis_type: form.diagnosis_type,
    certainty: form.certainty,
  };
  if (form.severity) payload.severity = form.severity;
  if (form.status) payload.status = form.status;
  if (form.notes.trim()) payload.notes = form.notes;
  return payload;
}

/**
 * The update body: ONLY what changed.
 *
 * Sending the whole form on every edit would rewrite fields the doctor never
 * touched with whatever this tab happened to load. An empty string IS sent
 * when it differs, because clearing a severity or a note is a real edit.
 */
export function buildUpdatePayload(
  form: DiagnosisForm,
  original: DiagnosisForm,
): DiagnosisUpdateRequest {
  const payload: DiagnosisUpdateRequest = {};
  (Object.keys(form) as (keyof DiagnosisForm)[]).forEach((key) => {
    if (form[key] !== original[key]) payload[key] = form[key];
  });
  return payload;
}

export function isEmptyUpdate(payload: DiagnosisUpdateRequest) {
  return Object.keys(payload).length === 0;
}

/** True when the doctor may still change anything on this consultation. */
export function canEdit(editable: boolean, row?: DoctorDiagnosis) {
  return editable && (row ? row.editable : true);
}

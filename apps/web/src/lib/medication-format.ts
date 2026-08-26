/**
 * Medication prescribing: display vocabulary, staging and payload construction.
 *
 * Pure functions. Nothing here fetches, nothing here writes, and nothing here
 * decides anything clinical or financial: confirmation, the pharmacy dispense,
 * billing, clearance and stock all live in the prescription, pharmacy, billing
 * and inventory models, whatever this file says.
 *
 * A SIBLING OF laboratory-format AND radiology-format, NOT A GENERALISATION.
 * Medication differs from both in the one way that matters to this file: a
 * laboratory or radiology order is a SET OF PICKED ITEMS, so the client stages
 * a list of ids. A prescription is a set of picked items EACH CARRYING ITS OWN
 * CLINICAL INSTRUCTIONS -- dose, route, frequency, duration, quantity -- so the
 * client stages a list of small forms. Folding that into the sibling shape
 * would have meant a parallel map from id to instructions, which is the same
 * data with an extra way to fall out of step.
 */
// TYPE-ONLY, and it has to stay that way -- TypeScript erases these, which is
// what lets node:test run medication-format.test.ts with no resolver and no
// transform.
import type {
  DoctorPrescription,
  MedPrescriptionForm,
  MedPrescriptionRequest,
  MedicineOption,
  MedicineRequestEntry,
  PrescribedMedicine,
  StagedMedicine,
} from "@/types/doctor-medication";

/* ------------------------------------------------------------------ *
 * Vocabulary
 * ------------------------------------------------------------------ */

/**
 * Labels for the status keys the SERVER produces. Restated rather than rendered
 * from `status_label` alone so the client can style each state, but the
 * server's own label is what the UI displays -- these are the fallback and the
 * styling key.
 */
const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  awaiting_pharmacy: "Awaiting pharmacy",
  ready_at_pharmacy: "Ready at pharmacy",
  partially_dispensed: "Partially dispensed",
  dispensed: "Dispensed",
  cancelled: "Cancelled",
};

const ROUTE_LABELS: Record<string, string> = {
  oral: "Oral",
  injection: "Injection",
  topical: "Topical",
  inhalation: "Inhalation",
  eye_drop: "Eye drop",
  ear_drop: "Ear drop",
  nasal: "Nasal",
  other: "Other",
};

const DOSAGE_FORM_LABELS: Record<string, string> = {
  tablet: "Tablet",
  capsule: "Capsule",
  syrup: "Syrup",
  injection: "Injection",
  cream: "Cream",
  ointment: "Ointment",
  drops: "Drops",
  inhaler: "Inhaler",
  solution: "Solution",
  other: "Other",
};

export function medStatusLabel(status: string | null | undefined) {
  if (!status) return STATUS_LABELS.draft;
  return STATUS_LABELS[status] ?? STATUS_LABELS.draft;
}

export function routeLabel(route: string | null | undefined) {
  if (!route) return null;
  return ROUTE_LABELS[route] ?? route;
}

export function dosageFormLabel(form: string | null | undefined) {
  if (!form) return null;
  return DOSAGE_FORM_LABELS[form] ?? form;
}

/** A prescription nobody is waiting on any more. */
export function isTerminalStatus(status: string | null | undefined) {
  return status === "dispensed" || status === "cancelled";
}

/* ------------------------------------------------------------------ *
 * Catalogue display
 * ------------------------------------------------------------------ */

/** "Amoxicillin 500mg" -- the two things that identify a drug at a glance. */
export function medicineLabel(medicine: {
  name: string;
  strength: string | null;
}) {
  return medicine.strength
    ? `${medicine.name} ${medicine.strength}`
    : medicine.name;
}

/**
 * The secondary line under a search hit: what form it comes in, and the other
 * name a prescriber might know it by.
 *
 * The generic name is suppressed when it merely repeats the trade name, which
 * is the common case in this catalogue and would otherwise print every drug
 * twice.
 */
export function medicineContext(medicine: {
  name: string;
  dosage_form: string | null;
  generic_name: string | null;
  brand_name: string | null;
}) {
  const parts: string[] = [];
  const form = dosageFormLabel(medicine.dosage_form);
  if (form) parts.push(form);
  const alt = medicine.generic_name ?? medicine.brand_name;
  if (alt && alt.toLowerCase() !== medicine.name.toLowerCase()) parts.push(alt);
  return parts.join(" · ");
}

/** The dose line of a prescribed medicine: "500mg · Oral · twice daily · 5 days". */
export function prescribedSummary(line: PrescribedMedicine) {
  const parts: string[] = [];
  if (line.dosage) parts.push(line.dosage);
  const route = routeLabel(line.route);
  if (route) parts.push(route);
  if (line.frequency) parts.push(line.frequency);
  if (line.duration) parts.push(line.duration);
  return parts.join(" · ");
}

/**
 * Fulfilment, only where the server was certain of it.
 *
 * Returns null when correlation was ambiguous (see the note on
 * PrescribedMedicine), so the caller renders nothing rather than an empty or
 * misleading progress figure.
 */
export function fulfilment(line: PrescribedMedicine) {
  if (line.dispensed_quantity === null || line.remaining_quantity === null) {
    return null;
  }
  return {
    dispensed: line.dispensed_quantity,
    remaining: line.remaining_quantity,
    complete: line.remaining_quantity === 0,
    started: line.dispensed_quantity > 0,
  };
}

/** "3 medicines" / "1 medicine". */
export function medicineCountLabel(count: number) {
  return count === 1 ? "1 medicine" : `${count} medicines`;
}

export function prescriptionSummary(prescription: DoctorPrescription) {
  return prescription.medicines
    .map((line) => medicineLabel({ name: line.name, strength: line.strength }))
    .join(", ");
}

/* ------------------------------------------------------------------ *
 * Staging
 * ------------------------------------------------------------------ */

export const EMPTY_PRESCRIPTION_FORM: MedPrescriptionForm = {
  diagnosis_id: null,
  notes: "",
};

/**
 * Stage a medicine, seeded from the catalogue where the catalogue can help.
 *
 * `dosage` is pre-filled from the strength because that is what the model's own
 * onchange does when the same order is typed into Odoo, and a prescriber who
 * wants something else overwrites it. `quantity` is deliberately left EMPTY:
 * there is no safe default for how many of something a patient should receive,
 * and pre-filling a number a doctor did not choose is how a wrong quantity gets
 * dispensed.
 *
 * DUPLICATES ARE ALLOWED, unlike the laboratory and radiology pickers. The same
 * drug can legitimately appear twice on one prescription with different
 * instructions, so each staged row gets its own key.
 */
export function stageMedicine(
  staged: StagedMedicine[],
  medicine: MedicineOption,
  key: string,
): StagedMedicine[] {
  return [
    ...staged,
    {
      key,
      medicine,
      quantity: "",
      dosage: medicine.strength ?? "",
      route: "",
      frequency: "",
      duration: "",
      instructions: "",
    },
  ];
}

export function unstageMedicine(staged: StagedMedicine[], key: string) {
  return staged.filter((entry) => entry.key !== key);
}

export function updateStaged(
  staged: StagedMedicine[],
  key: string,
  patch: Partial<Omit<StagedMedicine, "key" | "medicine">>,
): StagedMedicine[] {
  return staged.map((entry) =>
    entry.key === key ? { ...entry, ...patch } : entry,
  );
}

/** The modal's dirty check compares only editable line values. */
export function medicineEditorChanged(
  openedWith: StagedMedicine,
  current: StagedMedicine,
) {
  return (
    openedWith.quantity !== current.quantity ||
    openedWith.dosage !== current.dosage ||
    openedWith.route !== current.route ||
    openedWith.frequency !== current.frequency ||
    openedWith.duration !== current.duration ||
    openedWith.instructions !== current.instructions
  );
}

/** Compact review line for a medicine waiting in the local prescription. */
export function stagedRegimenSummary(entry: StagedMedicine) {
  const parts: string[] = [];
  if (entry.dosage) parts.push(entry.dosage);
  const route = routeLabel(entry.route);
  if (route) parts.push(route);
  if (entry.frequency) parts.push(entry.frequency);
  if (entry.duration) parts.push(entry.duration);
  parts.push(`Qty ${entry.quantity}`);
  return parts.join(" / ");
}

export const MED_STATUS_TONE: Record<string, string> = {
  awaiting_pharmacy: "border-sky-300 bg-sky-50 text-sky-900",
  ready_at_pharmacy: "border-amber-300 bg-amber-50 text-amber-900",
  partially_dispensed: "border-orange-300 bg-orange-50 text-orange-900",
  dispensed: "border-emerald-400 bg-emerald-50 text-emerald-900",
  cancelled: "border-red-300 bg-red-50 text-red-800",
  draft: "border-slate-300 bg-slate-100 text-slate-600",
};

/* ------------------------------------------------------------------ *
 * Validation
 * ------------------------------------------------------------------ */

/**
 * Quantity is the one field a prescription cannot be submitted without.
 *
 * The column has no required=True and no positivity constraint, but the
 * pharmacy workflow does: hospital_billing selects only lines with a positive
 * quantity and refuses the whole dispense when none qualify, so a zero would
 * reach the counter as an order that can never be marked ready. The server
 * refuses it too -- this is the copy of that rule that lets the doctor see it
 * before pressing the button, not a substitute for it.
 */
export function quantityError(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return "Quantity is required.";
  const value = Number(trimmed);
  if (!Number.isFinite(value)) return "Quantity must be a number.";
  if (value <= 0) return "Quantity must be greater than zero.";
  return null;
}

/** Every staged row's quantity problem, keyed by staged row. */
export function stagedErrors(staged: StagedMedicine[]): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const entry of staged) {
    const error = quantityError(entry.quantity);
    if (error) errors[entry.key] = error;
  }
  return errors;
}

export function canSubmitPrescription(staged: StagedMedicine[], busy: boolean) {
  if (busy || staged.length === 0) return false;
  return Object.keys(stagedErrors(staged)).length === 0;
}

/* ------------------------------------------------------------------ *
 * Payload
 * ------------------------------------------------------------------ */

function optionalText(value: string): string | undefined {
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

/**
 * ONE request for N medicines, carrying ONE token.
 *
 * The token identifies the SUBMISSION, not a medicine. Sending one token per
 * staged row would defeat the whole mechanism: a retry would match some rows
 * and not others, and the server would write a second prescription containing
 * whatever had not matched.
 *
 * Empty optional fields are omitted rather than sent as "": the server treats a
 * present-but-empty string as an explicit blank, and there is no reason to
 * transmit "the doctor left this alone" as a value.
 */
export function buildPrescriptionPayload(
  staged: StagedMedicine[],
  form: MedPrescriptionForm,
  token: string,
): MedPrescriptionRequest {
  const medicines: MedicineRequestEntry[] = staged.map((entry) => {
    const item: MedicineRequestEntry = {
      medicine_id: entry.medicine.id,
      quantity: Number(entry.quantity.trim()),
    };
    const dosage = optionalText(entry.dosage);
    if (dosage) item.dosage = dosage;
    if (entry.route) item.route = entry.route;
    const frequency = optionalText(entry.frequency);
    if (frequency) item.frequency = frequency;
    const duration = optionalText(entry.duration);
    if (duration) item.duration = duration;
    const instructions = optionalText(entry.instructions);
    if (instructions) item.instructions = instructions;
    return item;
  });

  const payload: MedPrescriptionRequest = { request_token: token, medicines };
  if (form.diagnosis_id) payload.diagnosis_id = form.diagnosis_id;
  const notes = optionalText(form.notes);
  if (notes) payload.notes = notes;
  return payload;
}

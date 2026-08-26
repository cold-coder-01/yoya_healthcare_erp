/**
 * THE Doctor medication wire contract.
 *
 * Emitted directly by yoya_emr_api's medication serializer; the BFF forwards it
 * unchanged, with no adapter and no reshaping.
 *
 * NOTHING PRICED APPEARS HERE, and for medication that is a sharper rule than
 * it was for laboratory or radiology. hospital.pharmacy.medicine carries a
 * `sale_price` on the record itself, not merely a link to a priced service, so
 * the catalogue type below is an explicit statement of the nine clinical fields
 * that cross the boundary and of nothing else.
 *
 * NO BILLING STATUS EITHER. Laboratory and radiology both expose a
 * `billing_blocked` boolean because both raise their charges when the doctor
 * confirms. Medication raises none until the PHARMACIST marks the dispense
 * ready, so there is usually nothing to be blocked on while a doctor is
 * looking, and the ready state is reported coarsely as `ready_at_pharmacy`.
 *
 * `code` IS NOT VALIDATED as any coding standard. hospital.pharmacy.medicine.
 * code is a free Char, so this layer does not assert a guarantee the schema
 * does not make.
 */
import type { ApiEnvelope } from "./doctor";

export type { ApiEnvelope };

/**
 * The operational status vocabulary, DERIVED by the serializer from the linked
 * pharmacy dispense -- never from prescription.state, which stays `confirmed`
 * forever however much medication has been handed over.
 */
export const MED_STATUSES = [
  "draft",
  "awaiting_pharmacy",
  "ready_at_pharmacy",
  "partially_dispensed",
  "dispensed",
  "cancelled",
] as const;
export type MedStatus = (typeof MED_STATUSES)[number];

/** hospital.prescription.line.route -- the LINE's vocabulary, not the catalogue's. */
export const MED_ROUTES = [
  "oral",
  "injection",
  "topical",
  "inhalation",
  "eye_drop",
  "ear_drop",
  "nasal",
  "other",
] as const;
export type MedRoute = (typeof MED_ROUTES)[number];

/** One catalogue entry. Clinical and reference fields only. */
export type MedicineOption = {
  id: number;
  name: string;
  code: string | null;
  generic_name: string | null;
  brand_name: string | null;
  dosage_form: string | null;
  strength: string | null;
  route: string | null;
  category: string | null;
};

/**
 * One prescribed medicine.
 *
 * `dispensed_quantity` and `remaining_quantity` are NULL when the server could
 * not correlate this line to its dispense line with certainty. Dispense lines
 * carry no prescription_line_id, so correlation is inferred from
 * (medicine, sequence); where two lines of one prescription collapse onto the
 * same pair the server reports null rather than guessing which course was
 * filled. Render progress only when both are numbers.
 */
export type PrescribedMedicine = {
  /** The prescription LINE id. */
  id: number;
  medicine_id: number;
  name: string;
  code: string | null;
  dosage_form: string | null;
  strength: string | null;
  dosage: string | null;
  route: string | null;
  frequency: string | null;
  duration: string | null;
  quantity: number;
  instructions: string | null;
  dispensed_quantity: number | null;
  remaining_quantity: number | null;
};

export type MedPrescriptionDiagnosis = {
  id: number;
  name: string;
  code: string | null;
};

export type DoctorPrescription = {
  id: number;
  prescription_code: string;
  medicines: PrescribedMedicine[];
  diagnosis: MedPrescriptionDiagnosis | null;
  notes: string | null;
  status: string;
  status_label: string;
  ordered_at: string | null;
  created_at: string | null;
  /** False when at least one medicine could not be matched to its dispense line. */
  progress_itemised: boolean;
  /** Always false: the prescribed set freezes when the prescription is confirmed. */
  editable: boolean;
  cancellable: boolean;
};

export type DoctorPrescriptionResponse = {
  prescriptions: DoctorPrescription[];
  /** The consultation is open, so new prescriptions may still be written. */
  can_order: boolean;
};

export type MedCatalogueResponse = {
  medicines: MedicineOption[];
  query: string | null;
  limit: number;
  truncated: boolean;
};

/**
 * One medicine staged in the builder, before the prescription is submitted.
 *
 * `key` is a client-side identity, not a server id: the same medicine may be
 * staged twice on one prescription (a tapering course and a rescue dose are two
 * lines of the same drug), so medicine_id cannot be the React key.
 *
 * `quantity` is a STRING because it is the raw input value. An empty box and a
 * zero are different things to a prescriber, and coercing early would make them
 * the same.
 */
export type StagedMedicine = {
  key: string;
  medicine: MedicineOption;
  quantity: string;
  dosage: string;
  route: string;
  frequency: string;
  duration: string;
  instructions: string;
};

/** What the prescription header holds before submission. */
export type MedPrescriptionForm = {
  diagnosis_id: number | null;
  notes: string;
};

export type MedicineRequestEntry = {
  medicine_id: number;
  quantity: number;
  dosage?: string;
  route?: string;
  frequency?: string;
  duration?: string;
  instructions?: string;
};

export type MedPrescriptionRequest = {
  request_token: string;
  medicines: MedicineRequestEntry[];
  diagnosis_id?: number;
  notes?: string;
};

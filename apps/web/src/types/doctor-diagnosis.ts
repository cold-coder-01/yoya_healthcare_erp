/**
 * THE Doctor diagnosis wire contract.
 *
 * Emitted directly by yoya_emr_api's diagnosis serializer; the BFF routes under
 * app/api/doctor/* forward it unchanged, with no adapter and no reshaping.
 *
 * NO FINANCIAL FIELD EXISTS HERE, and that is deliberate rather than
 * incidental. A diagnosis may later justify an insurance claim; producing that
 * justification is a billing surface's job, working FROM the clinical record.
 * It is not a reason to put money into the clinical record, and it is why the
 * insurance and credit roles hold no access to this model at all.
 *
 * `code` IS NOT VALIDATED AS ICD. hospital.disease.code is a free Char with no
 * coding system, no format check and no uniqueness. The field is named `code`
 * rather than `icd_code` so this layer does not assert a guarantee the schema
 * does not make.
 */
import type { ApiEnvelope } from "./doctor";

export type { ApiEnvelope };

/**
 * The three types a doctor records during a consultation.
 *
 * hospital.patient.diagnosis also defines "history", which the Doctor Desk
 * deliberately does not offer: it describes a past condition being noted, not
 * a diagnosis being made now, and it belongs to the HISTORY section rather
 * than to this one. Legacy rows carrying it still read back fine.
 */
export const DIAGNOSIS_TYPES = ["primary", "secondary", "differential"] as const;
export type DiagnosisType = (typeof DIAGNOSIS_TYPES)[number];

/** Independent of clinical status: Final + Resolved is a normal combination. */
export const CERTAINTIES = ["provisional", "final"] as const;
export type DiagnosisCertainty = (typeof CERTAINTIES)[number];

export const SEVERITIES = ["mild", "moderate", "severe", "critical"] as const;
export type DiagnosisSeverity = (typeof SEVERITIES)[number];

export const STATUSES = ["active", "resolved", "chronic", "suspected"] as const;
export type DiagnosisStatus = (typeof STATUSES)[number];

export type DiseaseOption = {
  id: number;
  name: string;
  code: string | null;
  category: string | null;
};

export type DoctorDiagnosis = {
  id: number;
  disease: DiseaseOption | null;
  /** Widened to string: legacy rows may carry "history", which is not offered. */
  diagnosis_type: string | null;
  certainty: string | null;
  severity: string | null;
  status: string | null;
  notes: string | null;
  diagnosis_date: string | null;
  editable: boolean;
};

export type DoctorDiagnosisResponse = {
  diagnoses: DoctorDiagnosis[];
  /** The consultation is open. Resolved server-side from its state. */
  editable: boolean;
  /** Whether the primary slot is taken, resolved server-side. */
  has_primary: boolean;
};

export type DiseaseCatalogueResponse = {
  diseases: DiseaseOption[];
  query: string | null;
  limit: number;
  /** The result set hit the server cap; there may be more matches. */
  truncated: boolean;
};

/** What the add/edit form holds. Every field optional except the type. */
export type DiagnosisForm = {
  diagnosis_type: DiagnosisType;
  certainty: DiagnosisCertainty;
  severity: string;
  status: string;
  notes: string;
};

export type DiagnosisAddRequest = {
  disease_id: number;
  request_token: string;
} & Partial<Record<keyof DiagnosisForm, string>>;

export type DiagnosisUpdateRequest = Partial<Record<keyof DiagnosisForm, string>>;

/**
 * The one error code this surface reacts to by name.
 *
 * The server refuses a second primary rather than silently demoting the
 * existing one, so the desk has to explain WHICH diagnosis holds the slot and
 * let the doctor decide. Any other code is rendered as its message.
 */
export const DIAGNOSIS_PRIMARY_CONFLICT_CODE = "diagnosis_primary_exists";

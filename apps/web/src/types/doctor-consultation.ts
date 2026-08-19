/**
 * THE Doctor consultation wire contract.
 *
 * Emitted DIRECTLY by yoya_emr_api's consultation serializer
 * (services/consultation_serializers.py). The BFF routes under
 * app/api/doctor/visits/[id]/consultation/* forward it unchanged; there is no
 * adapter and no field is reshaped in JavaScript.
 *
 * WHAT IS ABSENT IS AS DELIBERATE AS WHAT IS PRESENT.
 * There is no amount, balance, receipt, agreement, membership number or payer
 * field anywhere in this file, and no code path that could produce one. A
 * consultation note has no financial dimension; the clearance VERDICT the desk
 * needs already arrives on the visit payload in types/doctor.ts.
 *
 * THE NOTE IS NOT CLINICAL TRUTH UNTIL ODOO HAS IT.
 * Everything here describes a SERVER state. The editor holds a draft, but the
 * draft is a pending edit, never the record: after every save the server's own
 * response replaces it, including a fresh `version`.
 */

import type { ApiEnvelope } from "./doctor";

export type { ApiEnvelope };

/** hospital.consultation.state. Completion is not implemented in this slice. */
export type ConsultationState = "draft" | "completed";

/**
 * The six narrative fields, in the order a consultation is actually conducted.
 *
 * Declared as a const tuple so the field list, the draft shape and the save
 * payload all derive from ONE source. Mirrors NARRATIVE_FIELDS in
 * yoya_clinical_bridge/models/consultation.py, which is the authority.
 */
export const CONSULTATION_NARRATIVE_FIELDS = [
  "presenting_complaint",
  "history_of_presenting_illness",
  "review_of_systems",
  "examination_findings",
  "assessment",
  "plan",
] as const;

export type ConsultationNarrativeField =
  (typeof CONSULTATION_NARRATIVE_FIELDS)[number];

export type DoctorConsultation = {
  id: number;
  name: string;
  state: ConsultationState;
  started_at: string | null;
  completed_at: string | null;
  /**
   * hospital.consultation.version_token() -- the record's own write_date,
   * serialized by the model. Handed back on save and compared under a row
   * lock, which is what stops two clinicians silently overwriting each other.
   * Opaque: never parsed, never compared for ordering, only echoed.
   */
  version: string;
  /** state === 'draft'. An affordance; the model refuses the write regardless. */
  editable: boolean;
} & Record<ConsultationNarrativeField, string | null>;

/**
 * `available` distinguishes "this visit has not started, so there is no note"
 * from "the note exists and happens to be empty". Without it the client would
 * have to infer that from a null and would get it wrong for a consultation
 * whose narrative is genuinely blank.
 */
export type DoctorConsultationResponse = {
  available: boolean;
  reason: string | null;
  consultation: DoctorConsultation | null;
};

/** What the editor holds while the doctor types. Never null, always a string. */
export type ConsultationDraft = Record<ConsultationNarrativeField, string>;

export type ConsultationSaveRequest = {
  version: string;
} & Partial<Record<ConsultationNarrativeField, string>>;

/**
 * Stable error codes this surface reacts to by name rather than by message.
 *
 * `consultation_conflict` is the only one with its own recovery path: the
 * doctor must reload and re-apply, because free-text clinical narrative has no
 * safe automatic merge.
 */
export const CONSULTATION_CONFLICT_CODE = "consultation_conflict";
export const CONSULTATION_NOT_AVAILABLE_CODE = "consultation_not_available";

/** What the workspace is currently doing. Drives every status affordance. */
export type ConsultationSaveStatus =
  | "idle"
  | "saving"
  | "saved"
  | "conflict"
  | "error";

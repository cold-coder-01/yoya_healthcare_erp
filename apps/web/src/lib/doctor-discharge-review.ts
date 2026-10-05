/**
 * Doctor Desk discharge review: the view model behind the review wizard.
 *
 * A PURE PROJECTION of what the Doctor Desk already holds -- the visit's
 * patient, encounter and doctor, and the DoctorAdmissionSummary the server
 * returned. Nothing here fetches, and nothing here decides: whether the
 * request is offered is the server's `can_request_discharge`, and the
 * warnings are the server's `discharge_warnings`, only named here. They never
 * block; the server re-checks everything when the request is sent.
 */
// TYPE-ONLY imports of the payload, so node:test runs this with no resolver.
import type { DischargeCheck, DoctorAdmissionSummary } from "@/types/admissions-desk";

import { formatLengthOfStay, locationLabel, orDash } from "./admissions-desk-format.ts";
import { formatHospitalDateTime } from "./clinical-format.ts";

export type DischargeReviewField = { label: string; value: string };

export type DischargeReview = {
  title: string;
  identifiers: DischargeReviewField[];
  admissionContext: DischargeReviewField[];
  /** The summary as it will be sent: already cleaned, shown in full. */
  summary: string;
  action: string;
  revision: number;
  warnings: { key: string; text: string }[];
};

export type DischargeReviewContext = {
  patientName: string | null;
  patientMrn: string | null;
  encounterName: string | null;
  physicianName: string | null;
};

export const DISCHARGE_REVIEW_ACTION =
  "Declare medically ready. The Admissions Desk finalizes the discharge and releases the bed.";

export function buildDischargeReview(
  admission: NonNullable<DoctorAdmissionSummary["admission"]>,
  warnings: DischargeCheck[],
  context: DischargeReviewContext,
  cleanedSummary: string,
): DischargeReview {
  return {
    title: "Review discharge",
    identifiers: [
      { label: "Patient", value: orDash(context.patientName) },
      { label: "MRN", value: orDash(context.patientMrn) },
      { label: "Encounter", value: orDash(context.encounterName) },
      { label: "Admission", value: admission.reference },
    ],
    admissionContext: [
      { label: "Ward / room / bed", value: locationLabel(admission.location) },
      { label: "Admitted", value: formatHospitalDateTime(admission.admitted_at, "—") },
      { label: "Length of stay", value: formatLengthOfStay(admission.length_of_stay) },
      { label: "Responsible physician", value: orDash(context.physicianName) },
    ],
    summary: cleanedSummary,
    action: DISCHARGE_REVIEW_ACTION,
    revision: admission.workflow_revision,
    warnings: warnings.map((warning) => ({ key: warning.code, text: warning.message })),
  };
}

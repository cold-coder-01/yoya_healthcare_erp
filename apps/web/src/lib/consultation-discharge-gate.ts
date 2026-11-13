/**
 * Decision 3 (docs/business-decisions.md): discharge waits for the doctor to
 * complete the visit's consultation, and the Admissions Desk shows who holds it.
 *
 * Pure wording. The server decides whether a consultation blocks (it never
 * completes one automatically); the browser only names the holder it was given
 * -- in a discharge check's `consultation` or in a 409 error's `consultation`.
 */
// TYPE-ONLY import, so node:test runs this with no resolver.
import type { ConsultationHold } from "@/types/admissions-desk";

import { formatHospitalDateTime } from "./clinical-format.ts";

export const CONSULTATION_OPEN_CODE = "admission_consultation_open";
export const CONSULTATION_OPEN_CHECK = "consultation_open";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function strOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/** A holder from any untrusted object; null when there is nothing usable. */
export function parseConsultationHold(value: unknown): ConsultationHold | null {
  if (!isRecord(value)) return null;
  const hold: ConsultationHold = {
    reference: strOrNull(value.reference),
    doctor_name: strOrNull(value.doctor_name),
    started_at: strOrNull(value.started_at),
    archived: value.archived === true,
  };
  return hold.reference || hold.doctor_name ? hold : null;
}

/** The holder carried by a 409 admission_consultation_open error, if any. */
export function consultationHoldFromPayload(payload: unknown): ConsultationHold | null {
  if (!isRecord(payload) || !isRecord(payload.error)) return null;
  if (payload.error.code !== CONSULTATION_OPEN_CODE) return null;
  return parseConsultationHold(payload.error.consultation);
}

/** Admissions Desk: which doctor holds it, and what to do if it is archived. */
export function admissionsDeskHoldText(hold: ConsultationHold | null): string {
  if (!hold) {
    return "The visit's consultation is still open. The doctor must complete it before discharge.";
  }
  const subject = hold.reference ? `Consultation ${hold.reference}` : "The consultation";
  const doctor = hold.doctor_name ?? "an unassigned doctor";
  if (hold.archived) {
    return (
      `${subject} (held by ${doctor}) is archived but not completed. ` +
      "An administrator must restore it so the doctor can complete it before discharge."
    );
  }
  const since = hold.started_at ? ` since ${formatHospitalDateTime(hold.started_at, "—")}` : "";
  return `${subject} is still open, held by ${doctor}${since}. The doctor must complete it before discharge.`;
}

/** Doctor Desk: the doctor's own next step. Never "it will be completed for you". */
export function doctorDeskHoldText(hold: ConsultationHold | null): string {
  if (hold?.archived) {
    return (
      "This visit's consultation is archived but not completed. Ask an administrator to restore it, " +
      "then complete it before requesting discharge."
    );
  }
  return (
    "Complete the consultation for this visit before requesting discharge. " +
    "It is never completed automatically."
  );
}

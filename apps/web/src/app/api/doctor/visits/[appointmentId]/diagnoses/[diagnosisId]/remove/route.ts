/**
 * POST /api/doctor/visits/[appointmentId]/diagnoses/[diagnosisId]/remove
 *
 * Remove a diagnosis from an open consultation.
 *
 * ODOO ARCHIVES RATHER THAN DELETES, and this route does not pretend
 * otherwise. hospital.patient.diagnosis is the patient's longitudinal record --
 * laboratory requests, prescriptions and treatment plans all carry foreign keys
 * into it -- so removal clears it from the consultation while the record and its
 * audit trail survive. That is also what frees the primary slot, because the
 * uniqueness index is partial on `active`.
 */
import type { DoctorDiagnosisResponse } from "@/types/doctor-diagnosis";

import {
  DOCTOR_API,
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
} from "../../../../../_utils";

export async function POST(
  _request: Request,
  context: { params: Promise<{ appointmentId: string; diagnosisId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId, diagnosisId } = await context.params;
  const visit = parseAppointmentId(appointmentId);
  if (!visit.ok) {
    return visit.response;
  }

  const diagnosis = Number(diagnosisId);
  if (!Number.isInteger(diagnosis) || diagnosis <= 0) {
    return errorResponse("invalid_diagnosis_id", "Diagnosis ID is invalid.", 400);
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorDiagnosisResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${visit.value}/diagnoses/${diagnosis}/remove`,
        "POST",
        {},
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "diagnosis_remove_failed",
      "Unable to remove the diagnosis.",
    );
  }
}

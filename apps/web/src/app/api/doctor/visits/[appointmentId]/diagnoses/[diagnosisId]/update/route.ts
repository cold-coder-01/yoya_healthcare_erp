/**
 * POST /api/doctor/visits/[appointmentId]/diagnoses/[diagnosisId]/update
 *
 * Edit a diagnosis while its consultation is open.
 *
 * A POST action rather than PATCH, matching every other mutation on this
 * surface. Odoo's doctor controller is consistently action-shaped, and one REST
 * verb among a dozen POST actions is a surprise rather than a purity win.
 *
 * The diagnosis id travels in the PATH and is validated by Odoo against the
 * consultation it is being edited through -- so an id belonging to another
 * visit reads as not found even when it is this same doctor's own row.
 */
import type { DoctorDiagnosisResponse } from "@/types/doctor-diagnosis";

import {
  DOCTOR_API,
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  readJsonObject,
  requireOdooSession,
} from "../../../../../_utils";

export async function POST(
  request: Request,
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

  const body = await readJsonObject(request);
  if (!body.ok) {
    return body.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorDiagnosisResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${visit.value}/diagnoses/${diagnosis}/update`,
        "POST",
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "diagnosis_update_failed",
      "Unable to update the diagnosis.",
    );
  }
}

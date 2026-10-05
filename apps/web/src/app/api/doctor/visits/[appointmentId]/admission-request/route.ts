/**
 * POST /api/doctor/visits/[appointmentId]/admission-request
 *
 * The doctor asks for an inpatient stay for this visit (Admissions Slice 2).
 * Forwards exactly {operation_token, reason}, rebuilt by
 * pickAdmissionRequestBody: the doctor names NO ward and NO bed.
 *
 * THE ROUTE DECIDES NOTHING. Odoo loads the visit through the doctor's own
 * scope, and hospital.admission._desk_request_admission() re-checks that the
 * caller is the visit's own doctor, derives everything else from the visit,
 * refuses a duplicate and replays a retried token. Fixed error codes come back
 * as-is, the same vocabulary the Admissions Desk speaks.
 */
import { pickAdmissionRequestBody } from "@/app/api/admissions/_body";
import type { DoctorAdmissionRequestResponse } from "@/types/admissions-desk";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  readJsonObject,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
  request: Request,
  context: { params: Promise<{ appointmentId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId } = await context.params;
  const parsed = parseAppointmentId(appointmentId);
  if (!parsed.ok) {
    return parsed.response;
  }

  const body = await readJsonObject(request);
  if (!body.ok) {
    return body.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorAdmissionRequestResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/admission-request`,
        "POST",
        pickAdmissionRequestBody(body.body),
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "admission_request_failed",
      "Unable to reach the admissions service. Retry to finish the same request safely.",
    );
  }
}

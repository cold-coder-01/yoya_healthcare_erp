/**
 * POST /api/doctor/visits/[appointmentId]/discharge-request
 *
 * The doctor declares this visit's inpatient MEDICALLY ready for discharge
 * (Admissions Slice 4). Forwards exactly {operation_token, expected_revision,
 * summary}, rebuilt by pickDischargeRequestBody.
 *
 * THE ROUTE DECIDES NOTHING. Odoo finds the visit's active admission through
 * the doctor's own record rules, and
 * hospital.admission._desk_request_medical_discharge() re-checks that the
 * caller is its physician (or oversight). It frees no bed and moves no money:
 * the Admissions Desk finalizes the discharge.
 */
import { pickDischargeRequestBody } from "@/app/api/admissions/_body";
import type { DoctorDischargeRequestResponse } from "@/types/admissions-desk";

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
      await callOdooApi<DoctorDischargeRequestResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/discharge-request`,
        "POST",
        pickDischargeRequestBody(body.body),
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "discharge_request_failed",
      "Unable to reach the admissions service. Retry to finish the same request safely.",
    );
  }
}

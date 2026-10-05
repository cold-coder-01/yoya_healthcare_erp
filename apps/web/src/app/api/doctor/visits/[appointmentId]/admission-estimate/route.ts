/**
 * GET  /api/doctor/visits/[appointmentId]/admission-estimate
 * POST /api/doctor/visits/[appointmentId]/admission-estimate
 *
 * The physician's INPATIENT ESTIMATE (Advance slice). GET reads it; POST gives
 * or revises it with exactly {operation_token, expected_revision, amount,
 * reason}, rebuilt by pickEstimateBody.
 *
 * THE ROUTE DECIDES NOTHING. Odoo finds the visit's admission through the
 * doctor's own record rules, and hospital.admission._desk_set_estimate()
 * re-checks that the caller is its physician (or oversight). An estimate moves
 * no money: the Cashier takes the advance.
 */
import { pickEstimateBody } from "@/app/api/admissions/_body";
import type { DoctorEstimateResponse } from "@/types/inpatient-settlement";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  readJsonObject,
  requireOdooSession,
} from "../../../_utils";

type Context = { params: Promise<{ appointmentId: string }> };

export async function GET(_request: Request, context: Context) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }
  const { appointmentId } = await context.params;
  const parsed = parseAppointmentId(appointmentId);
  if (!parsed.ok) {
    return parsed.response;
  }
  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorEstimateResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/admission-estimate`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(error, "estimate_failed", "Unable to load the inpatient estimate.");
  }
}

export async function POST(request: Request, context: Context) {
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
      await callOdooApi<DoctorEstimateResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/admission-estimate`,
        "POST",
        pickEstimateBody(body.body),
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "estimate_failed",
      "Unable to reach the admissions service. Retry to finish the same request safely.",
    );
  }
}

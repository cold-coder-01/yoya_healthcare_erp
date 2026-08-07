import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { ReceptionVisitDetail } from "@/types/reception";

/**
 * Authorize an emergency bypass and route straight to triage.
 *
 * Authorization is NOT decided here. Odoo requires the Emergency Authorizer,
 * Manager or System Administrator role, and hospital.encounter.write()
 * enforces it again underneath. A receptionist without that role receives a
 * 403 from the API, which this route forwards unchanged.
 *
 * Charges are deliberately left unpaid: a bypass postpones payment, it does
 * not forgive it.
 */
export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { id } = await context.params;
  const appointmentId = Number(id);
  if (!Number.isInteger(appointmentId) || appointmentId <= 0) {
    return errorResponse("invalid_visit_id", "Visit ID is invalid.", 400);
  }

  const parsed = await readJsonObject(request);
  if (!parsed.ok) {
    return parsed.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<ReceptionVisitDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/reception/visits/${appointmentId}/emergency-bypass`,
        "POST",
        parsed.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "emergency_bypass_failed",
      "Unable to authorize the emergency bypass.",
    );
  }
}

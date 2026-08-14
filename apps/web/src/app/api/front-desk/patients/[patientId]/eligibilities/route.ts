import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { FrontDeskEligibilityList } from "@/types/front-desk";

/**
 * Payer identities this patient may be presented under today.
 *
 * The BFF validates only that the path segment is a positive integer, so it can
 * build a safe URL. WHICH eligibilities are selectable is decided entirely by
 * hospital.reception.workflow.selectable_eligibilities(), and WHAT is returned
 * is decided by the serializer allowlist in front_desk_serializers.py. Nothing
 * here filters or reshapes the payload; a second opinion in this layer would be
 * a second source of truth that drifts.
 */
export async function GET(
  _request: Request,
  context: { params: Promise<{ patientId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const { patientId: raw } = await context.params;
  const patientId = Number(raw);
  if (!Number.isInteger(patientId) || patientId <= 0) {
    return errorResponse("invalid_patient_id", "Patient ID is invalid.", 400);
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<FrontDeskEligibilityList>(
        session.sessionId,
        `/yoya-emr/api/v1/front-desk/patients/${patientId}/eligibilities`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "front_desk_eligibilities_failed",
      "Unable to load payer eligibilities for this patient.",
    );
  }
}

import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { ReceptionVisitDetail } from "@/types/reception";

export async function GET(
  _request: Request,
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

  try {
    return await forwardOdooResult(
      await callOdooApi<ReceptionVisitDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/reception/visits/${appointmentId}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "visit_detail_failed",
      "Unable to load the visit.",
    );
  }
}

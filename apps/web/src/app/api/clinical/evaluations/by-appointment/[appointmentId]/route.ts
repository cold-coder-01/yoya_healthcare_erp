import { fetchEvaluationDetail } from "@/lib/odoo-client";
import {
  forwardOdooResult,
  handleRouteError,
  parsePositiveInteger,
  requireOdooSession,
} from "../../../_utils";

export async function GET(
  _request: Request,
  context: { params: Promise<{ appointmentId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId } = await context.params;
  const parsed = parsePositiveInteger(appointmentId, "Appointment ID");
  if (!parsed.ok) {
    return parsed.response;
  }

  try {
    return forwardOdooResult(
      await fetchEvaluationDetail(session.sessionId, parsed.value),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "evaluation_detail_failed",
      "Unable to load evaluation detail.",
    );
  }
}

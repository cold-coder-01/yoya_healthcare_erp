import { startClinicalConsultation } from "@/lib/odoo-client";
import {
  forwardOdooResult,
  handleRouteError,
  parsePositiveInteger,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
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
      await startClinicalConsultation(session.sessionId, parsed.value),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "start_consultation_failed",
      "Unable to start consultation.",
    );
  }
}

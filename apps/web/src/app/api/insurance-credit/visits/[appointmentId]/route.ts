import { parseAppointmentId } from "@/app/api/front-desk/_utils";
import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { OfficerVisitDetail } from "@/types/insurance-credit";

/** Canonical officer visit payload: charges plus live benefit evaluation. */
export async function GET(
  _request: Request,
  context: { params: Promise<{ appointmentId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const { appointmentId: raw } = await context.params;
  const parsed = parseAppointmentId(raw);
  if (!parsed.ok) return parsed.response;

  try {
    return await forwardOdooResult(
      await callOdooApi<OfficerVisitDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/insurance-credit/visits/${parsed.value}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "insurance_credit_visit_failed",
      "Unable to load the selected visit.",
    );
  }
}

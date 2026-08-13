import { parseAppointmentId } from "@/app/api/front-desk/_utils";
import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { FrontDeskVisit } from "@/types/front-desk";

export async function GET(
  request: Request,
  context: { params: Promise<{ appointmentId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const { appointmentId: raw } = await context.params;
  const parsed = parseAppointmentId(raw);
  if (!parsed.ok) return parsed.response;

  const url = new URL(request.url);

  try {
    return await forwardOdooResult(
      await callOdooApi<FrontDeskVisit>(
        session.sessionId,
        `/yoya-emr/api/v1/front-desk/visits/${parsed.value}${url.search}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "front_desk_visit_failed",
      "Unable to load the selected visit.",
    );
  }
}

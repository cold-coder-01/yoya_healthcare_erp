import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { FrontDeskWorklist } from "@/types/front-desk";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const url = new URL(request.url);

  try {
    return await forwardOdooResult(
      await callOdooApi<FrontDeskWorklist>(
        session.sessionId,
        `/yoya-emr/api/v1/front-desk/worklist${url.search}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "front_desk_worklist_failed",
      "Unable to load the front desk worklist.",
    );
  }
}

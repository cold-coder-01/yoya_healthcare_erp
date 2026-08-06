import { forwardOdooResult } from "@/app/api/reception/_utils";
import { callOdooApi, requireOdooSession, handleRouteError } from "@/app/api/reception/_utils";

export async function GET() {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi(session.sessionId, "/yoya-emr/api/v1/reception/session", "GET"),
    );
  } catch (error) {
    return handleRouteError(error, "reception_session_failed", "Unable to load reception session.");
  }
}

import { forwardOdooResult, callOdooApi, requireOdooSession, handleRouteError } from "@/app/api/reception/_utils";

export async function GET() {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi(session.sessionId, "/yoya-emr/api/v1/reference/departments", "GET"),
    );
  } catch (error) {
    return handleRouteError(error, "reference_departments_failed", "Unable to load departments.");
  }
}

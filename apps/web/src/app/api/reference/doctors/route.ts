import { forwardOdooResult, callOdooApi, requireOdooSession, handleRouteError, withQuery } from "@/app/api/reception/_utils";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const url = new URL(request.url);
  const path = withQuery("/yoya-emr/api/v1/reference/doctors", {
    department_id: url.searchParams.get("department_id") ?? undefined,
  });

  try {
    return await forwardOdooResult(
      await callOdooApi(session.sessionId, path, "GET"),
    );
  } catch (error) {
    return handleRouteError(error, "reference_doctors_failed", "Unable to load doctors.");
  }
}

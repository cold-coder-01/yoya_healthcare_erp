import { forwardOdooResult, callOdooApi, requireOdooSession, handleRouteError, withQuery } from "@/app/api/reception/_utils";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const url = new URL(request.url);
  const path = withQuery("/yoya-emr/api/v1/reception/queue", {
    date: url.searchParams.get("date") ?? undefined,
    stage: url.searchParams.get("stage") ?? undefined,
    department_id: url.searchParams.get("department_id") ?? undefined,
    doctor_id: url.searchParams.get("doctor_id") ?? undefined,
    visit_type: url.searchParams.get("visit_type") ?? undefined,
    search: url.searchParams.get("search") ?? undefined,
  });

  try {
    return await forwardOdooResult(
      await callOdooApi(session.sessionId, path, "GET"),
    );
  } catch (error) {
    return handleRouteError(error, "reception_queue_failed", "Unable to load reception queue.");
  }
}

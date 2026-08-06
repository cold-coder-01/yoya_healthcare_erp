import { forwardOdooResult, callOdooApi, requireOdooSession, handleRouteError, withQuery } from "@/app/api/reception/_utils";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const url = new URL(request.url);
  const path = withQuery("/yoya-emr/api/v1/reception/visit-preview", {
    patient_id: url.searchParams.get("patient_id") ?? undefined,
    visit_type: url.searchParams.get("visit_type") ?? undefined,
    department_id: url.searchParams.get("department_id") ?? undefined,
    doctor_id: url.searchParams.get("doctor_id") ?? undefined,
  });

  try {
    return await forwardOdooResult(
      await callOdooApi(session.sessionId, path, "GET"),
    );
  } catch (error) {
    return handleRouteError(error, "visit_preview_failed", "Unable to load visit preview.");
  }
}

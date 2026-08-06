import { forwardOdooResult, callOdooApi, requireOdooSession, handleRouteError, withQuery } from "@/app/api/reception/_utils";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const url = new URL(request.url);
  const query = url.searchParams.get("q") ?? "";
  const path = withQuery("/yoya-emr/api/v1/reception/patients/search", { q: query });

  try {
    return await forwardOdooResult(
      await callOdooApi(session.sessionId, path, "GET"),
    );
  } catch (error) {
    return handleRouteError(error, "patient_search_failed", "Unable to load patient search results.");
  }
}

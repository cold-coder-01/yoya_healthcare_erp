/**
 * GET /api/doctor/visit-search?q=
 *
 * "Search all my visits": used by the Doctor Desk only when the date field is
 * cleared. Any date, newest first, limited and flagged when truncated.
 *
 * SCOPE IS NOT SET HERE. This forwards to /yoya-emr/api/v1/doctor/visit-search,
 * which ANDs the same clinical scope domain as the worklist outside its own
 * filters and validates `q` (at least two letters) itself. The query string is
 * forwarded verbatim; this route cannot widen what Odoo returns.
 */
import type { DoctorVisitSearchResponse } from "@/types/doctor";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "../_utils";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const url = new URL(request.url);

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorVisitSearchResponse>(
        session.sessionId,
        `${DOCTOR_API}/visit-search${url.search}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "doctor_visit_search_failed",
      "Unable to search your visits.",
    );
  }
}

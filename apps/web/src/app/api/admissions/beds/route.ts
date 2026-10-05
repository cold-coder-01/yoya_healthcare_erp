/**
 * GET /api/admissions/beds
 *
 * The bed board (?ward_id= &room_id= &state= &q=). A bed names a patient only
 * when the caller may read the admission holding it -- decided upstream.
 *
 * THE ROLE GATE IS NOT SET HERE: upstream calls
 * reception_scope.may_admissions_desk() before it touches a record and answers
 * 403 for every role outside ADMISSIONS_DESK_GROUPS; record rules then decide
 * which admissions come back. This route adds no check and cannot widen what
 * Odoo returned. The query string is forwarded verbatim; upstream parses and
 * refuses its own parameters.
 */
import type { BedsResponse } from "@/types/admissions-desk";

import {
  ADMISSIONS_API,
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
      await callOdooApi<BedsResponse>(session.sessionId, `${ADMISSIONS_API}/beds${url.search}`),
    );
  } catch (error) {
    return handleRouteError(error, "admissions_beds_failed", "Unable to load the bed board.");
  }
}

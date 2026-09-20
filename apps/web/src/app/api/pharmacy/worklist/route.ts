/**
 * GET /api/pharmacy/worklist
 *
 * The counter queue. THE ROLE GATE IS NOT SET HERE: upstream calls
 * reception_scope.may_pharmacy_desk() before it touches a record and answers
 * 403 for every role outside PHARMACY_DESK_GROUPS. This route adds no check and
 * cannot widen what Odoo returned.
 *
 * The query string is forwarded verbatim; upstream parses and refuses its own
 * parameters (`status`, `date`, `q`, `limit`).
 */
import type { PharmacyWorklistResponse } from "@/types/pharmacy-desk";

import {
  PHARMACY_API,
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
      await callOdooApi<PharmacyWorklistResponse>(
        session.sessionId,
        `${PHARMACY_API}/worklist${url.search}`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "pharmacy_worklist_failed",
      "Unable to load the pharmacy queue.",
    );
  }
}

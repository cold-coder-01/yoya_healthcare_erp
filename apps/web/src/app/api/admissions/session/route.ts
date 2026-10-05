/**
 * GET /api/admissions/session
 *
 * Who is signed in, which Admissions Desk role they hold and whether the desk
 * opens for them. PRESENTATION ONLY: every other route enforces the gate too.
 *
 * THE ROLE GATE IS NOT SET HERE: upstream calls
 * reception_scope.may_admissions_desk() before it touches a record and answers
 * 403 for every role outside ADMISSIONS_DESK_GROUPS; record rules then decide
 * which admissions come back. This route adds no check and cannot widen what
 * Odoo returned. The query string is forwarded verbatim; upstream parses and
 * refuses its own parameters.
 */
import type { AdmissionDeskSession } from "@/types/admissions-desk";

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
      await callOdooApi<AdmissionDeskSession>(session.sessionId, `${ADMISSIONS_API}/session${url.search}`),
    );
  } catch (error) {
    return handleRouteError(error, "admissions_session_failed", "Unable to load your session.");
  }
}

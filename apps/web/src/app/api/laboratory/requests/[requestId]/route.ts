/**
 * GET /api/laboratory/requests/[requestId]
 *
 * One laboratory request, read-only: patient identity, visit context, ordered
 * tests and the ordering clinician's own words.
 *
 * A request that does not exist and one the caller's record rules hide BOTH
 * come back 404 `lab_request_not_found` from Odoo -- the same answer -- so this
 * route cannot be used to confirm which request ids are real. A caller outside
 * the Lab Desk roles is refused 403 by the upstream gate before existence is
 * ever evaluated. Neither answer is computed here.
 */
import type { LabRequestResponse } from "@/types/lab-desk";

import {
  LAB_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseRequestId,
  requireOdooSession,
} from "../../_utils";

export async function GET(
  _request: Request,
  context: { params: Promise<{ requestId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { requestId } = await context.params;
  const parsed = parseRequestId(requestId);
  if (!parsed.ok) {
    return parsed.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<LabRequestResponse>(
        session.sessionId,
        `${LAB_API}/requests/${parsed.value}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_request_failed",
      "Unable to load the selected laboratory request.",
    );
  }
}

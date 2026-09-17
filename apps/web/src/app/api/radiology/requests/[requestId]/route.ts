/**
 * GET /api/radiology/requests/[requestId]
 *
 * One radiology request, read-only: patient identity, ordering doctor, ordered
 * studies, the one operational report if unambiguous, and image METADATA.
 *
 * No image bytes and no image URL exist on this desk in Slice 1. A request that
 * does not exist, one the caller's record rules hide and an archived one all
 * come back 404 `radiology_request_not_found` from Odoo; a caller outside the
 * desk roles is refused 403 before existence is evaluated. Neither answer is
 * computed here.
 */
import type { RadRequestResponse } from "@/types/rad-desk";

import {
  RADIOLOGY_API,
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
      await callOdooApi<RadRequestResponse>(
        session.sessionId,
        `${RADIOLOGY_API}/requests/${parsed.value}`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_request_failed",
      "Unable to load the selected radiology request.",
    );
  }
}

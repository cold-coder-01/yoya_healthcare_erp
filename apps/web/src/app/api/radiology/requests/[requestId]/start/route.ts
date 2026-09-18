/**
 * POST /api/radiology/requests/[requestId]/start
 *
 * scheduled -> in_progress. THIS ROUTE DECIDES NOTHING.
 *
 * It forwards to /yoya-emr/api/v1/radiology/requests/<id>/start, which locks
 * the request row, re-checks the desk policy and calls
 * action_mark_in_progress() -- where hospital_billing's clearance gate, charge
 * moves and encounter start live -- in one savepoint. A clearance or integrity
 * refusal arrives as a fixed, amount-free sentence; nothing here reads money.
 *
 * BODILESS: the browser's body is never read or forwarded.
 */
import type { RadTransitionResponse } from "@/types/rad-desk";

import {
  RADIOLOGY_API,
  forwardOdooResult,
  handleRouteError,
  parseRequestId,
  postOdooApi,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
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
      await postOdooApi<RadTransitionResponse>(
        session.sessionId,
        `${RADIOLOGY_API}/requests/${parsed.value}/start`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_start_failed",
      "Unable to reach the radiology service. The exam was not started.",
    );
  }
}

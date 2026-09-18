/**
 * POST /api/radiology/requests/[requestId]/schedule
 *
 * requested -> scheduled. THIS ROUTE DECIDES NOTHING.
 *
 * It forwards to /yoya-emr/api/v1/radiology/requests/<id>/schedule, which
 * checks the Radiology Desk role, locks the request row, re-checks the desk
 * policy (requested, To schedule, not financially blocked, an active study, no
 * report conflict) and calls hospital.radiology.request.action_schedule() in
 * one savepoint. Every refusal arrives already worded without money.
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
        `${RADIOLOGY_API}/requests/${parsed.value}/schedule`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_schedule_failed",
      "Unable to reach the radiology service. The study was not scheduled.",
    );
  }
}

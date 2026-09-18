/**
 * POST /api/radiology/results/[resultId]/release
 *
 * validated -> released, through action_release(): billing delivers the charge once and the request completes when every active study is released. The Doctor Desk shows the report from this moment.
 *
 * THIS ROUTE DECIDES NOTHING. Odoo checks the report-author role, locks the
 * request and the report, re-checks the desk policy and calls the model method
 * in one savepoint. Any billing refusal arrives as a fixed, amount-free
 * sentence.
 *
 * BODILESS: the browser's body is never read or forwarded.
 */
import type { RadSignoffResponse } from "@/types/rad-desk";

import {
  RADIOLOGY_API,
  forwardOdooResult,
  handleRouteError,
  parseResultId,
  postOdooApi,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
  _request: Request,
  context: { params: Promise<{ resultId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { resultId } = await context.params;
  const parsed = parseResultId(resultId);
  if (!parsed.ok) {
    return parsed.response;
  }

  try {
    return await forwardOdooResult(
      await postOdooApi<RadSignoffResponse>(
        session.sessionId,
        `${RADIOLOGY_API}/results/${parsed.value}/release`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_report_release_failed",
      "Unable to reach the radiology service. The report was not released.",
    );
  }
}

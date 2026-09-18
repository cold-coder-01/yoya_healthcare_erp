/**
 * POST /api/radiology/requests/[requestId]/report
 *
 * Find or create THE operational report of an In progress request. THIS ROUTE
 * DECIDES NOTHING.
 *
 * It forwards to /yoya-emr/api/v1/radiology/requests/<id>/report, which locks
 * the request row, re-checks that the study is In progress with exactly zero
 * or one active report, and creates the draft in one savepoint. The model
 * derives the patient, the ordering physician and one line per active study,
 * and records no radiologist.
 *
 * BODILESS: the browser's body is never read or forwarded.
 */
import type { RadReportResponse } from "@/types/rad-desk";

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
      await postOdooApi<RadReportResponse>(
        session.sessionId,
        `${RADIOLOGY_API}/requests/${parsed.value}/report`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_report_open_failed",
      "Unable to reach the radiology service. The report was not opened.",
    );
  }
}

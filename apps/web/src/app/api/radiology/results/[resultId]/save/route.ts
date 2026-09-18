/**
 * POST /api/radiology/results/[resultId]/save
 *
 * Save a DRAFT report's text. No state moves.
 *
 * A PASS-THROUGH. The body is forwarded as the JSON object it is; which keys
 * are writable (findings, impression, recommendations, and per line
 * result_summary and notes) is the Radiology API's allow-list, and it refuses
 * everything else with a 400 rather than silently dropping it. Filtering here
 * as well would be a second list that drifts from the one that protects the
 * record. Who may write -- the report authors -- is decided upstream too.
 */
import type { RadReportResponse } from "@/types/rad-desk";

import {
  RADIOLOGY_API,
  forwardOdooResult,
  handleRouteError,
  parseResultId,
  postOdooApiWithBody,
  readJsonObject,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
  request: Request,
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

  const body = await readJsonObject(request);
  if (!body.ok) {
    return body.response;
  }

  try {
    return await forwardOdooResult(
      await postOdooApiWithBody<RadReportResponse>(
        session.sessionId,
        `${RADIOLOGY_API}/results/${parsed.value}/save`,
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_report_save_failed",
      "Unable to reach the radiology service. The draft was not saved.",
    );
  }
}

/**
 * POST /api/laboratory/results/[resultId]/save
 *
 * Save a DRAFT result's entry fields. No state moves.
 *
 * A PASS-THROUGH. The body is forwarded as the JSON object it is; which keys
 * are writable (interpretation, remarks, and per line result_value, unit,
 * reference_range, abnormal_flag, notes) is the Lab API's allow-list, and it
 * refuses everything else with a 400 rather than silently dropping it. Filtering
 * here as well would be a second list that drifts from the one that protects
 * the record.
 */
import type { LabResultResponse } from "@/types/lab-desk";

import {
  LAB_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseResultId,
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
      await callOdooApi<LabResultResponse>(
        session.sessionId,
        `${LAB_API}/results/${parsed.value}/save`,
        "POST",
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_result_save_failed",
      "Unable to save the laboratory result draft.",
    );
  }
}

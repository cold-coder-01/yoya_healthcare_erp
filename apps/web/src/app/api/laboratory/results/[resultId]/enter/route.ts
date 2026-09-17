/**
 * POST /api/laboratory/results/[resultId]/enter
 *
 * Save the latest entry fields AND mark the result entered, atomically.
 *
 * ONE ROUTE, NOT /save FOLLOWED BY /enter. Mark entered is one act to the
 * technician; the Lab API writes the values and calls
 * hospital.laboratory.result.action_mark_entered() inside one savepoint, so a
 * refusal leaves the draft exactly as it was before the click. Chaining two
 * calls from here would reintroduce the half-done state that design removes.
 *
 * THE COMPLETENESS RULE IS NOT RESTATED. A blank or whitespace-only value is
 * forwarded as typed; the model refuses it and names the test, and that
 * sentence reaches the browser unchanged.
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
        `${LAB_API}/results/${parsed.value}/enter`,
        "POST",
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_result_enter_failed",
      "Unable to mark the laboratory result entered.",
    );
  }
}

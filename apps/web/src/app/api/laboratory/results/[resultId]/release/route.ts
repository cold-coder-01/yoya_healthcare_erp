/**
 * POST /api/laboratory/results/[resultId]/release
 *
 * Release a VALIDATED result: validated -> released, and complete its request.
 *
 * THIS ROUTE DECIDES NOTHING. It forwards to
 * /yoya-emr/api/v1/lab/results/<id>/release, where the Lab API re-checks the
 * desk's policy under lock (request in_progress, one result, result validated)
 * and calls hospital.laboratory.result.action_release() in one savepoint. That
 * method publishes the result to the Doctor Desk and completes the request;
 * neither rule is known or restated here.
 *
 * THE BODY IS EMPTY AND CARRIES NOTHING. Release changes no value, so the
 * browser's body is not read and a fixed `{}` is sent.
 *
 * THE RESPONSE IS FORWARDED UNCHANGED, including the `completion` outcome the
 * Lab API read from the model after release.
 */
import type { LabResultResponse } from "@/types/lab-desk";

import {
  LAB_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseResultId,
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
      await callOdooApi<LabResultResponse>(
        session.sessionId,
        `${LAB_API}/results/${parsed.value}/release`,
        "POST",
        {},
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_result_release_failed",
      "Unable to release the laboratory result.",
    );
  }
}

/**
 * POST /api/laboratory/requests/[requestId]/result
 *
 * Find or create the request's ONE operational result -- what "Enter results"
 * calls.
 *
 * THIS ROUTE DECIDES NOTHING. It forwards to
 * /yoya-emr/api/v1/lab/requests/<id>/result, where the Lab API applies the
 * desk's entry policy (the request must be in_progress; one result per
 * request) under a row lock, and either returns the existing draft or entered
 * result or creates the draft through the model. Whether a result may be
 * started is never restated here.
 *
 * THE BODY IS EMPTY AND CARRIES NOTHING. Nothing about a new result is chosen
 * by the client: the model derives patient, physician, technician and lines
 * from the request.
 */
import type { LabResultResponse } from "@/types/lab-desk";

import {
  LAB_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseRequestId,
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
      await callOdooApi<LabResultResponse>(
        session.sessionId,
        `${LAB_API}/requests/${parsed.value}/result`,
        "POST",
        {},
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_result_open_failed",
      "Unable to open laboratory result entry.",
    );
  }
}

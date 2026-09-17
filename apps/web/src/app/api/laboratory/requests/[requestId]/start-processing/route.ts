/**
 * POST /api/laboratory/requests/[requestId]/start-processing
 *
 * Move a collected sample onto the bench.
 *
 * THIS ROUTE DECIDES NOTHING. It forwards to
 * /yoya-emr/api/v1/lab/requests/<id>/start-processing, which calls exactly one
 * authoritative model method -- hospital.laboratory.request
 * .action_mark_in_progress() -- where the state machine lives. The BFF does
 * not know which states are valid and must not learn: restating the transition
 * here would create a second definition that drifts from Odoo's.
 *
 * NO MONEY IS INVOLVED IN THIS ONE, unlike collection. Laboratory has no
 * billing override for action_mark_in_progress, so no clearance is re-checked
 * and no charge moves; the refusal it can produce names states only.
 *
 * THE BODY IS EMPTY AND CARRIES NOTHING. `{}` is sent to match the convention
 * every other POST route here follows; the upstream handler reads no field
 * from it. Idempotency needs no token: the state machine refuses anything but
 * `sample_collected`, so a replayed POST is a clean 422 rather than a second
 * transition.
 */
import type { LabRequestResponse } from "@/types/lab-desk";

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
      await callOdooApi<LabRequestResponse>(
        session.sessionId,
        `${LAB_API}/requests/${parsed.value}/start-processing`,
        "POST",
        {},
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_start_processing_failed",
      "Unable to start processing.",
    );
  }
}

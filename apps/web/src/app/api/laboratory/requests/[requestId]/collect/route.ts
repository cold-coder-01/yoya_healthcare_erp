/**
 * POST /api/laboratory/requests/[requestId]/collect
 *
 * Mark the sample collected. THE ONLY WRITE ROUTE IN THE LABORATORY DESK.
 *
 * THIS ROUTE DECIDES NOTHING. It forwards to
 * /yoya-emr/api/v1/lab/requests/<id>/collect, which calls exactly one
 * authoritative model method -- hospital.laboratory.request
 * .action_mark_sample_collected() -- where both gates live: hospital_billing's
 * financial-clearance check across every charge on the request, and the base
 * state machine's `requested`-only rule.
 *
 * NO CLEARANCE LOGIC HERE, and none in the browser either. The BFF never reads
 * an amount, never asks whether the patient has paid, and never pre-empts the
 * refusal. Duplicating that decision would create a second definition of
 * "cleared" that drifts from the billing engine's.
 *
 * THE BODY IS EMPTY AND CARRIES NOTHING. `{}` is sent to match the convention
 * every other POST route here follows (see the doctor order-cancel routes);
 * the upstream handler reads no field from it. There is nothing for a client
 * to supply -- the request is identified by the URL and everything else is
 * derived server-side.
 *
 * Idempotency needs no token: the state machine refuses anything but
 * `requested`, so a replayed POST is a clean 422 rather than a second
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
        `${LAB_API}/requests/${parsed.value}/collect`,
        "POST",
        {},
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_collect_failed",
      "Unable to mark the sample collected.",
    );
  }
}

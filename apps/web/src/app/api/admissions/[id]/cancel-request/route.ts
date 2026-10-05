/**
 * POST /api/admissions/[id]/cancel-request
 *
 * Withdraw a DRAFT admission request (Admissions Slice 3), from the Admissions
 * Desk or the Doctor Desk. Forwards exactly {operation_token,
 * expected_revision}, rebuilt by pickCancelRequestBody.
 *
 * THE ROUTE DECIDES NOTHING. Odoo checks the role before it reads the
 * admission; hospital.admission._desk_cancel_request() decides ownership (a
 * doctor may cancel only their own request), owns the lock, the revision and
 * the replay, and releases the visit completion the request had deferred.
 */
import type { AdmissionCancelRequestResponse } from "@/types/admissions-desk";

import {
  ADMISSIONS_API,
  forwardOdooResult,
  handleRouteError,
  parseAdmissionId,
  pickCancelRequestBody,
  postOdooApiWithBody,
  readMutationBody,
  requireOdooSession,
} from "../../_utils";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { id } = await context.params;
  const parsed = parseAdmissionId(id);
  if (!parsed.ok) {
    return parsed.response;
  }

  const body = await readMutationBody(request);
  if (!body.ok) {
    return body.response;
  }

  try {
    return await forwardOdooResult(
      await postOdooApiWithBody<AdmissionCancelRequestResponse>(
        session.sessionId,
        `${ADMISSIONS_API}/${parsed.value}/cancel-request`,
        pickCancelRequestBody(body.body),
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "admission_cancel_request_failed",
      "Unable to reach the admissions service. Retry to finish the same request safely.",
    );
  }
}

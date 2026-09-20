/**
 * POST /api/pharmacy/dispenses/[dispenseId]/validate
 *
 * Hand over exactly the prepared increment. Forwards exactly
 * {operation_token, expected_revision}: no quantity is accepted from the
 * browser, because validation hands over what Prepare billed and nothing else.
 */
import type { PharmacyMutationResponse } from "@/types/pharmacy-desk";

import {
  PHARMACY_API,
  forwardOdooResult,
  handleRouteError,
  parseDispenseId,
  pickValidateBody,
  postOdooApiWithBody,
  readMutationBody,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
  request: Request,
  context: { params: Promise<{ dispenseId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { dispenseId } = await context.params;
  const parsed = parseDispenseId(dispenseId);
  if (!parsed.ok) {
    return parsed.response;
  }

  const body = await readMutationBody(request);
  if (!body.ok) {
    return body.response;
  }

  try {
    return await forwardOdooResult(
      await postOdooApiWithBody<PharmacyMutationResponse>(
        session.sessionId,
        `${PHARMACY_API}/dispenses/${parsed.value}/validate`,
        pickValidateBody(body.body),
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "pharmacy_validate_failed",
      "Unable to reach the pharmacy service. Retry to finish the same request safely.",
    );
  }
}

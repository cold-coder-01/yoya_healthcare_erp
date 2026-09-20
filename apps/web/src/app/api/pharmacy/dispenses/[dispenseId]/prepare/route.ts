/**
 * POST /api/pharmacy/dispenses/[dispenseId]/prepare
 *
 * Set every line's CUMULATIVE intended quantity. Forwards exactly
 * {operation_token, expected_revision, lines: [{line_id, intended_quantity}]}
 * -- rebuilt, never passed through. Who may prepare, whether this dispense may
 * be prepared and whether the quantities are allowed are all decided by Odoo
 * under its locks; its fixed error codes come back unchanged.
 */
import type { PharmacyMutationResponse } from "@/types/pharmacy-desk";

import {
  PHARMACY_API,
  forwardOdooResult,
  handleRouteError,
  parseDispenseId,
  pickPrepareBody,
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
        `${PHARMACY_API}/dispenses/${parsed.value}/prepare`,
        pickPrepareBody(body.body),
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "pharmacy_prepare_failed",
      "Unable to reach the pharmacy service. Retry to finish the same request safely.",
    );
  }
}

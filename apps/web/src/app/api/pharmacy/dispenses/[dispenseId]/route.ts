/**
 * GET /api/pharmacy/dispenses/[dispenseId]
 *
 * One dispense, read-only: patient identity, prescriber, prescription, per-line
 * quantities and amount-free billing/stock verdicts. A missing, hidden or
 * archived dispense is 404 from Odoo; a caller outside the desk roles is 403
 * before existence is evaluated. Neither answer is computed here.
 */
import type { PharmacyDispenseResponse } from "@/types/pharmacy-desk";

import {
  PHARMACY_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseDispenseId,
  requireOdooSession,
} from "../../_utils";

export async function GET(
  _request: Request,
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

  try {
    return await forwardOdooResult(
      await callOdooApi<PharmacyDispenseResponse>(
        session.sessionId,
        `${PHARMACY_API}/dispenses/${parsed.value}`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "pharmacy_dispense_failed",
      "Unable to load the selected dispense.",
    );
  }
}

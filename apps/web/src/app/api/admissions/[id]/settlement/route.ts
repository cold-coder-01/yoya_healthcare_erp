/**
 * GET /api/admissions/[id]/settlement
 *
 * The stay's FINAL SETTLEMENT for the Admissions Desk: estimate, advance, care
 * delivered by category, payer and patient shares, funds, the result and the
 * server-reported calculation stages. Read-only; it moves no money.
 *
 * The one Admissions route that carries amounts. Odoo refuses it (403) to
 * every role but the discharging clerk and oversight -- the ward nurse and the
 * doctor included. Nothing is computed or re-checked here.
 */
import type { AdmissionSettlementResponse } from "@/types/inpatient-settlement";

import {
  ADMISSIONS_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAdmissionId,
  requireOdooSession,
} from "../../_utils";

export async function GET(
  _request: Request,
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

  try {
    return await forwardOdooResult(
      await callOdooApi<AdmissionSettlementResponse>(
        session.sessionId,
        `${ADMISSIONS_API}/${parsed.value}/settlement`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "admission_settlement_failed",
      "Unable to calculate the final settlement.",
    );
  }
}

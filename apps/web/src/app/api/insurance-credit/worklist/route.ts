import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { OfficerWorklist } from "@/types/insurance-credit";

/**
 * Visits waiting on a sponsor decision.
 *
 * A pass-through: date, q, limit and include_resolved are all validated in
 * Odoo, which owns the queue predicate. Re-deriving membership here would
 * create a second definition of "needs review" that drifts from the server's.
 */
export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const url = new URL(request.url);

  try {
    return await forwardOdooResult(
      await callOdooApi<OfficerWorklist>(
        session.sessionId,
        `/yoya-emr/api/v1/insurance-credit/worklist${url.search}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "insurance_credit_worklist_failed",
      "Unable to load the insurance review queue.",
    );
  }
}

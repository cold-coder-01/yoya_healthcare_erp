import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { AccountantWorklist } from "@/types/accountant";

/**
 * The Accountant queue: refund due, needs review, refunded.
 *
 * A pass-through. `lane` and `q` are validated in Odoo, which rejects an
 * unknown lane with 400 invalid_lane. The queue is NOT date-driven: a refund
 * owed on a stay discharged days ago is still listed.
 */
export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const url = new URL(request.url);

  try {
    return await forwardOdooResult(
      await callOdooApi<AccountantWorklist>(
        session.sessionId,
        `/yoya-emr/api/v1/accountant/worklist${url.search}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "accountant_worklist_failed",
      "Unable to load the accountant queue.",
    );
  }
}

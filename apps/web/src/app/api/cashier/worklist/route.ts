import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { CashierWorklist } from "@/types/cashier";

/**
 * The Cashier queue.
 *
 * A pass-through. The query string is forwarded verbatim -- date, q, stage,
 * department_id, limit are all validated in Odoo, which rejects an unknown
 * stage with 400 invalid_stage. Re-validating here would create a second
 * allowlist that drifts from the server's.
 */
export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const url = new URL(request.url);

  try {
    return await forwardOdooResult(
      await callOdooApi<CashierWorklist>(
        session.sessionId,
        `/yoya-emr/api/v1/cashier/worklist${url.search}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "cashier_worklist_failed",
      "Unable to load the cashier queue.",
    );
  }
}

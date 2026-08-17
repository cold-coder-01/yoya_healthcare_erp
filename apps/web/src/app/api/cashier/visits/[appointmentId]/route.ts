import { parseAppointmentId } from "@/app/api/front-desk/_utils";
import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { CashierVisitDetail } from "@/types/cashier";

/**
 * Canonical cashier visit detail.
 *
 * Returns the same shape a successful payment returns, minus the receipt, so
 * the workstation renders one financial object whether it just loaded a visit
 * or just paid for one.
 */
export async function GET(
  _request: Request,
  context: { params: Promise<{ appointmentId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const { appointmentId: raw } = await context.params;
  const parsed = parseAppointmentId(raw);
  if (!parsed.ok) return parsed.response;

  try {
    return await forwardOdooResult(
      await callOdooApi<CashierVisitDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/cashier/visits/${parsed.value}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "cashier_visit_failed",
      "Unable to load the selected visit.",
    );
  }
}

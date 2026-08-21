/**
 * POST /api/doctor/visits/[appointmentId]/orders/laboratory/[orderId]/cancel
 *
 * Cancel a laboratory order.
 *
 * ODOO'S OWN WORKFLOW DECIDES WHETHER IT MAY BE CANCELLED, and this route does
 * not second-guess it. The base transition guard permits cancellation only from
 * draft or requested and refuses a request carrying a validated or released
 * result; hospital_billing then cancels the operational charges and refuses
 * outright if any has already been delivered. Its refusal is forwarded with its
 * own wording, because that sentence is the only thing that says WHY.
 */
import type { DoctorLabOrderResponse } from "@/types/doctor-laboratory";

import {
  DOCTOR_API,
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
} from "../../../../../../_utils";

export async function POST(
  _request: Request,
  context: { params: Promise<{ appointmentId: string; orderId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId, orderId } = await context.params;
  const visit = parseAppointmentId(appointmentId);
  if (!visit.ok) {
    return visit.response;
  }

  const order = Number(orderId);
  if (!Number.isInteger(order) || order <= 0) {
    return errorResponse("invalid_order_id", "Order ID is invalid.", 400);
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorLabOrderResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${visit.value}/orders/laboratory/${order}/cancel`,
        "POST",
        {},
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "laboratory_cancel_failed",
      "Unable to cancel the laboratory order.",
    );
  }
}

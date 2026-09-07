/**
 * /api/doctor/visits/[appointmentId]/orders/laboratory
 *
 *   GET   the laboratory orders placed in this visit's consultation
 *   POST  place a new one
 *
 * NEITHER DECIDES ANYTHING, AND THE POST DECIDES NOTHING FINANCIAL IN
 * PARTICULAR. The body carries tests, an optional diagnosis, a priority, a
 * clinical indication and an idempotency token. Patient, physician, encounter,
 * appointment and consultation are all derived server-side.
 *
 * Confirmation runs Odoo's own action_confirm_request(), where hospital_billing
 * validates every test's billing configuration and raises one charge per test,
 * all-or-nothing. No layer of this application creates a charge, and this route
 * does not know that charges exist.
 */
import type { DoctorLabOrderResponse } from "@/types/doctor-laboratory";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  readJsonObject,
  requireOdooSession,
} from "../../../../_utils";

type Context = { params: Promise<{ appointmentId: string }> };

export async function GET(_request: Request, context: Context) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId } = await context.params;
  const parsed = parseAppointmentId(appointmentId);
  if (!parsed.ok) {
    return parsed.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorLabOrderResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/orders/laboratory`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "laboratory_orders_load_failed",
      "Unable to load the laboratory orders for this consultation.",
    );
  }
}

export async function POST(request: Request, context: Context) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId } = await context.params;
  const parsed = parseAppointmentId(appointmentId);
  if (!parsed.ok) {
    return parsed.response;
  }

  const body = await readJsonObject(request);
  if (!body.ok) {
    return body.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorLabOrderResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/orders/laboratory`,
        "POST",
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "laboratory_order_failed",
      "Unable to place the laboratory order.",
    );
  }
}

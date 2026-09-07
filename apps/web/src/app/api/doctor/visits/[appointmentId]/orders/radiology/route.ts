/**
 * /api/doctor/visits/[appointmentId]/orders/radiology
 *
 *   GET   the radiology orders placed in this visit's consultation
 *   POST  place a new one
 *
 * NEITHER DECIDES ANYTHING, AND THE POST DECIDES NOTHING FINANCIAL IN
 * PARTICULAR. The body carries studies, an optional diagnosis, a priority, a
 * clinical indication, preparation instructions and an idempotency token.
 * Patient, physician, encounter, appointment and consultation are all derived
 * server-side.
 *
 * Confirmation runs Odoo's own action_confirm_request(), where hospital_billing
 * validates every exam's billing configuration and raises one charge per study,
 * all-or-nothing. No layer of this application creates a charge, and this route
 * does not know that charges exist.
 *
 * THE GET IS KEYED ON THE CONSULTATION, NOT THE VISIT STATE, so orders stay
 * readable after the visit finishes. That is not incidental for imaging: a scan
 * routinely outlives the consultation that ordered it.
 */
import type { DoctorRadOrderResponse } from "@/types/doctor-radiology";

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
      await callOdooApi<DoctorRadOrderResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/orders/radiology`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_orders_load_failed",
      "Unable to load the radiology orders for this consultation.",
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
      await callOdooApi<DoctorRadOrderResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/orders/radiology`,
        "POST",
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_order_failed",
      "Unable to place the radiology order.",
    );
  }
}

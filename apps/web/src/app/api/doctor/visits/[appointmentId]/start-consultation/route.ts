/**
 * POST /api/doctor/visits/[appointmentId]/start-consultation
 *
 * THE ONLY WRITE ON THE DOCTOR DESK, AND IT DECIDES NOTHING.
 *
 * This forwards to the dedicated Odoo doctor route, which loads the appointment
 * through the caller's own scope and calls
 * hospital.appointment.action_start_consultation() and nothing else. That
 * single call runs four independent model-layer gates, in this order:
 *
 *   1. yoya_reception_bridge._assert_may_start_consultation()
 *        assigned doctor / manager / admin only          -> AccessError -> 403
 *   2. yoya_reception_bridge._assert_triage_completed()
 *        nursing evaluation must be done                 -> UserError   -> 422
 *   3. hospital_billing.action_start_consultation()
 *        financial clearance on the consultation charge  -> UserError   -> 422
 *   4. hospital_management.action_start_consultation()
 *        confirmed -> in_consultation, plus the audit log
 *
 * There is no sudo() anywhere on this path, no state is written by any
 * JavaScript in this repository, and none of the four checks is re-implemented
 * in the browser. The button on the workstation is a CALLER of this gate, and
 * the readiness hint it shows is an affordance that Odoo overrules.
 *
 * Errors are forwarded verbatim so the doctor reads Odoo's own reason -- the
 * sentence that names which of the four gates refused. Rewriting them into
 * generic UI copy would hide exactly that.
 */
import type { DoctorStartConsultationResponse } from "@/types/doctor";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
  _request: Request,
  context: { params: Promise<{ appointmentId: string }> },
) {
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
      await callOdooApi<DoctorStartConsultationResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/start-consultation`,
        "POST",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "start_consultation_failed",
      "Unable to start consultation.",
    );
  }
}

/**
 * POST /api/doctor/visits/[appointmentId]/start-consultation
 *
 * THE ONLY WRITE ON THE DOCTOR DESK, AND IT DECIDES NOTHING.
 *
 * This is a thin alias over the existing, unchanged clinical endpoint, which
 * calls hospital.appointment.action_start_consultation() and nothing else.
 * That single call runs four independent model-layer gates, in this order:
 *
 *   1. yoya_reception_bridge._assert_may_start_consultation()
 *        assigned doctor / manager / admin only          -> AccessError
 *   2. yoya_reception_bridge._assert_triage_completed()
 *        nursing evaluation must be done                 -> UserError
 *   3. hospital_billing.action_start_consultation()
 *        financial clearance on the consultation charge  -> UserError
 *   4. hospital_management.action_start_consultation()
 *        confirmed -> in_consultation, plus the audit log
 *
 * There is no sudo() anywhere on this path, no state is written by any
 * JavaScript in this repository, and none of the four checks is re-implemented
 * in the browser. The button on the workstation is a CALLER of this gate, and
 * the readiness hint it shows is an affordance that Odoo overrules.
 *
 * It exists as a separate route from the clinical one purely so the Doctor
 * Desk has a stable /api/doctor/* surface; it adds no behaviour. Errors are
 * forwarded verbatim so the doctor reads Odoo's own reason -- including the
 * clearance payload the clinical controller attaches to
 * `billing_clearance_required`.
 */
import { startClinicalConsultation } from "@/lib/odoo-client";

import {
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
    const result = await startClinicalConsultation(session.sessionId, parsed.value);
    // Forwarded as-is: success and failure alike are Odoo's words.
    return Response.json(result.body, { status: result.status });
  } catch (error) {
    return handleRouteError(
      error,
      "start_consultation_failed",
      "Unable to start consultation.",
    );
  }
}

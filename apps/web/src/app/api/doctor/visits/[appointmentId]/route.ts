/**
 * GET /api/doctor/visits/[appointmentId]
 *
 * The operational panel for one selected visit: identity, triage, vitals,
 * medical alerts and the clearance verdict, in one read.
 *
 * A visit outside this doctor's scope comes back 403 `out_of_scope` from Odoo,
 * and one that does not exist comes back 404. Neither answer is computed here.
 */
import { fetchEvaluationDetail } from "@/lib/odoo-client";

import { adaptVisit } from "@/lib/doctor-adapt";
import {
  forwardAdapted,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
} from "../../_utils";

export async function GET(
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
    const result = await fetchEvaluationDetail(session.sessionId, parsed.value);
    return forwardAdapted(result, adaptVisit);
  } catch (error) {
    return handleRouteError(
      error,
      "doctor_visit_failed",
      "Unable to load the selected visit.",
    );
  }
}

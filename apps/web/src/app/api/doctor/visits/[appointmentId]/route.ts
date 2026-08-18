/**
 * GET /api/doctor/visits/[appointmentId]
 *
 * The operational panel for one selected visit: identity, visit, triage,
 * vitals, medical alerts, the sponsorship category and the clearance verdict,
 * in one read.
 *
 * A visit outside this doctor's scope comes back 404 `visit_not_found` from
 * Odoo -- the same answer an id that does not exist produces, so the desk
 * cannot be used to confirm a colleague's visit exists. Neither answer is
 * computed here.
 */
import type { DoctorVisitResponse } from "@/types/doctor";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
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
    return await forwardOdooResult(
      await callOdooApi<DoctorVisitResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "doctor_visit_failed",
      "Unable to load the selected visit.",
    );
  }
}

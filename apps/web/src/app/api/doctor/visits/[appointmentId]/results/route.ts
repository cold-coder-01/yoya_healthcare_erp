/**
 * /api/doctor/visits/[appointmentId]/results
 *
 *   GET  released laboratory and radiology findings for this visit
 *
 * GET IS THE ONLY EXPORT, AND THAT IS THE DESIGN. Reporting, validating and
 * releasing belong to the laboratory and the imaging department; the Doctor
 * Desk reviews what they have handed off. There is deliberately no POST here
 * -- no acknowledge, no mark-reviewed, no sign-off -- because no model records
 * any of those, and a route would be asserting something no record supports.
 *
 * ONE CALL, BOTH SERVICES. The Results tab always renders laboratory and
 * radiology together, so Odoo returns them in one envelope and this forwards
 * it untouched. Which results are released, which are visible to this doctor
 * and what may be shown are all decided upstream; nothing clinical is
 * re-derived here.
 */
import type { DoctorResultsResponse } from "@/types/doctor-results";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
} from "../../../_utils";

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
      await callOdooApi<DoctorResultsResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/results`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "results_load_failed",
      "Unable to load the results for this consultation.",
    );
  }
}

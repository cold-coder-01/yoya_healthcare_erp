/**
 * /api/doctor/visits/[appointmentId]/history/[historicalAppointmentId]
 *
 *   GET  one PRIOR clinical episode, in full
 *
 * BOTH APPOINTMENTS TRAVEL UPSTREAM, and both are checked there. The current
 * one proves the caller's care relationship; the historical one names the
 * episode. Odoo verifies that the historical episode belongs to the SAME
 * patient as the current visit and that it still qualifies as history -- the
 * pairing is checked, never the historical id alone.
 *
 * A REFUSAL IS A FLAT 404, AND IT IS FORWARDED AS ONE. An episode that never
 * existed, one belonging to another patient, one with no clinical substance
 * and one the caller may no longer reach all answer identically upstream. This
 * route must not translate, enrich or distinguish them: the sameness IS the
 * non-disclosure property, and a friendlier message per case would undo it.
 *
 * FETCHED ONLY WHEN AN EPISODE IS OPENED. The worklist is served by the
 * summary route and carries counts, not content, so a doctor scanning ten
 * visits costs one request rather than eleven.
 */
import type { DoctorHistoryDetailResponse } from "@/types/doctor-history";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
} from "../../../../_utils";

type Context = {
  params: Promise<{ appointmentId: string; historicalAppointmentId: string }>;
};

export async function GET(_request: Request, context: Context) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId, historicalAppointmentId } = await context.params;
  const visit = parseAppointmentId(appointmentId);
  if (!visit.ok) {
    return visit.response;
  }
  /* The historical id is validated exactly as strictly as the current one, and
     for the same reason: this builds an upstream URL, so a non-integer must
     never reach it. */
  const historical = parseAppointmentId(historicalAppointmentId);
  if (!historical.ok) {
    return historical.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorHistoryDetailResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${visit.value}/history/${historical.value}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "history_visit_load_failed",
      "Unable to load this visit.",
    );
  }
}

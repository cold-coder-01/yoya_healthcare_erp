/**
 * /api/doctor/visits/[appointmentId]/history
 *
 *   GET  the compact list of this patient's PRIOR clinical episodes
 *
 * GET IS THE ONLY EXPORT. History records nothing and changes nothing: there
 * is no acknowledge, no annotate and no correct-a-past-visit here, because a
 * historical episode belongs to the consultation that created it and Slice 9A
 * serves it read-only at the record-rule layer.
 *
 * THE APPOINTMENT IN THE PATH IS THE ONLY CREDENTIAL. The patient is derived
 * upstream from the caller's own current visit and can never be named by the
 * browser -- so this route forwards a visit id and a page window, and nothing
 * else. A patient id has no way to reach Odoo from here, which is the property
 * that stops the desk being used to walk the hospital's census.
 *
 * NOTHING CLINICAL IS DECIDED HERE. Which episodes qualify, which provider's
 * work is visible, and whether the care relationship still holds are all
 * upstream decisions; this forwards the envelope untouched.
 */
import type { DoctorHistoryResponse } from "@/types/doctor-history";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
  withQuery,
} from "../../../_utils";

type Context = { params: Promise<{ appointmentId: string }> };

export async function GET(request: Request, context: Context) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId } = await context.params;
  const parsed = parseAppointmentId(appointmentId);
  if (!parsed.ok) {
    return parsed.response;
  }

  /*
    TWO SCALARS, FORWARDED RATHER THAN INTERPRETED. Odoo clamps `limit` to its
    own ceiling and refuses a negative `offset`; re-deriving either here would
    create a second paging policy that could drift from the one the server
    actually enforces. withQuery drops empty values, so an absent parameter
    stays absent instead of arriving as `limit=`.
  */
  const query = new URL(request.url).searchParams;

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorHistoryResponse>(
        session.sessionId,
        withQuery(`${DOCTOR_API}/visits/${parsed.value}/history`, {
          limit: query.get("limit") ?? undefined,
          offset: query.get("offset") ?? undefined,
        }),
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "history_load_failed",
      "Unable to load this patient's history.",
    );
  }
}

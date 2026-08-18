/**
 * GET /api/doctor/queue
 *
 * The doctor's working queue for one hospital day.
 *
 * SCOPE IS NOT SET HERE. This forwards to /yoya-emr/api/v1/doctor/worklist,
 * which applies services/clinical_scope.build_appointment_scope_domain --
 * restricting a doctor to `doctor_id.user_id = <caller>` -- on top of the ORM
 * record rules yoya_reception_bridge ships for hospital.appointment. This route
 * adds no domain of its own and cannot widen what Odoo returned.
 *
 * The query string is forwarded verbatim. The upstream endpoint parses and
 * rejects its own parameters, and its error message is better than one invented
 * here would be. There is deliberately no doctor_id parameter upstream, so
 * passing one changes nothing.
 */
import type { DoctorQueueResponse } from "@/types/doctor";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "../_utils";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const url = new URL(request.url);

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorQueueResponse>(
        session.sessionId,
        `${DOCTOR_API}/worklist${url.search}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "doctor_queue_failed",
      "Unable to load the doctor queue.",
    );
  }
}

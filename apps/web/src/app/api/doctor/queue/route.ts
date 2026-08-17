/**
 * GET /api/doctor/queue
 *
 * The doctor's working queue for one hospital day.
 *
 * SCOPE IS NOT SET HERE. This forwards to /clinical/evaluation-queue, which
 * applies services/clinical_scope.build_appointment_scope_domain -- restricting
 * a doctor to `doctor_id.user_id = <caller>` -- on top of the ORM record rules
 * yoya_reception_bridge ships for hospital.appointment. This route adds no
 * domain of its own and can only ever narrow what Odoo already returned.
 */
import { fetchEvaluationQueue } from "@/lib/odoo-client";

import { adaptQueue } from "@/lib/doctor-adapt";
import { forwardAdapted, handleRouteError, requireOdooSession } from "../_utils";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const params = new URL(request.url).searchParams;

  try {
    const result = await fetchEvaluationQueue(session.sessionId, {
      // Passed through unvalidated on purpose: the upstream endpoint parses and
      // rejects these itself, and its error message is better than one invented
      // here would be.
      date: params.get("date") ?? undefined,
      state: params.get("state") ?? undefined,
      department_id: params.get("department_id") ?? undefined,
      doctor_id: params.get("doctor_id") ?? undefined,
    });

    return forwardAdapted(result, adaptQueue);
  } catch (error) {
    return handleRouteError(
      error,
      "doctor_queue_failed",
      "Unable to load the doctor queue.",
    );
  }
}

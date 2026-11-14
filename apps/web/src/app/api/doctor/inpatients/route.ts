/**
 * GET /api/doctor/inpatients
 *
 * The Doctor Desk Inpatients lane: the caller's current inpatients, whatever
 * day the visit began (business decision 8).
 *
 * SCOPE IS NOT SET HERE. This forwards to /yoya-emr/api/v1/doctor/inpatients,
 * which reads admissions under the caller's own record rules AND keeps only
 * stays whose visit is inside the Doctor Desk scope (booked to the caller;
 * managers and system administrators see every stay). There is no date and no
 * doctor parameter upstream; this route adds nothing and cannot widen it.
 */
import type { DoctorInpatientsResponse } from "@/types/doctor";

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
      await callOdooApi<DoctorInpatientsResponse>(
        session.sessionId,
        `${DOCTOR_API}/inpatients${url.search}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "doctor_inpatients_failed",
      "Unable to load your inpatients.",
    );
  }
}

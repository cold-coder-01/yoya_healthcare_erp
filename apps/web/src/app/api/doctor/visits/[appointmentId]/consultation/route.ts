/**
 * GET /api/doctor/visits/[appointmentId]/consultation
 *
 * The active consultation note for one visit.
 *
 * WHY THIS IS SEPARATE FROM /api/doctor/visits/[id]
 * The visit-detail read fires on EVERY queue selection, including for visits
 * that have not started and never will. Folding the note into it would open a
 * clinical record as a side effect of clicking a row in a list.
 *
 * WHY A GET MAY OPEN THE RECORD
 * Odoo's handler is get-or-create, and it is idempotent: one consultation per
 * encounter, guaranteed by an advisory lock and a unique index. Requiring the
 * desk to POST first would leave the very first save of every consultation with
 * no version token to build on -- the exact gap the token exists to close. A
 * visit that has not reached `in_consultation` returns
 * `{ available: false, consultation: null }` and creates nothing at all; that
 * decision is Odoo's and is not second-guessed here.
 */
import type { DoctorConsultationResponse } from "@/types/doctor-consultation";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
} from "../../../_utils";

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
      await callOdooApi<DoctorConsultationResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/consultation`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "consultation_load_failed",
      "Unable to load the consultation note.",
    );
  }
}

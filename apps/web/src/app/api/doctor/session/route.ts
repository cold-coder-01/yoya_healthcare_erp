/**
 * GET /api/doctor/session
 *
 * Who is signed in, which hospital.doctor record they are, whether they hold
 * the Doctor group, and what the desk may offer them.
 *
 * PRESENTATION ONLY. `is_doctor` and `capabilities` gate hints, never data:
 * every read this page performs is scoped by Odoo record rules and the clinical
 * scope domain regardless of what this endpoint reports.
 *
 * The upstream route is deliberately reachable without the Doctor Desk role, so
 * the shell can tell a nurse plainly why the desk is empty for them instead of
 * rendering a bare 403.
 */
import type { DoctorSession } from "@/types/doctor";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "../_utils";

export async function GET() {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorSession>(
        session.sessionId,
        `${DOCTOR_API}/session`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "doctor_session_failed",
      "Unable to load your session.",
    );
  }
}

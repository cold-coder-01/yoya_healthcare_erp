/**
 * GET /api/doctor/session
 *
 * Who is signed in, which hospital.doctor record they are, and whether they
 * hold the Doctor group. Used for the shell's identity line and to tell a
 * non-doctor plainly why the desk is empty for them.
 *
 * PRESENTATION ONLY. `is_doctor` gates a hint, never data: every read this
 * page performs is scoped by Odoo record rules and the clinical scope domain
 * regardless of what this endpoint reports.
 */
import { fetchClinicalSession } from "@/lib/odoo-client";
import type { ClinicalSession } from "@/types/clinical";
import type { DoctorSession } from "@/types/doctor";

import { forwardAdapted, handleRouteError, requireOdooSession } from "../_utils";

/**
 * `groups` arrives from serialize_session as the dict built by
 * clinical_scope.hospital_groups (doctor, nurse, manager, ...), but the shared
 * ClinicalSession type declares it as string[]. Both shapes are handled rather
 * than asserting one, so a backend that changes it cannot crash the shell.
 */
function isDoctor(groups: ClinicalSession["groups"]): boolean {
  if (Array.isArray(groups)) {
    return groups.includes("doctor");
  }
  if (groups && typeof groups === "object") {
    return Boolean((groups as Record<string, boolean>).doctor);
  }
  return false;
}

function adaptSession(source: ClinicalSession): DoctorSession {
  return {
    user: source.user,
    company: source.company,
    doctor: source.doctor,
    is_doctor: isDoctor(source.groups),
    groups: source.groups,
  };
}

export async function GET() {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  try {
    const result = await fetchClinicalSession(session.sessionId);
    return forwardAdapted(result, adaptSession);
  } catch (error) {
    return handleRouteError(
      error,
      "doctor_session_failed",
      "Unable to load your session.",
    );
  }
}

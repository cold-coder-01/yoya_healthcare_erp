/**
 * POST /api/doctor/visits/[appointmentId]/consultation/save
 *
 * Saves the consultation narrative. IT DECIDES NOTHING.
 *
 * The body is forwarded to Odoo essentially untouched. This route does NOT
 * validate which fields are writable, does NOT compare the version, does NOT
 * check the completion freeze and does NOT decide who may write -- every one of
 * those lives in hospital.consultation.save_narrative(), which enforces them
 * for the Odoo form and any RPC caller as well as for this route. Re-checking
 * them here would create a second source of truth that drifts.
 *
 * The one thing validated is that the body is a JSON OBJECT, because forwarding
 * an array or a bare string would produce a confusing Odoo-side error about a
 * request this layer could plainly see was malformed.
 *
 * CONFLICTS ARE FORWARDED, NOT SMOOTHED. Odoo answers 409
 * `consultation_conflict` when the note changed after the client read it, with
 * a sentence telling the doctor to reload. That status and that wording reach
 * the browser unchanged: a merge attempted here, or a retry with a refreshed
 * token, would silently overwrite the other clinician's paragraph.
 */
import type { DoctorConsultationResponse } from "@/types/doctor-consultation";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  readJsonObject,
  requireOdooSession,
} from "../../../../_utils";

export async function POST(
  request: Request,
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

  const body = await readJsonObject(request);
  if (!body.ok) {
    return body.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorConsultationResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/consultation/save`,
        "POST",
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "consultation_save_failed",
      "Unable to save the consultation note.",
    );
  }
}

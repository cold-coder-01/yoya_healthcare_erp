import { parseAppointmentId } from "@/app/api/front-desk/_utils";
import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { FrontDeskVisit } from "@/types/front-desk";

/**
 * Assign or change the doctor for a visit. Body: {"doctor_id": <int>}.
 *
 * The doctor/department rule, the appointment -> encounter -> draft-evaluation
 * synchronization and the group check all live in
 * hospital.reception.workflow.assign_doctor(). This route deliberately does not
 * re-implement any of them: it checks only that doctor_id is a positive integer
 * so the payload is well-formed, and lets Odoo's 400 carry the real message
 * ("Dr X belongs to Cardiology, not Paediatrics") through to the panel.
 */
export async function POST(
  request: Request,
  context: { params: Promise<{ appointmentId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const { appointmentId: raw } = await context.params;
  const parsed = parseAppointmentId(raw);
  if (!parsed.ok) return parsed.response;

  const payload = await readJsonObject(request);
  if (!payload.ok) return payload.response;

  const doctorId = Number(payload.body.doctor_id);
  if (!Number.isInteger(doctorId) || doctorId <= 0) {
    return errorResponse(
      "invalid_doctor_id",
      "A doctor must be selected.",
      400,
    );
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<FrontDeskVisit>(
        session.sessionId,
        `/yoya-emr/api/v1/front-desk/visits/${parsed.value}/doctor`,
        "POST",
        { doctor_id: doctorId },
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "front_desk_assign_doctor_failed",
      "Unable to assign the doctor for this visit.",
    );
  }
}

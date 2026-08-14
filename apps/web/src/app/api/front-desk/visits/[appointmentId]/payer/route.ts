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
 * Select, change or clear the payer identity of a visit.
 *
 * Body: {"patient_payer_id": <int>} to select, {"patient_payer_id": null} to
 * return the visit to no sponsorship identity. Clearing is a legitimate outcome,
 * so an explicit null is forwarded rather than rejected -- the key's PRESENCE is
 * what this route checks.
 *
 * The role check, the patient/company validation, the selectability rule and the
 * payer-change freeze all live in
 * hospital.reception.workflow.set_visit_payer(). None of them is repeated here,
 * so Odoo's own message ("payment RCP0042 has already been received against
 * encounter ENC01225") reaches the panel intact.
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

  if (!("patient_payer_id" in payload.body)) {
    return errorResponse(
      "invalid_patient_payer_id",
      "Send a patient_payer_id, or null to clear the payer.",
      400,
    );
  }

  const value = payload.body.patient_payer_id;
  let patientPayerId: number | null = null;
  if (value !== null && value !== undefined && value !== "") {
    const parsedId = Number(value);
    if (!Number.isInteger(parsedId) || parsedId <= 0) {
      return errorResponse(
        "invalid_patient_payer_id",
        "The selected payer eligibility is invalid.",
        400,
      );
    }
    patientPayerId = parsedId;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<FrontDeskVisit>(
        session.sessionId,
        `/yoya-emr/api/v1/front-desk/visits/${parsed.value}/payer`,
        "POST",
        { patient_payer_id: patientPayerId },
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "front_desk_set_payer_failed",
      "Unable to update the payer for this visit.",
    );
  }
}

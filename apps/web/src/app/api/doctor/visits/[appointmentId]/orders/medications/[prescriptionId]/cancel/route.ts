/**
 * POST /api/doctor/visits/[appointmentId]/orders/medications/[prescriptionId]/cancel
 *
 * Cancel a prescription.
 *
 * ODOO'S OWN WORKFLOW DECIDES WHETHER IT MAY BE CANCELLED, and this route does
 * not second-guess it. Slice 6A made action_cancel() authoritative: the base
 * guard permits cancellation only from draft or confirmed, hospital_pharmacy
 * then refuses outright if the linked dispense is partial or dispensed, cancels
 * the dispense when it is draft or ready, and hospital_billing cancels any
 * medication charges the pharmacist had already raised. Its refusal is
 * forwarded with its own wording, because that sentence is the only thing that
 * says WHY.
 *
 * THE CHARGE CLEANUP MATTERS EVEN THOUGH PRESCRIBING RAISED NO CHARGE. By the
 * time a doctor thinks to cancel, the pharmacist may already have marked the
 * dispense ready -- and that is the moment the charges appear. A cancellation
 * that left them live would strand the visit in the cashier's queue, collecting
 * for medication nobody will hand over.
 */
import type { DoctorPrescriptionResponse } from "@/types/doctor-medication";

import {
  DOCTOR_API,
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
} from "../../../../../../_utils";

export async function POST(
  _request: Request,
  context: {
    params: Promise<{ appointmentId: string; prescriptionId: string }>;
  },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId, prescriptionId } = await context.params;
  const visit = parseAppointmentId(appointmentId);
  if (!visit.ok) {
    return visit.response;
  }

  const prescription = Number(prescriptionId);
  if (!Number.isInteger(prescription) || prescription <= 0) {
    return errorResponse(
      "invalid_prescription_id",
      "Prescription ID is invalid.",
      400,
    );
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<DoctorPrescriptionResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${visit.value}/orders/medications/${prescription}/cancel`,
        "POST",
        {},
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "medication_cancel_failed",
      "Unable to cancel the prescription.",
    );
  }
}

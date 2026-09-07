/**
 * /api/doctor/visits/[appointmentId]/orders/medications
 *
 *   GET   the prescriptions written in this visit's consultation
 *   POST  write a new one
 *
 * NEITHER DECIDES ANYTHING, AND THE POST DECIDES NOTHING FINANCIAL. The body
 * carries medicines with their dose, route, frequency, duration and quantity,
 * an optional diagnosis, an optional note and an idempotency token. Patient,
 * physician, appointment and consultation are all derived server-side.
 *
 * ONE SUBMISSION IS ONE PRESCRIPTION, however many medicines it contains. The
 * domain models a prescription as a header with many lines, and confirmation
 * composes exactly one pharmacy dispense from it -- so writing one prescription
 * per medicine would send the patient to the counter once per drug.
 *
 * PRESCRIBING RAISES NO CHARGE, which is a real difference from the laboratory
 * and radiology routes next door. Medication is billed later, by the
 * pharmacist, at Mark Ready. No layer of this application creates a charge, and
 * this route does not know that charges exist.
 *
 * THE GET IS KEYED ON THE CONSULTATION, NOT THE VISIT STATE, so prescriptions
 * stay readable after the visit finishes -- which is the ordinary outpatient
 * shape, where the patient walks to the cashier and the pharmacy afterwards.
 */
import type { DoctorPrescriptionResponse } from "@/types/doctor-medication";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  readJsonObject,
  requireOdooSession,
} from "../../../../_utils";

type Context = { params: Promise<{ appointmentId: string }> };

export async function GET(_request: Request, context: Context) {
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
      await callOdooApi<DoctorPrescriptionResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/orders/medications`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "medication_orders_load_failed",
      "Unable to load the prescriptions for this consultation.",
    );
  }
}

export async function POST(request: Request, context: Context) {
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
      await callOdooApi<DoctorPrescriptionResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/orders/medications`,
        "POST",
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "medication_order_failed",
      "Unable to write the prescription.",
    );
  }
}

/**
 * /api/doctor/visits/[appointmentId]/diagnoses
 *
 *   GET   the diagnoses recorded in this visit's consultation
 *   POST  record a new one
 *
 * NEITHER DECIDES ANYTHING. The POST body carries a disease, the clinical
 * fields and a request token; patient, encounter, appointment, consultation and
 * physician are all derived server-side from the consultation. This route does
 * not check the one-primary rule, does not check the completion freeze and does
 * not decide who may write -- every one of those lives in
 * hospital.patient.diagnosis, which enforces them for the Odoo form and any RPC
 * caller as well as for this route.
 *
 * THE PRIMARY CONFLICT IS FORWARDED, NOT SMOOTHED. Odoo answers 409
 * `diagnosis_primary_exists` naming the diagnosis that already holds the slot.
 * That status and that sentence reach the browser unchanged: silently demoting
 * the existing primary to make room would rewrite a clinical judgement the
 * doctor never asked to change.
 */
import type { DoctorDiagnosisResponse } from "@/types/doctor-diagnosis";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  readJsonObject,
  requireOdooSession,
} from "../../../_utils";

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
      await callOdooApi<DoctorDiagnosisResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/diagnoses`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "diagnoses_load_failed",
      "Unable to load the diagnoses for this consultation.",
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
      await callOdooApi<DoctorDiagnosisResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/diagnoses`,
        "POST",
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "diagnosis_add_failed",
      "Unable to record the diagnosis.",
    );
  }
}

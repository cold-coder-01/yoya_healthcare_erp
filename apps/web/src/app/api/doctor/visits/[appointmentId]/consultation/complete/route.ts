/**
 * POST /api/doctor/visits/[appointmentId]/consultation/complete
 *
 * Completes the consultation. IT DECIDES NOTHING.
 *
 * A pass-through, for the same reason the save route is one. This layer does
 * NOT check who may complete, does NOT evaluate the clinical minimum, does NOT
 * compare the version and does NOT touch the appointment, the encounter or any
 * charge. All of that lives in hospital.consultation.action_complete(), which
 * enforces it for the Odoo form and any RPC caller as well as for this route.
 *
 * WHY THAT MATTERS MORE HERE THAN ON SAVE. Completion is irreversible: there is
 * no amendment or reopen workflow for a completed consultation. A second,
 * drifting copy of the completion rules in TypeScript could enable a button the
 * server refuses -- or, far worse, let a client believe a completion had been
 * validated by a check that no longer matches the server's.
 *
 * CONFLICTS AND REFUSALS ARE FORWARDED, NOT SMOOTHED. Odoo answers 409
 * `consultation_conflict` when the note changed after the client read it, 422
 * when the clinical minimum is unmet and 403 when the caller is not the
 * consultation's physician. Each status and sentence reaches the browser
 * unchanged; retrying here against a refreshed token would complete a note the
 * doctor never saw.
 */
import type { ConsultationCompleteResponse } from "@/types/doctor-consultation";

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
      await callOdooApi<ConsultationCompleteResponse>(
        session.sessionId,
        `${DOCTOR_API}/visits/${parsed.value}/consultation/complete`,
        "POST",
        body.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "consultation_complete_failed",
      "Unable to complete the consultation.",
    );
  }
}

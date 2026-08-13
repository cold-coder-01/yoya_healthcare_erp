import { parseAppointmentId } from "@/app/api/front-desk/_utils";
import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { FrontDeskVisit } from "@/types/front-desk";

/**
 * Claim the nursing evaluation for a visit: intake -> triage.
 *
 * A pure proxy. The Odoo endpoint finds or creates the one evaluation, calls
 * action_start_evaluation(), recovers from the unique(appointment_id) race and
 * returns the canonical visit detail -- including the DERIVED stage. Nothing is
 * re-decided here, and no body is sent: the appointment in the URL is the whole
 * request.
 */
export async function POST(
  _request: Request,
  context: { params: Promise<{ appointmentId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const { appointmentId: raw } = await context.params;
  const parsed = parseAppointmentId(raw);
  if (!parsed.ok) return parsed.response;

  try {
    return await forwardOdooResult(
      await callOdooApi<FrontDeskVisit>(
        session.sessionId,
        `/yoya-emr/api/v1/front-desk/visits/${parsed.value}/start-triage`,
        "POST",
        {},
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "front_desk_start_triage_failed",
      "Unable to start triage for this visit.",
    );
  }
}

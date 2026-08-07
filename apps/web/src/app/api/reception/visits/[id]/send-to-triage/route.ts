import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { ReceptionVisitDetail } from "@/types/reception";

/**
 * Release a visit to nursing triage.
 *
 * The clearance decision belongs entirely to Odoo, which gates on the
 * ENCOUNTER-WIDE reception clearance (consultation + patient card), not the
 * consultation-only billing_blocked signal. A 409 carries required /
 * received / outstanding amounts; normalizeError preserves those extra keys
 * so the client can render them.
 */
export async function POST(
  _request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { id } = await context.params;
  const appointmentId = Number(id);
  if (!Number.isInteger(appointmentId) || appointmentId <= 0) {
    return errorResponse("invalid_visit_id", "Visit ID is invalid.", 400);
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<ReceptionVisitDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/reception/visits/${appointmentId}/send-to-triage`,
        "POST",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "send_to_triage_failed",
      "Unable to send the visit to triage.",
    );
  }
}

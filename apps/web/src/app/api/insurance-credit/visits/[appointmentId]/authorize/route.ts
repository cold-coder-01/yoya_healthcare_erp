import { parseAppointmentId } from "@/app/api/front-desk/_utils";
import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { OfficerVisitDetail } from "@/types/insurance-credit";

/**
 * Authorize sponsor shares for selected charges.
 *
 * Proxies the Odoo endpoint and validates nothing about the AMOUNTS. Every
 * limit, permitted figure and freeze rule is re-derived server-side inside a
 * lock; a check here would be a second opinion that is stale by construction,
 * because the browser's numbers were rendered before the request was sent.
 *
 * The idempotency key is the CLIENT's and is forwarded unchanged: minting one
 * here would let a browser retry authorize a second sponsor share.
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

  const decisions = payload.body.decisions;
  if (!Array.isArray(decisions) || decisions.length === 0) {
    return errorResponse(
      "decisions_required",
      "Select at least one charge to authorize.",
      400,
    );
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<OfficerVisitDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/insurance-credit/visits/${parsed.value}/authorize`,
        "POST",
        {
          decisions,
          idempotency_key: payload.body.idempotency_key ?? null,
        },
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "insurance_credit_authorize_failed",
      "Unable to record the authorization.",
    );
  }
}

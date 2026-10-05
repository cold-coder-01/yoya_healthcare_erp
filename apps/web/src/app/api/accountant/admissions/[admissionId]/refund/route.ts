import { parseAdmissionId } from "@/app/api/admissions/_utils";
import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { AccountantDetail } from "@/types/accountant";

/**
 * Record a refund of unapplied inpatient credit -- discharged stays
 * included. AN ACCOUNTING ACT.
 *
 * Exactly {amount, reason, idempotency_key} leave this route; a body naming
 * anything else is rebuilt, not forwarded. Odoo re-checks the role, that care
 * is over, the settlement state and the refundable balance under the
 * admission's row lock, and replays the same key instead of refunding twice.
 */
export async function POST(
  request: Request,
  context: { params: Promise<{ admissionId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const { admissionId: raw } = await context.params;
  const parsed = parseAdmissionId(raw);
  if (!parsed.ok) return parsed.response;

  const payload = await readJsonObject(request);
  if (!payload.ok) return payload.response;

  const body = payload.body;
  const idempotencyKey = body.idempotency_key;
  if (typeof idempotencyKey !== "string" || !idempotencyKey.trim()) {
    return errorResponse(
      "idempotency_key_required",
      "A refund must carry an idempotency key so a retry cannot refund twice.",
      400,
    );
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<AccountantDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/accountant/admissions/${parsed.value}/refund`,
        "POST",
        {
          amount: body.amount,
          reason: body.reason,
          idempotency_key: idempotencyKey.trim(),
        },
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "accountant_refund_failed",
      "Unable to record the refund.",
    );
  }
}

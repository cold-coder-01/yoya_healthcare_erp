import { parseAppointmentId } from "@/app/api/front-desk/_utils";
import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { CashierPaymentResult } from "@/types/cashier";

/**
 * Record actual patient cash against a visit.
 *
 * Proxies the existing authoritative Odoo endpoint. Everything that decides
 * whether the money may be taken -- the intake group check, the patient
 * responsibility ceiling under enforce, the reference requirement, the
 * savepoint that rolls a payment back if its response cannot be built -- lives
 * in hospital_billing and yoya_emr_api. None of it is repeated here, so Odoo's
 * own message and error code reach the desk intact.
 *
 * THE IDEMPOTENCY KEY IS THE CLIENT'S, and is forwarded unchanged. Minting one
 * here would defeat it entirely: a browser retry would arrive with a fresh key
 * and take a second payment. This route validates only that it is present.
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

  const body = payload.body;
  const idempotencyKey = body.idempotency_key;
  if (typeof idempotencyKey !== "string" || !idempotencyKey.trim()) {
    return errorResponse(
      "idempotency_key_required",
      "A payment must carry an idempotency key so a retry cannot charge twice.",
      400,
    );
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<CashierPaymentResult>(
        session.sessionId,
        `/yoya-emr/api/v1/cashier/visits/${parsed.value}/payment`,
        "POST",
        {
          amount: body.amount,
          payment_method: body.payment_method,
          payment_reference: body.payment_reference ?? null,
          note: body.note ?? null,
          idempotency_key: idempotencyKey.trim(),
        },
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "cashier_payment_failed",
      "Unable to record the payment.",
    );
  }
}

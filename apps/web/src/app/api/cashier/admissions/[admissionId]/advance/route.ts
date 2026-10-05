import { parseAdmissionId } from "@/app/api/admissions/_utils";
import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { CashierInpatientPaymentResult } from "@/types/cashier";

/**
 * Take an inpatient ADVANCE against the physician's estimate.
 *
 * THE BODY IS REBUILT, NOT FORWARDED: amount, payment_method,
 * payment_reference, note, idempotency_key. The admission, the deposit charge,
 * the estimate and the uncovered amount are all derived in Odoo, which caps
 * the advance at the estimate still uncovered.
 *
 * THE IDEMPOTENCY KEY IS THE CLIENT'S, forwarded unchanged.
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
      "An advance must carry an idempotency key so a retry cannot charge twice.",
      400,
    );
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<CashierInpatientPaymentResult>(
        session.sessionId,
        `/yoya-emr/api/v1/cashier/admissions/${parsed.value}/advance`,
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
      "cashier_inpatient_advance_failed",
      "Unable to record the advance.",
    );
  }
}

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
 * Settle an inpatient account (all or part of the remaining balance).
 *
 * THE BODY IS REBUILT, NOT FORWARDED. Exactly five fields leave this route:
 * amount, payment_method, payment_reference, note, idempotency_key. The
 * admission, visit, billing account, remaining balance, patient share and
 * financial state are all derived in Odoo -- a browser that sends them is
 * simply not heard, and Odoo refuses the request if they reach it by any other
 * path.
 *
 * THE IDEMPOTENCY KEY IS THE CLIENT'S, forwarded unchanged: a key minted here
 * would make every browser retry a new payment.
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
      "A payment must carry an idempotency key so a retry cannot charge twice.",
      400,
    );
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<CashierInpatientPaymentResult>(
        session.sessionId,
        `/yoya-emr/api/v1/cashier/admissions/${parsed.value}/payment`,
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
      "cashier_inpatient_payment_failed",
      "Unable to record the payment.",
    );
  }
}

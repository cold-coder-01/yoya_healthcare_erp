import { parseAdmissionId } from "@/app/api/admissions/_utils";
import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { CashierInpatientDetail } from "@/types/cashier";

/**
 * Record the refund of unapplied inpatient credit. AN ACCOUNTING ACT.
 *
 * Exactly {amount, reason, idempotency_key} leave this route. Odoo refuses
 * the Cashier (403) and admits the Accountant, Hospital Manager and System
 * Administrator; it also caps the refund at the unapplied credit and records
 * it without touching delivered care, invoices or revenue.
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
      await callOdooApi<CashierInpatientDetail & { replayed: boolean }>(
        session.sessionId,
        `/yoya-emr/api/v1/cashier/admissions/${parsed.value}/refund`,
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
      "cashier_inpatient_refund_failed",
      "Unable to record the refund.",
    );
  }
}

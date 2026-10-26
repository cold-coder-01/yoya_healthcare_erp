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
 * Post the accounting of refunds recorded BEFORE refund accounting existed.
 * AN ACCOUNTING ACT.
 *
 * Exactly {payment_method, idempotency_key} -- plus payment_reference and
 * accounting_date when given -- leave this route. The method is the
 * Accountant's statement of how the earlier refund was paid; nothing is
 * assumed here or in Odoo, which maps it to the company's ledger and replays
 * the same key instead of posting twice.
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
      "Posting must carry an idempotency key so a retry cannot post twice.",
      400,
    );
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<AccountantDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/accountant/admissions/${parsed.value}/post-legacy-accounting`,
        "POST",
        {
          payment_method: body.payment_method,
          ...(typeof body.payment_reference === "string" && body.payment_reference.trim()
            ? { payment_reference: body.payment_reference.trim() }
            : {}),
          ...(typeof body.accounting_date === "string" && body.accounting_date.trim()
            ? { accounting_date: body.accounting_date.trim() }
            : {}),
          idempotency_key: idempotencyKey.trim(),
        },
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "accountant_post_legacy_accounting_failed",
      "Unable to post legacy accounting.",
    );
  }
}

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
 * Post a DISCHARGED stay's accounting (receipts, delivered-care invoice,
 * advance application). AN ACCOUNTING ACT.
 *
 * Exactly {idempotency_key} leaves this route. Odoo decides everything --
 * whether the stay may be posted, what is posted and to which ledger -- and
 * replays the same key instead of posting twice.
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

  const idempotencyKey = payload.body.idempotency_key;
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
        `/yoya-emr/api/v1/accountant/admissions/${parsed.value}/post-accounting`,
        "POST",
        { idempotency_key: idempotencyKey.trim() },
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "accountant_post_accounting_failed",
      "Unable to post accounting.",
    );
  }
}

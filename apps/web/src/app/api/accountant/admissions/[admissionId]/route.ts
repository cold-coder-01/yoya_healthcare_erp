import { parseAdmissionId } from "@/app/api/admissions/_utils";
import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { AccountantDetail } from "@/types/accountant";

/**
 * One inpatient account for Finance: identity, the stay's workflow facts,
 * the server's settlement, payments in and refunds out. Discharged stays
 * included. Hidden and missing are the same 404.
 */
export async function GET(
  _request: Request,
  context: { params: Promise<{ admissionId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  const { admissionId: raw } = await context.params;
  const parsed = parseAdmissionId(raw);
  if (!parsed.ok) return parsed.response;

  try {
    return await forwardOdooResult(
      await callOdooApi<AccountantDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/accountant/admissions/${parsed.value}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "accountant_detail_failed",
      "Unable to load the selected inpatient account.",
    );
  }
}

import { parseAdmissionId } from "@/app/api/admissions/_utils";
import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { CashierInpatientDetail } from "@/types/cashier";

/**
 * One inpatient account for the Cashier: identity, location, the
 * delivered-basis figures by category and the collect verdict.
 *
 * A pass-through. Whether this admission is visible to the caller, and every
 * figure in it, is decided in Odoo; hidden and missing are the same 404.
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
      await callOdooApi<CashierInpatientDetail>(
        session.sessionId,
        `/yoya-emr/api/v1/cashier/admissions/${parsed.value}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "cashier_inpatient_failed",
      "Unable to load the selected inpatient account.",
    );
  }
}

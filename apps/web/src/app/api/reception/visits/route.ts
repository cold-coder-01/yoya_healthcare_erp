import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { ReceptionVisitDetail } from "@/types/reception";

/**
 * Guided visit registration.
 *
 * The body is forwarded as-is to the Odoo reception endpoint, which owns all
 * validation (allowlist, patient_id XOR patient_values, doctor-department
 * consistency) and performs the whole registration in one transaction. This
 * route creates no patient, appointment, encounter, card issuance or charge.
 */
export async function POST(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const parsed = await readJsonObject(request);
  if (!parsed.ok) {
    return parsed.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<ReceptionVisitDetail>(
        session.sessionId,
        "/yoya-emr/api/v1/reception/visits",
        "POST",
        parsed.body,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "create_visit_failed",
      "Unable to register the visit.",
    );
  }
}

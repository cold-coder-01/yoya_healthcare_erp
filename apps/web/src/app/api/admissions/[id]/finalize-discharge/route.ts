/**
 * POST /api/admissions/[id]/finalize-discharge
 *
 * The administrative final discharge (Admissions Slice 4). Forwards exactly
 * {operation_token, expected_revision}, rebuilt by pickFinalizeDischargeBody.
 *
 * THE ROUTE DECIDES NOTHING. Odoo checks the discharging role before it reads
 * the admission; hospital.admission._desk_finalize_discharge() re-checks the
 * doctor's readiness, bed ownership and the visit, posts the final stay,
 * applies the settlement gate, releases the bed and completes the visit -- as
 * one transaction. Fixed error codes come back as-is.
 */
import type { AdmissionFinalizeDischargeResponse } from "@/types/admissions-desk";

import {
  ADMISSIONS_API,
  forwardOdooResult,
  handleRouteError,
  parseAdmissionId,
  pickFinalizeDischargeBody,
  postOdooApiWithBody,
  readMutationBody,
  requireOdooSession,
} from "../../_utils";

export async function POST(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { id } = await context.params;
  const parsed = parseAdmissionId(id);
  if (!parsed.ok) {
    return parsed.response;
  }

  const body = await readMutationBody(request);
  if (!body.ok) {
    return body.response;
  }

  try {
    return await forwardOdooResult(
      await postOdooApiWithBody<AdmissionFinalizeDischargeResponse>(
        session.sessionId,
        `${ADMISSIONS_API}/${parsed.value}/finalize-discharge`,
        pickFinalizeDischargeBody(body.body),
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "admission_finalize_discharge_failed",
      "Unable to reach the admissions service. Retry to finish the same request safely.",
    );
  }
}

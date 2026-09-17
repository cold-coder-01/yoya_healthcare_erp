/**
 * POST /api/laboratory/results/[resultId]/validate
 *
 * Validate an ENTERED result: entered -> validated.
 *
 * THIS ROUTE DECIDES NOTHING. It forwards to
 * /yoya-emr/api/v1/lab/results/<id>/validate, where the Lab API re-checks the
 * desk's policy under lock (request in_progress, one result, result entered)
 * and calls hospital.laboratory.result.action_validate() in one savepoint.
 * That method delivers the test to billing; nothing about billing is known or
 * restated here.
 *
 * THE BODY IS EMPTY AND CARRIES NOTHING. Validation changes no value, and the
 * upstream handler reads no field -- so the browser's body is not even read,
 * and a fixed `{}` is sent.
 *
 * REFUSALS ARE FORWARDED AS ODOO SENT THEM. The Lab API has already replaced
 * every billing-side refusal with a fixed, money-free sentence
 * (`lab_result_validation_blocked`); forwarding unchanged is what keeps that
 * guarantee in one place.
 */
import type { LabResultResponse } from "@/types/lab-desk";

import {
  LAB_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseResultId,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
  _request: Request,
  context: { params: Promise<{ resultId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { resultId } = await context.params;
  const parsed = parseResultId(resultId);
  if (!parsed.ok) {
    return parsed.response;
  }

  try {
    return await forwardOdooResult(
      await callOdooApi<LabResultResponse>(
        session.sessionId,
        `${LAB_API}/results/${parsed.value}/validate`,
        "POST",
        {},
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_result_validate_failed",
      "Unable to validate the laboratory result.",
    );
  }
}

import { saveEvaluation } from "@/lib/odoo-client";
import type { EvaluationSavePayload } from "@/types/clinical";
import {
  forwardOdooResult,
  handleRouteError,
  parsePositiveInteger,
  readJsonObject,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
  request: Request,
  context: { params: Promise<{ appointmentId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId } = await context.params;
  const parsed = parsePositiveInteger(appointmentId, "Appointment ID");
  if (!parsed.ok) {
    return parsed.response;
  }

  const payload = await readJsonObject(request);
  if (!payload.ok) {
    return payload.response;
  }

  try {
    return forwardOdooResult(
      await saveEvaluation(
        session.sessionId,
        parsed.value,
        payload.body as EvaluationSavePayload,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "evaluation_save_failed",
      "Unable to save evaluation.",
    );
  }
}

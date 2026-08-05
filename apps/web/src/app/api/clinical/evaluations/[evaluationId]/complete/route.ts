import { completeEvaluation } from "@/lib/odoo-client";
import {
  forwardOdooResult,
  handleRouteError,
  parsePositiveInteger,
  requireOdooSession,
} from "../../../_utils";

export async function POST(
  _request: Request,
  context: { params: Promise<{ evaluationId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { evaluationId } = await context.params;
  const parsed = parsePositiveInteger(evaluationId, "Evaluation ID");
  if (!parsed.ok) {
    return parsed.response;
  }

  try {
    return forwardOdooResult(
      await completeEvaluation(session.sessionId, parsed.value),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "evaluation_complete_failed",
      "Unable to complete evaluation.",
    );
  }
}

import { fetchEvaluationQueue } from "@/lib/odoo-client";
import {
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "../_utils";

const FORWARDED_PARAMS = ["date", "state", "department_id", "doctor_id"] as const;

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const url = new URL(request.url);
  const filters: Record<string, string | undefined> = {};
  for (const param of FORWARDED_PARAMS) {
    filters[param] = url.searchParams.get(param) ?? undefined;
  }

  try {
    return forwardOdooResult(
      await fetchEvaluationQueue(session.sessionId, filters),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "evaluation_queue_failed",
      "Unable to load evaluation queue.",
    );
  }
}

import { fetchClinicalSession } from "@/lib/odoo-client";
import {
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "../_utils";

export async function GET() {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  try {
    return forwardOdooResult(await fetchClinicalSession(session.sessionId));
  } catch (error) {
    return handleRouteError(
      error,
      "clinical_session_failed",
      "Unable to load clinical session.",
    );
  }
}

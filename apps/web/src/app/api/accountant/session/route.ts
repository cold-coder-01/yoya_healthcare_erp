import {
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";
import type { AccountantSession } from "@/types/accountant";

/**
 * Who is signed in and what the Accountant Desk may offer them. A
 * pass-through; Odoo decides every capability.
 */
export async function GET() {
  const session = await requireOdooSession();
  if (!session.ok) return session.response;

  try {
    return await forwardOdooResult(
      await callOdooApi<AccountantSession>(
        session.sessionId,
        "/yoya-emr/api/v1/accountant/session",
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "accountant_session_failed",
      "Unable to load the Accountant Desk session.",
    );
  }
}

/**
 * GET /api/pharmacy/session
 *
 * Who is signed in, which Pharmacy Desk role they hold, and whether the desk
 * opens for them. PRESENTATION ONLY: the worklist and detail endpoints enforce
 * the desk role themselves, and the upstream session route is gated too.
 */
import type { PharmacySessionResponse } from "@/types/pharmacy-desk";

import {
  PHARMACY_API,
  callOdooApi,
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
    return await forwardOdooResult(
      await callOdooApi<PharmacySessionResponse>(
        session.sessionId,
        `${PHARMACY_API}/session`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "pharmacy_session_failed",
      "Unable to load your session.",
    );
  }
}

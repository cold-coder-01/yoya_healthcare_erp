/**
 * GET /api/radiology/session
 *
 * Who is signed in, which Radiology Desk role they hold, and whether the desk
 * opens for them.
 *
 * PRESENTATION ONLY. The flags gate hints, never data: the worklist and detail
 * endpoints enforce the desk role themselves. The upstream session route is
 * gated too, so a refused role gets a 403 the shell can explain.
 */
import type { RadSessionResponse } from "@/types/rad-desk";

import {
  RADIOLOGY_API,
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
      await callOdooApi<RadSessionResponse>(
        session.sessionId,
        `${RADIOLOGY_API}/session`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_session_failed",
      "Unable to load your session.",
    );
  }
}

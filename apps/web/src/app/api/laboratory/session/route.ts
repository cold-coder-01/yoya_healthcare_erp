/**
 * GET /api/laboratory/session
 *
 * Who is signed in and whether the Laboratory Desk opens for them.
 *
 * PRESENTATION ONLY. `capabilities.lab_desk` gates hints, never data: the
 * worklist and detail endpoints enforce the desk role themselves, and every
 * row they return is scoped by Odoo record rules regardless of what this
 * endpoint reports.
 *
 * The upstream route enforces the same role gate as the data endpoints.
 * Its 403 lets the shell explain the refusal.
 */
import type { LabSessionResponse } from "@/types/lab-desk";

import {
  LAB_API,
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
      await callOdooApi<LabSessionResponse>(
        session.sessionId,
        `${LAB_API}/session`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_session_failed",
      "Unable to load your session.",
    );
  }
}

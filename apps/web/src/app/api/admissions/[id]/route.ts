/**
 * GET /api/admissions/[id]
 *
 * One admission, read-only. A missing admission and one the caller's record
 * rules hide are the same 404 from Odoo; a caller outside the desk roles is 403
 * before existence is evaluated. Neither answer is computed here.
 */
import type { AdmissionDetailResponse } from "@/types/admissions-desk";

import {
  ADMISSIONS_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  parseAdmissionId,
  requireOdooSession,
} from "../_utils";

export async function GET(
  _request: Request,
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

  try {
    return await forwardOdooResult(
      await callOdooApi<AdmissionDetailResponse>(
        session.sessionId,
        `${ADMISSIONS_API}/${parsed.value}`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "admission_detail_failed",
      "Unable to load the selected admission.",
    );
  }
}

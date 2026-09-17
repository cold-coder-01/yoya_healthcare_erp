/**
 * GET /api/radiology/worklist
 *
 * The imaging department's queue.
 *
 * THE ROLE GATE IS NOT SET HERE. This forwards to
 * /yoya-emr/api/v1/radiology/worklist, which calls reception_scope.may_rad_desk()
 * before it touches a record and answers 403 for every role outside
 * RAD_DESK_GROUPS -- including the nurse, receptionist and doctor, who hold a
 * read ACL on the radiology models. This route adds no check and cannot widen
 * what Odoo returned.
 *
 * The query string is forwarded verbatim. Upstream parses and refuses its own
 * parameters (`status`, `date`, `q`, `modality`, `limit`), and its refusal names
 * the valid values.
 */
import type { RadWorklistResponse } from "@/types/rad-desk";

import {
  RADIOLOGY_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "../_utils";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const url = new URL(request.url);

  try {
    return await forwardOdooResult(
      await callOdooApi<RadWorklistResponse>(
        session.sessionId,
        `${RADIOLOGY_API}/worklist${url.search}`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_worklist_failed",
      "Unable to load the radiology queue.",
    );
  }
}

/**
 * GET /api/laboratory/worklist
 *
 * The laboratory bench queue.
 *
 * THE ROLE GATE IS NOT SET HERE. This forwards to /yoya-emr/api/v1/lab/worklist,
 * which calls services/reception_scope.may_lab_desk() before it touches a
 * record and answers 403 for every role outside LAB_DESK_GROUPS -- including
 * the nurse and receptionist, who hold a read ACL on the laboratory models but
 * have no business at this workstation. This route adds no check of its own
 * and cannot widen what Odoo returned.
 *
 * The query string is forwarded verbatim. The upstream endpoint parses and
 * rejects its own parameters (`status`, `date`, `q`, `limit`), and its refusal
 * names the valid values, which is better than a message invented here. There
 * is deliberately no patient_id or department_id parameter upstream, so
 * passing one changes nothing.
 */
import type { LabWorklistResponse } from "@/types/lab-desk";

import {
  LAB_API,
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
      await callOdooApi<LabWorklistResponse>(
        session.sessionId,
        `${LAB_API}/worklist${url.search}`,
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "lab_worklist_failed",
      "Unable to load the laboratory queue.",
    );
  }
}

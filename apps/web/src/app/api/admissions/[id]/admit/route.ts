/**
 * POST /api/admissions/[id]/admit
 *
 * Put a draft admission into a bed: assign the bed and confirm the admission
 * as ONE act. Forwards exactly {operation_token, expected_revision, bed_id},
 * rebuilt by pickAdmitBody. The ward and room come from the bed, in Odoo.
 *
 * THE ROUTE DECIDES NOTHING. Odoo checks the admitting role before it reads
 * the admission, then hospital.admission._desk_admit() owns the locks, the
 * revision, the idempotent replay and the Slice 0 confirmation. Fixed error
 * codes come back as-is.
 */
import type { AdmissionAdmitResponse } from "@/types/admissions-desk";

import {
  ADMISSIONS_API,
  forwardOdooResult,
  handleRouteError,
  parseAdmissionId,
  pickAdmitBody,
  postOdooApiWithBody,
  readMutationBody,
  requireOdooSession,
} from "../../_utils";

export async function POST(
  request: Request,
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

  const body = await readMutationBody(request);
  if (!body.ok) {
    return body.response;
  }

  try {
    return await forwardOdooResult(
      await postOdooApiWithBody<AdmissionAdmitResponse>(
        session.sessionId,
        `${ADMISSIONS_API}/${parsed.value}/admit`,
        pickAdmitBody(body.body),
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "admission_admit_failed",
      "Unable to reach the admissions service. Retry to finish the same request safely.",
    );
  }
}

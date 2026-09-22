/**
 * POST /api/admissions/[id]/transfer
 *
 * Move an admitted patient to another bed (Admissions Slice 3). Forwards
 * exactly {operation_token, expected_revision, bed_id, reason}, rebuilt by
 * pickTransferBody. The destination ward and room come from the bed, in Odoo.
 *
 * THE ROUTE DECIDES NOTHING. Odoo checks the transferring role before it reads
 * the admission, then hospital.admission._desk_transfer() owns the locks, the
 * revision, the idempotent replay, the bed checks and the Slice 0 transition
 * (release, occupy, immutable history, destination rate snapshot). Fixed error
 * codes come back as-is.
 */
import type { AdmissionTransferResponse } from "@/types/admissions-desk";

import {
  ADMISSIONS_API,
  forwardOdooResult,
  handleRouteError,
  parseAdmissionId,
  pickTransferBody,
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
      await postOdooApiWithBody<AdmissionTransferResponse>(
        session.sessionId,
        `${ADMISSIONS_API}/${parsed.value}/transfer`,
        pickTransferBody(body.body),
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "admission_transfer_failed",
      "Unable to reach the admissions service. Retry to finish the same request safely.",
    );
  }
}

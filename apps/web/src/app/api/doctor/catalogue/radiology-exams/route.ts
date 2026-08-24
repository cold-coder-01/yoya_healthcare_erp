/**
 * GET /api/doctor/catalogue/radiology-exams?q=&limit=
 *
 * The radiology exam picker's search. Reference data, read only.
 *
 * The cap is Odoo's: `limit` is forwarded and CLAMPED server-side, so a client
 * cannot widen it here or there. Nothing is filtered or trimmed in this layer,
 * because a browser trimming a full table dump is the problem a server-side
 * search exists to avoid.
 *
 * ODOO ALSO DECIDES WHAT IS ORDERABLE. It returns only exams whose billing
 * configuration would survive confirmation, which is most of the point of this
 * endpoint -- most of the shipped radiology catalogue is unmapped, and offering
 * one of those would offer an action the very next step refuses. That predicate
 * is the model's and is never restated here.
 */
import type { RadCatalogueResponse } from "@/types/doctor-radiology";

import {
  DOCTOR_API,
  callOdooApi,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
  withQuery,
} from "../../_utils";

export async function GET(request: Request) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const params = new URL(request.url).searchParams;

  try {
    return await forwardOdooResult(
      await callOdooApi<RadCatalogueResponse>(
        session.sessionId,
        withQuery(`${DOCTOR_API}/catalogue/radiology-exams`, {
          q: params.get("q") ?? undefined,
          limit: params.get("limit") ?? undefined,
        }),
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_catalogue_failed",
      "Unable to search the radiology catalogue.",
    );
  }
}

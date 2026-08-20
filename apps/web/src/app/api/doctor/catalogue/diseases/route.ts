/**
 * GET /api/doctor/catalogue/diseases?q=&limit=
 *
 * The disease picker's search. Reference data, read only.
 *
 * The cap is Odoo's, not this route's: `limit` is forwarded and CLAMPED
 * server-side, so a client cannot widen it here or there. This layer does not
 * filter, rank or trim the result, because a browser trimming a full table
 * dump is the problem the server-side search exists to avoid.
 */
import type { DiseaseCatalogueResponse } from "@/types/doctor-diagnosis";

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
      await callOdooApi<DiseaseCatalogueResponse>(
        session.sessionId,
        withQuery(`${DOCTOR_API}/catalogue/diseases`, {
          q: params.get("q") ?? undefined,
          limit: params.get("limit") ?? undefined,
        }),
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "catalogue_failed",
      "Unable to search the diagnosis catalogue.",
    );
  }
}

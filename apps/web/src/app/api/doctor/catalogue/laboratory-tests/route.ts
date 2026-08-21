/**
 * GET /api/doctor/catalogue/laboratory-tests?q=&limit=
 *
 * The laboratory test picker's search. Reference data, read only.
 *
 * The cap is Odoo's: `limit` is forwarded and CLAMPED server-side, so a client
 * cannot widen it here or there. Nothing is filtered or trimmed in this layer,
 * because a browser trimming a full table dump is the problem a server-side
 * search exists to avoid.
 */
import type { LabCatalogueResponse } from "@/types/doctor-laboratory";

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
      await callOdooApi<LabCatalogueResponse>(
        session.sessionId,
        withQuery(`${DOCTOR_API}/catalogue/laboratory-tests`, {
          q: params.get("q") ?? undefined,
          limit: params.get("limit") ?? undefined,
        }),
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "laboratory_catalogue_failed",
      "Unable to search the laboratory catalogue.",
    );
  }
}

/**
 * GET /api/doctor/catalogue/medicines?q=&limit=
 *
 * The medicine picker's search. Reference data, read only.
 *
 * The cap is Odoo's: `limit` is forwarded and CLAMPED server-side, so a client
 * cannot widen it here or there. Nothing is filtered or trimmed in this layer,
 * because a browser trimming a full formulary is the problem a server-side
 * search exists to avoid.
 *
 * ODOO ALSO DECIDES WHAT IS PRESCRIBABLE, and for medication that predicate
 * guards more than a tidy picker. It returns only medicines that BOTH carry a
 * billing service the pharmacist's Mark Ready will accept AND an inventory
 * mapping that Validate Dispense will accept. A medicine failing the second is
 * the dangerous one: it would be prescribed, priced and PAID FOR before
 * anything refused. That predicate is the model's and is never restated here.
 *
 * NOTHING PRICED COMES BACK. hospital.pharmacy.medicine carries sale_price on
 * the record itself; the serializer never emits it.
 */
import type { MedCatalogueResponse } from "@/types/doctor-medication";

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
      await callOdooApi<MedCatalogueResponse>(
        session.sessionId,
        withQuery(`${DOCTOR_API}/catalogue/medicines`, {
          q: params.get("q") ?? undefined,
          limit: params.get("limit") ?? undefined,
        }),
        "GET",
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "medicine_catalogue_failed",
      "Unable to search the medicine catalogue.",
    );
  }
}

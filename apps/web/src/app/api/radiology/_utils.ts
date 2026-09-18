/**
 * Shared bits for the Radiology Desk BFF routes.
 *
 * `server-only` keeps these -- and the Odoo session cookie they read -- out of
 * every client bundle. The browser never holds an Odoo session and never has a
 * reachable Odoo URL; it talks to /api/radiology/* and nothing else.
 *
 * The generic helpers are imported from the reception BFF rather than copied,
 * the pattern laboratory/_utils.ts and doctor/_utils.ts follow. This desk does
 * NOT reuse any Doctor-specific loader: the Doctor Desk resolves visits and
 * consultations through a doctor's own scope, and none of that applies here.
 *
 * TWO WRITE ROUTES (Slice 2): schedule and start. Both are BODILESS -- the
 * request is identified by the URL and nothing else is accepted -- so there is
 * still no body reader here, and still no binary stream helper, because this
 * desk serves no image bytes.
 */
import "server-only";

import {
  callOdooApi as callOdooApiWithLabel,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";

export { errorResponse, forwardOdooResult, handleRouteError, requireOdooSession };

/**
 * How this desk names the upstream service in fallback wording, so an HTML 404
 * from a stale Odoo process tells the user about the radiology service rather
 * than the reception one -- the Laboratory Desk's UAT defect, not repeated.
 */
const RADIOLOGY_SERVICE_LABEL = "radiology service";

/**
 * callOdooApi, bound to this desk's service label. Every /api/radiology/* route
 * imports THIS, so a route added later cannot forget the label.
 */
export function callOdooApi<T>(sessionId: string, path: string) {
  return callOdooApiWithLabel<T>(
    sessionId,
    path,
    "GET",
    undefined,
    RADIOLOGY_SERVICE_LABEL,
  );
}

/**
 * A BODILESS POST, bound to the same label. Sends `{}`, the convention every
 * POST route in this app follows; the upstream handler reads no field from it,
 * and nothing a browser sends is forwarded.
 */
export function postOdooApi<T>(sessionId: string, path: string) {
  return callOdooApiWithLabel<T>(
    sessionId,
    path,
    "POST",
    {},
    RADIOLOGY_SERVICE_LABEL,
  );
}

/** Every Radiology Desk route hangs off this one Odoo prefix. */
export const RADIOLOGY_API = "/yoya-emr/api/v1/radiology";

/**
 * Parse the `[requestId]` segment. Only what is needed to build a safe
 * upstream URL; whether the request exists, is readable, or may be opened by
 * this user are all Odoo's decisions.
 */
export function parseRequestId(raw: string) {
  const requestId = Number(raw);
  if (!Number.isInteger(requestId) || requestId <= 0) {
    return {
      ok: false as const,
      response: errorResponse(
        "invalid_request_id",
        "Radiology request ID is invalid.",
        400,
      ),
    };
  }
  return { ok: true as const, value: requestId };
}

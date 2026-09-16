/**
 * Shared bits for the Laboratory Desk BFF routes.
 *
 * `server-only` keeps these -- and the Odoo session cookie they read -- out of
 * every client bundle. The browser never holds an Odoo session and never has a
 * reachable Odoo URL; it talks to /api/laboratory/* and nothing else.
 *
 * The generic helpers are imported from the reception BFF rather than copied,
 * which is the pattern doctor/_utils.ts and front-desk/_utils.ts both follow.
 *
 * `readJsonObject` ARRIVED WITH RESULT ENTRY (Slice 3), for the two routes that
 * carry a body (results/<id>/save and results/<id>/enter). It only checks that
 * the body IS a JSON object; WHICH fields are writable is the Lab API's
 * allow-list and is not duplicated here. `streamOdooBinary` stays absent: there
 * is no binary on this desk.
 */
import "server-only";

import {
  callOdooApi as callOdooApiWithLabel,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
} from "@/app/api/reception/_utils";

export {
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
};

/**
 * How this desk names the upstream service in fallback wording.
 *
 * THE DEFECT THIS FIXES, OBSERVED IN UAT. The shared helpers hardcoded
 * "reception service" in the one message they fall back to when Odoo returns a
 * body that is NOT the JSON envelope -- an HTML 404 from the router, or an
 * error page. When a laboratory route was missing from a stale server, the Lab
 * Desk told the technician "The reception service returned an error", which
 * names the wrong workstation and sends them looking in the wrong place.
 *
 * The label is now a parameter with a reception-flavoured default, so no other
 * desk's wording changed; this module binds its own once, here, and every
 * laboratory route inherits it without knowing about it.
 */
const LAB_SERVICE_LABEL = "laboratory service";

/**
 * callOdooApi, bound to this desk's service label.
 *
 * Every /api/laboratory/* route imports THIS rather than the shared helper, so
 * a route added later cannot forget the label and reintroduce the defect.
 */
export function callOdooApi<T>(
  sessionId: string,
  path: string,
  method: "GET" | "POST",
  body?: unknown,
) {
  return callOdooApiWithLabel<T>(sessionId, path, method, body, LAB_SERVICE_LABEL);
}

/** Every Laboratory Desk route hangs off this one Odoo prefix. */
export const LAB_API = "/yoya-emr/api/v1/lab";

/**
 * Parse the `[requestId]` segment.
 *
 * The BFF validates only what it must to build a safe upstream URL. Whether
 * the request exists, whether this user may open the bench, and whether they
 * may read this particular row are all Odoo's decisions and are never
 * second-guessed here -- duplicating them would create a second source of
 * truth that drifts from the one that actually protects the data.
 */
export function parseRequestId(raw: string) {
  const requestId = Number(raw);
  if (!Number.isInteger(requestId) || requestId <= 0) {
    return {
      ok: false as const,
      response: errorResponse(
        "invalid_request_id",
        "Laboratory request ID is invalid.",
        400,
      ),
    };
  }
  return { ok: true as const, value: requestId };
}

/**
 * Parse the `[resultId]` segment. The same rule as parseRequestId: only what is
 * needed to build a safe upstream URL. Whether the result exists, is readable,
 * and is still a draft are all Odoo's decisions.
 */
export function parseResultId(raw: string) {
  const resultId = Number(raw);
  if (!Number.isInteger(resultId) || resultId <= 0) {
    return {
      ok: false as const,
      response: errorResponse(
        "invalid_result_id",
        "Laboratory result ID is invalid.",
        400,
      ),
    };
  }
  return { ok: true as const, value: resultId };
}

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
 * SLICE 1 EXPORTS NO WRITE HELPER. `readJsonObject` and `streamOdooBinary` are
 * deliberately absent: there is no POST route and no binary in the Laboratory
 * Desk yet, and re-exporting a helper nothing calls invites a route that
 * should not exist. They arrive with the slices that need them.
 */
import "server-only";

import {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/reception/_utils";

export {
  callOdooApi,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  requireOdooSession,
};

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

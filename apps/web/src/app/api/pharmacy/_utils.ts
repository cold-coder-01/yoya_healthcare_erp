/**
 * Shared bits for the Pharmacy Desk BFF routes. READ ONLY (Pharmacy Slice 1).
 *
 * `server-only` keeps these -- and the Odoo session cookie they read -- out of
 * every client bundle. The browser never holds an Odoo session and never has a
 * reachable Odoo URL; it talks to /api/pharmacy/* and nothing else.
 *
 * The generic helpers are imported from the reception BFF rather than copied,
 * the pattern radiology/_utils.ts and laboratory/_utils.ts follow. No write
 * helper is exported: this desk has no mutation route yet.
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

/** How this desk names the upstream service in fallback wording. */
const PHARMACY_SERVICE_LABEL = "pharmacy service";

/** callOdooApi bound to GET and this desk's label. The only upstream call. */
export function callOdooApi<T>(sessionId: string, path: string) {
  return callOdooApiWithLabel<T>(
    sessionId,
    path,
    "GET",
    undefined,
    PHARMACY_SERVICE_LABEL,
  );
}

/** Every Pharmacy Desk route hangs off this one Odoo prefix. */
export const PHARMACY_API = "/yoya-emr/api/v1/pharmacy";

/** Parse the `[dispenseId]` segment. Existence and access are Odoo's decisions. */
export function parseDispenseId(raw: string) {
  const dispenseId = Number(raw);
  if (!Number.isInteger(dispenseId) || dispenseId <= 0) {
    return {
      ok: false as const,
      response: errorResponse(
        "invalid_dispense_id",
        "Pharmacy dispense ID is invalid.",
        400,
      ),
    };
  }
  return { ok: true as const, value: dispenseId };
}

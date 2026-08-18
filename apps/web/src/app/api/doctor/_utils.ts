/**
 * Shared bits for the Doctor Desk BFF routes.
 *
 * `server-only` keeps these -- and the Odoo session cookie they read -- out of
 * every client bundle. The browser never holds an Odoo session and never has a
 * reachable Odoo URL; it talks to /api/doctor/* and nothing else.
 *
 * The generic helpers are imported from the reception BFF rather than copied,
 * which is the pattern front-desk/_utils.ts, and the cashier and
 * insurance-credit routes, all follow. `forwardAdapted` is gone with the
 * adapter it existed for: the Odoo doctor controller emits the final contract,
 * so these routes forward the envelope untouched.
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

/** Every Doctor Desk route hangs off this one Odoo prefix. */
export const DOCTOR_API = "/yoya-emr/api/v1/doctor";

/**
 * Parse the `[appointmentId]` segment.
 *
 * The BFF validates only what it must to build a safe upstream URL. Whether
 * the visit exists, whether this doctor may see it, and whether the workflow
 * permits anything are all Odoo's decisions and are never second-guessed here
 * -- duplicating them would create a second source of truth that drifts.
 */
export function parseAppointmentId(raw: string) {
  const appointmentId = Number(raw);
  if (!Number.isInteger(appointmentId) || appointmentId <= 0) {
    return {
      ok: false as const,
      response: errorResponse("invalid_visit_id", "Visit ID is invalid.", 400),
    };
  }
  return { ok: true as const, value: appointmentId };
}

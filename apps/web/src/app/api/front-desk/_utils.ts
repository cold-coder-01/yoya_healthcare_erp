// Shared bits for the front-desk BFF routes. `server-only` keeps them (and the
// Odoo session they read) out of any client bundle; reception/_utils.ts carries
// the same guard and owns the actual Odoo call.
import "server-only";

import { errorResponse } from "@/app/api/reception/_utils";

/**
 * Parse the `[appointmentId]` segment.
 *
 * The BFF validates only what it must in order to build a safe URL. Everything
 * else -- whether the visit exists, whether this user may see it, whether the
 * workflow permits the action -- is Odoo's decision and is never second-guessed
 * here. Duplicating those checks would create a second source of truth that
 * drifts.
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

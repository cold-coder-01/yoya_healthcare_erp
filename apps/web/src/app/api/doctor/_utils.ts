/**
 * Shared bits for the Doctor Desk BFF routes.
 *
 * `server-only` keeps these -- and the Odoo session cookie they read -- out of
 * every client bundle. The browser never holds an Odoo session and never has a
 * reachable Odoo URL; it talks to /api/doctor/* and nothing else.
 *
 * The generic helpers are imported from the clinical BFF rather than copied,
 * following the same pattern front-desk/_utils.ts uses for reception's.
 */
import "server-only";

import { NextResponse } from "next/server";

import {
  errorResponse,
  handleRouteError,
  requireOdooSession,
} from "@/app/api/clinical/_utils";
import type { OdooApiResult } from "@/lib/odoo-client";

export { errorResponse, handleRouteError, requireOdooSession };

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

/**
 * Forward an Odoo result, running `adapt` over the payload on success only.
 *
 * An error envelope is passed through byte-for-byte with its original status,
 * so the operator sees Odoo's own wording -- including the clearance and
 * authorization messages raised by action_start_consultation, which are the
 * most important sentences this whole surface can display. Rewriting them into
 * generic UI copy would hide which of the four gates actually refused.
 */
export function forwardAdapted<TSource, TResult>(
  result: OdooApiResult<TSource>,
  adapt: (payload: TSource) => TResult,
) {
  if (!result.body.success) {
    return NextResponse.json(result.body, { status: result.status });
  }

  return NextResponse.json(
    { success: true as const, data: adapt(result.body.data) },
    { status: result.status },
  );
}

/**
 * Shared bits for the Admissions Desk BFF routes (Admissions Slice 1).
 *
 * READ ONLY. Every route here is a GET; there is no body helper and no POST
 * binding, because Slice 1 registers no mutation upstream.
 *
 * `server-only` keeps these -- and the Odoo session cookie they read -- out of
 * every client bundle. The browser never holds an Odoo session and never has a
 * reachable Odoo URL; it talks to /api/admissions/* and nothing else.
 *
 * The generic helpers are imported from the reception BFF rather than copied,
 * the pattern pharmacy/_utils.ts, radiology/_utils.ts and laboratory/_utils.ts
 * follow: session forwarding, the upstream timeout, and the fixed error
 * mapping that never passes an Odoo traceback through.
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
const ADMISSIONS_SERVICE_LABEL = "admissions service";

/** callOdooApi bound to GET and this desk's label. The only upstream call. */
export function callOdooApi<T>(sessionId: string, path: string) {
  return callOdooApiWithLabel<T>(sessionId, path, "GET", undefined, ADMISSIONS_SERVICE_LABEL);
}

/** Every Admissions Desk route hangs off this one Odoo prefix. */
export const ADMISSIONS_API = "/yoya-emr/api/v1/admissions";

/** Parse the `[id]` segment. Existence and access are Odoo's decisions. */
export function parseAdmissionId(raw: string) {
  const admissionId = Number(raw);
  if (!Number.isInteger(admissionId) || admissionId <= 0) {
    return {
      ok: false as const,
      response: errorResponse("invalid_admission_id", "Admission ID is invalid.", 400),
    };
  }
  return { ok: true as const, value: admissionId };
}

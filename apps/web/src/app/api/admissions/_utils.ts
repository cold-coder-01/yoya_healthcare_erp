/**
 * Shared bits for the Admissions Desk BFF routes (Admissions Slice 1).
 *
 * READS ARE GET. The ONE write (Slice 2) is POST .../admit, whose body is
 * rebuilt from exactly three fields by pickAdmitBody (see _body.ts).
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

/** A POST with a rebuilt body, bound to this desk's label. Used ONLY by admit. */
export function postOdooApiWithBody<T>(
  sessionId: string,
  path: string,
  body: Record<string, unknown>,
) {
  return callOdooApiWithLabel<T>(sessionId, path, "POST", body, ADMISSIONS_SERVICE_LABEL);
}

const INVALID_PAYLOAD = () =>
  errorResponse("admission_invalid_payload", "The admission request is not valid.", 400);

/** The request body as a JSON object, or the desk's fixed 400. */
export async function readMutationBody(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return { ok: false as const, response: INVALID_PAYLOAD() };
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return { ok: false as const, response: INVALID_PAYLOAD() };
  }
  return { ok: true as const, body: body as Record<string, unknown> };
}

export { pickAdmitBody, pickAdmissionRequestBody } from "./_body";

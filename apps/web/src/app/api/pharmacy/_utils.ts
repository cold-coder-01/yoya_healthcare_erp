/**
 * Shared bits for the Pharmacy Desk BFF routes.
 *
 * `server-only` keeps these -- and the Odoo session cookie they read -- out of
 * every client bundle. The browser never holds an Odoo session and never has a
 * reachable Odoo URL; it talks to /api/pharmacy/* and nothing else.
 *
 * The generic helpers are imported from the reception BFF rather than copied,
 * the pattern radiology/_utils.ts and laboratory/_utils.ts follow.
 *
 * TWO MUTATION ROUTES (Slice 2): prepare and validate. Unlike the Radiology
 * report save, they are NOT pass-throughs: the body is REBUILT here from the
 * allowed fields only (pickPrepareBody / pickValidateBody), so no stray key a
 * browser adds -- a state, a price, a batch -- is ever forwarded. The values
 * themselves are validated by Odoo, whose fixed error codes come back as-is.
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

/** A POST with a body, bound to the same label. Used ONLY by prepare and validate. */
export function postOdooApiWithBody<T>(
  sessionId: string,
  path: string,
  body: Record<string, unknown>,
) {
  return callOdooApiWithLabel<T>(sessionId, path, "POST", body, PHARMACY_SERVICE_LABEL);
}

const INVALID_PAYLOAD = () =>
  errorResponse("pharmacy_invalid_payload", "The dispense request is not valid.", 400);

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

// The two body filters are pure and import-free, so the contract tests can run
// them directly; see _body.ts.
export { pickPrepareBody, pickValidateBody } from "./_body";

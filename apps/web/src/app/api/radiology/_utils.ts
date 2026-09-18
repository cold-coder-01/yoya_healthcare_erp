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
 * TWO BODILESS WRITE ROUTES (Slice 2): schedule and start -- the request is
 * identified by the URL and nothing else is accepted.
 *
 * THREE REPORT ROUTES (Slice 3). Opening the report is bodiless too. Save and
 * enter carry the report text, so `readJsonObject` arrives here with them, as
 * it did for the Laboratory Desk's result entry. Those two routes are
 * PASS-THROUGHS: which keys are writable is the Radiology API's allow-list,
 * which refuses every other key with a 400.
 *
 * IMAGES (Slice 4). One multipart upload, one byte stream and one bodiless
 * remove. The upload form is REBUILT here from the one file and an optional
 * caption; the bytes are streamed back through streamOdooBinary's header
 * allow-list, so no Odoo cookie, URL or banner reaches the browser.
 */
import "server-only";

import {
  callOdooApi as callOdooApiWithLabel,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  postOdooMultipart as postOdooMultipartWithLabel,
  readJsonObject,
  requireOdooSession,
  streamOdooBinary,
} from "@/app/api/reception/_utils";

export {
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  readJsonObject,
  requireOdooSession,
  streamOdooBinary,
};

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

/**
 * A POST WITH A BODY, bound to the same label. Used ONLY by the report save and
 * enter routes, which forward the browser's JSON object unchanged; the
 * Radiology API decides which of its keys are honoured.
 */
export function postOdooApiWithBody<T>(
  sessionId: string,
  path: string,
  body: Record<string, unknown>,
) {
  return callOdooApiWithLabel<T>(
    sessionId,
    path,
    "POST",
    body,
    RADIOLOGY_SERVICE_LABEL,
  );
}

/** A multipart POST, bound to the same label. Used ONLY by the image upload. */
export function postOdooMultipart<T>(sessionId: string, path: string, form: FormData) {
  return postOdooMultipartWithLabel<T>(sessionId, path, form, RADIOLOGY_SERVICE_LABEL);
}

/** The model's ceiling, restated only to refuse a huge body before forwarding it. */
export const RADIOLOGY_IMAGE_MAX_BYTES = 25 * 1024 * 1024;

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

/** Parse the `[resultId]` segment. Existence and access are Odoo's decisions. */
export function parseResultId(raw: string) {
  const resultId = Number(raw);
  if (!Number.isInteger(resultId) || resultId <= 0) {
    return {
      ok: false as const,
      response: errorResponse(
        "invalid_result_id",
        "Radiology report ID is invalid.",
        400,
      ),
    };
  }
  return { ok: true as const, value: resultId };
}

/** Parse the `[imageId]` segment. Existence and ownership are Odoo's decisions. */
export function parseImageId(raw: string) {
  const imageId = Number(raw);
  if (!Number.isInteger(imageId) || imageId <= 0) {
    return {
      ok: false as const,
      response: errorResponse("radiology_image_not_found", "Image not found.", 404),
    };
  }
  return { ok: true as const, value: imageId };
}

/**
 * GET /api/radiology/results/[resultId]/images/[imageId]
 *
 * The bytes of one file of one radiology report, for the Radiology Desk.
 *
 * THE ONLY URL THE DESK EVER USES FOR IMAGERY. It reads the HTTP-only session
 * server-side, asks Odoo's desk byte route -- which checks the desk gate, that
 * the image belongs to THIS report, and that it is active -- and streams the
 * answer back through the allow-listed headers. No /web/content link, no
 * token, no Odoo origin in the page.
 *
 * NOT THE DOCTOR'S ROUTE. The Doctor's serves released images only; the desk
 * works on draft and entered reports and has its own gate upstream.
 *
 * NEVER CACHED, NEVER SNIFFED: `private, no-store` and `nosniff` are set here
 * whatever arrives, so a proxy or a shared workstation's history keeps nothing.
 */
import {
  RADIOLOGY_API,
  forwardOdooResult,
  handleRouteError,
  parseImageId,
  parseResultId,
  requireOdooSession,
  streamOdooBinary,
} from "../../../../_utils";

type Context = {
  params: Promise<{ resultId: string; imageId: string }>;
};

export async function GET(request: Request, context: Context) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { resultId, imageId } = await context.params;
  const result = parseResultId(resultId);
  if (!result.ok) {
    return result.response;
  }
  const image = parseImageId(imageId);
  if (!image.ok) {
    return image.response;
  }

  // ONE ENUM, TWO LITERALS: mapped, never forwarded.
  const requested = new URL(request.url).searchParams.get("disposition");
  const disposition = requested === "attachment" ? "?disposition=attachment" : "";

  try {
    const streamed = await streamOdooBinary(
      session.sessionId,
      `${RADIOLOGY_API}/results/${result.value}/images/${image.value}${disposition}`,
    );
    if (!streamed.ok) {
      return await forwardOdooResult({
        status: streamed.status,
        body: streamed.body as never,
      });
    }
    const response = streamed.response;
    response.headers.set("Cache-Control", "private, no-store");
    response.headers.set("X-Content-Type-Options", "nosniff");
    return response;
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_image_load_failed",
      "Unable to load this file.",
    );
  }
}

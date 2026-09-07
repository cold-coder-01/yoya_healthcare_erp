/**
 * /api/doctor/visits/[appointmentId]/results/images/[imageId]
 *
 *   GET  the bytes of one released radiology image
 *
 * THE ONLY URL THE BROWSER EVER SEES FOR CLINICAL IMAGERY. An `<img src>`
 * pointing at Odoo would put the backend origin in the page, and a
 * /web/content link would need either a public attachment or an access token
 * to work at all -- both of which turn a scoped clinical file into something
 * anyone holding the link can open. Neither exists: this route reads the
 * HTTP-only session server-side, calls Odoo itself, and streams the answer
 * back.
 *
 * THE APPOINTMENT IS IN THE PATH ON PURPOSE. It travels upstream, where Odoo
 * verifies that this image belongs to a RELEASED result of a request placed in
 * that visit's consultation. A doctor editing the id in the URL gets the same
 * 404 as one asking for a record that never existed, because the pairing is
 * checked rather than the id alone.
 *
 * NOTHING CLINICAL IS DECIDED HERE. This route does not know what "released"
 * means, does not inspect a mimetype and does not choose a disposition -- it
 * forwards two path segments and one enum, and relays four headers back.
 */
import {
  DOCTOR_API,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
  streamOdooBinary,
} from "../../../../../_utils";

type Context = {
  params: Promise<{ appointmentId: string; imageId: string }>;
};

export async function GET(request: Request, context: Context) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId, imageId } = await context.params;
  const visit = parseAppointmentId(appointmentId);
  if (!visit.ok) {
    return visit.response;
  }
  // The image id is validated exactly as strictly as the visit id, and for the
  // same reason: this builds an upstream URL, so a non-integer must never
  // reach it.
  const image = parseAppointmentId(imageId);
  if (!image.ok) {
    return image.response;
  }

  /*
    ONE ENUM, TWO LITERALS. `disposition` is the only thing a caller may
    influence, and it is mapped rather than forwarded -- an arbitrary value
    reaching a Content-Disposition header is a header-injection surface, and
    Odoo refuses anything but these two anyway.
  */
  const requested = new URL(request.url).searchParams.get("disposition");
  const disposition = requested === "attachment" ? "?disposition=attachment" : "";

  try {
    const result = await streamOdooBinary(
      session.sessionId,
      `${DOCTOR_API}/visits/${visit.value}/results/images/${image.value}${disposition}`,
    );
    if (!result.ok) {
      // A refusal keeps the envelope every other Doctor route uses, rather
      // than streaming an error page to an <img> tag.
      return await forwardOdooResult({
        status: result.status,
        body: result.body as never,
      });
    }
    return result.response;
  } catch (error) {
    return handleRouteError(
      error,
      "result_image_load_failed",
      "Unable to load this image.",
    );
  }
}

/**
 * /api/doctor/visits/[appointmentId]/history/[historicalAppointmentId]/images/[imageId]
 *
 *   GET  the bytes of one released image from a PRIOR episode
 *
 * WHY THIS IS NOT THE RESULTS IMAGE ROUTE. /results/images/[imageId] is a
 * CURRENT-VISIT endpoint: Slice 9A deliberately refuses to let it resolve a
 * historical appointment, because a current-visit URL that could serve another
 * episode's bytes is exactly the leak the visit-scoped design exists to
 * prevent. Historical imagery therefore has its own route, on the longitudinal
 * surface, where BOTH appointments are named and both are checked upstream.
 *
 * IT IS NOT A GENERIC IMAGE-BY-ID ROUTE EITHER. It can serve nothing without a
 * current visit the caller owns AND a historical episode of that visit's own
 * patient; Odoo re-checks that the image belongs to a RELEASED result of that
 * episode's consultation before a single byte moves.
 *
 * THE ID IS A hospital.radiology.image ID, never an ir.attachment id. An
 * attachment id is a database-wide file handle; accepting one here would turn
 * a clinical endpoint into a general file reader.
 *
 * NOTHING CLINICAL IS DECIDED HERE. This route does not know what "released"
 * means, does not inspect a mimetype and does not choose a disposition -- it
 * forwards three path segments and one enum, and relays the headers back.
 */
import {
  DOCTOR_API,
  forwardOdooResult,
  handleRouteError,
  parseAppointmentId,
  requireOdooSession,
  streamOdooBinary,
} from "../../../../../../_utils";

type Context = {
  params: Promise<{
    appointmentId: string;
    historicalAppointmentId: string;
    imageId: string;
  }>;
};

export async function GET(request: Request, context: Context) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { appointmentId, historicalAppointmentId, imageId } =
    await context.params;
  const visit = parseAppointmentId(appointmentId);
  if (!visit.ok) {
    return visit.response;
  }
  const historical = parseAppointmentId(historicalAppointmentId);
  if (!historical.ok) {
    return historical.response;
  }
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
      `${DOCTOR_API}/visits/${visit.value}` +
        `/history/${historical.value}/images/${image.value}${disposition}`,
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
      "history_image_load_failed",
      "Unable to load this image.",
    );
  }
}

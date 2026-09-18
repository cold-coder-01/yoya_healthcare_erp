/**
 * POST /api/radiology/results/[resultId]/images/[imageId]/remove
 *
 * Remove a wrong upload from a draft or entered report. THIS ROUTE DECIDES
 * NOTHING: Odoo locks the report, re-checks that its images may still change,
 * checks the image belongs to it, and unlinks it in one savepoint.
 *
 * BODILESS: the browser's body is never read or forwarded.
 */
import type { RadImageResponse } from "@/types/rad-desk";

import {
  RADIOLOGY_API,
  forwardOdooResult,
  handleRouteError,
  parseImageId,
  parseResultId,
  postOdooApi,
  requireOdooSession,
} from "../../../../../_utils";

export async function POST(
  _request: Request,
  context: { params: Promise<{ resultId: string; imageId: string }> },
) {
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

  try {
    return await forwardOdooResult(
      await postOdooApi<RadImageResponse>(
        session.sessionId,
        `${RADIOLOGY_API}/results/${result.value}/images/${image.value}/remove`,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_image_remove_failed",
      "Unable to reach the radiology service. The file was not removed.",
    );
  }
}

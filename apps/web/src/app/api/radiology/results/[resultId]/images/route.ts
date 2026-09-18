/**
 * POST /api/radiology/results/[resultId]/images
 *
 * Upload ONE file to a draft or entered radiology report. multipart/form-data.
 *
 * THE FORM IS REBUILT, NOT RELAYED. Exactly one `file` and an optional
 * `caption` are read here and a fresh form is sent to Odoo server-side, with
 * the session cookie on that request only. Any other field is refused. The
 * browser's Content-Type for the file is dropped: the Radiology API sniffs the
 * bytes and decides the type, the size and the image kind itself.
 *
 * A body over the model's 25 MB is refused here before it is forwarded; every
 * other rule -- the type, the report's state, who may upload -- is Odoo's.
 */
import type { RadImageResponse } from "@/types/rad-desk";

import {
  RADIOLOGY_API,
  RADIOLOGY_IMAGE_MAX_BYTES,
  errorResponse,
  forwardOdooResult,
  handleRouteError,
  parseResultId,
  postOdooMultipart,
  requireOdooSession,
} from "../../../_utils";

const ALLOWED_FIELDS = new Set(["file", "caption"]);

export async function POST(
  request: Request,
  context: { params: Promise<{ resultId: string }> },
) {
  const session = await requireOdooSession();
  if (!session.ok) {
    return session.response;
  }

  const { resultId } = await context.params;
  const parsed = parseResultId(resultId);
  if (!parsed.ok) {
    return parsed.response;
  }

  let incoming: FormData;
  try {
    incoming = await request.formData();
  } catch {
    return errorResponse(
      "radiology_image_invalid_file",
      "Send the file as multipart/form-data.",
      400,
    );
  }

  const unknown = [...new Set(incoming.keys())].filter((key) => !ALLOWED_FIELDS.has(key));
  if (unknown.length > 0) {
    return errorResponse(
      "radiology_image_field_not_allowed",
      `These fields cannot be sent with a radiology file: ${unknown.sort().join(", ")}.`,
      400,
    );
  }
  const files = incoming.getAll("file");
  const file = files[0];
  if (files.length !== 1 || !(file instanceof File)) {
    return errorResponse(
      "radiology_image_invalid_file",
      "Send exactly one file, in the 'file' field.",
      400,
    );
  }
  if (file.size > RADIOLOGY_IMAGE_MAX_BYTES) {
    return errorResponse(
      "radiology_image_too_large",
      "This file is larger than 25 MB, the limit for one radiology file. Nothing has been uploaded.",
      413,
    );
  }
  const caption = incoming.get("caption");

  const form = new FormData();
  form.append(
    "file",
    new Blob([await file.arrayBuffer()], { type: "application/octet-stream" }),
    file.name,
  );
  if (typeof caption === "string") {
    form.append("caption", caption);
  }

  try {
    return await forwardOdooResult(
      await postOdooMultipart<RadImageResponse>(
        session.sessionId,
        `${RADIOLOGY_API}/results/${parsed.value}/images`,
        form,
      ),
    );
  } catch (error) {
    return handleRouteError(
      error,
      "radiology_image_upload_failed",
      "Unable to reach the radiology service. The file was not uploaded.",
    );
  }
}

/**
 * The admission mutation body filters (Admissions Slice 2). PURE and
 * import-free, so node:test can load them without a resolver.
 *
 * Bodies are REBUILT from the allowed fields, never passed through, so no stray
 * key a browser adds -- a ward, a state, a patient -- is ever forwarded. The
 * VALUES are passed through untouched: Odoo, not this route, decides whether
 * they are valid, and answers with its fixed error codes.
 */

/** EXACTLY the Admit fields. The ward and room are derived from the bed by Odoo. */
export function pickAdmitBody(body: Record<string, unknown>): Record<string, unknown> {
  return {
    operation_token: body.operation_token,
    expected_revision: body.expected_revision,
    bed_id: body.bed_id,
  };
}

/** EXACTLY the Doctor request fields. The doctor names no ward and no bed. */
export function pickAdmissionRequestBody(body: Record<string, unknown>): Record<string, unknown> {
  return {
    operation_token: body.operation_token,
    reason: body.reason,
  };
}

/** EXACTLY the Transfer fields (Slice 3). The destination ward and room are
 *  derived from the bed by Odoo; no rate or amount is ever sent. */
export function pickTransferBody(body: Record<string, unknown>): Record<string, unknown> {
  return {
    operation_token: body.operation_token,
    expected_revision: body.expected_revision,
    bed_id: body.bed_id,
    reason: body.reason,
  };
}

/** EXACTLY the Cancel request fields (Slice 3). */
export function pickCancelRequestBody(body: Record<string, unknown>): Record<string, unknown> {
  return {
    operation_token: body.operation_token,
    expected_revision: body.expected_revision,
  };
}

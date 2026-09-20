/**
 * The Pharmacy Desk mutation body filters. PURE and import-free, so node:test
 * can load them without a resolver; _utils.ts re-exports them for the routes.
 */
/**
 * EXACTLY the Prepare fields. Line entries are rebuilt from line_id and
 * intended_quantity only; values are passed through untouched so Odoo -- not
 * this route -- decides whether they are valid.
 */
export function pickPrepareBody(body: Record<string, unknown>): Record<string, unknown> {
  const lines = Array.isArray(body.lines)
    ? body.lines.map((entry) => {
        const record = typeof entry === "object" && entry !== null ? (entry as Record<string, unknown>) : {};
        return { line_id: record.line_id, intended_quantity: record.intended_quantity };
      })
    : body.lines;
  return {
    operation_token: body.operation_token,
    expected_revision: body.expected_revision,
    lines,
  };
}

/** EXACTLY the Validate fields. No quantity is ever forwarded. */
export function pickValidateBody(body: Record<string, unknown>): Record<string, unknown> {
  return {
    operation_token: body.operation_token,
    expected_revision: body.expected_revision,
  };
}

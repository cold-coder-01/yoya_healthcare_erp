/**
 * Billing-clearance banner.
 *
 * Only ever rendered for a genuine billing signal: either Odoo reported the
 * appointment as blocked, or it returned a clearance message. An empty or
 * malformed error object produces no banner at all.
 */
export default function BillingWarning({
  blocked,
  message,
  detail,
}: {
  blocked?: boolean | null;
  message?: string | null;
  /** Odoo's billing_clearance_message, when it adds information. */
  detail?: string | null;
}) {
  const mainMessage = typeof message === "string" ? message.trim() : "";
  const detailMessage = typeof detail === "string" ? detail.trim() : "";

  if (!blocked && !mainMessage && !detailMessage) {
    return null;
  }

  // The Odoo UserError usually already quotes the clearance reason; only show
  // it separately when it genuinely adds something.
  const showDetail =
    detailMessage.length > 0 && !mainMessage.includes(detailMessage);

  return (
    <div
      role="alert"
      className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900"
    >
      <div className="font-semibold">Billing clearance required</div>
      <div className="mt-1">
        {mainMessage ||
          detailMessage ||
          "This patient is blocked by billing clearance."}
      </div>
      {showDetail && mainMessage ? (
        <div className="mt-1 font-medium">{detailMessage}</div>
      ) : null}
    </div>
  );
}

/**
 * Generic API/request failure banner.
 *
 * Kept separate from BillingWarning so a transport or validation failure is
 * never presented to a clinician as a billing-clearance problem.
 */
export default function ErrorBanner({
  message,
  title = "Something went wrong",
}: {
  message?: string | null;
  title?: string;
}) {
  const text = typeof message === "string" ? message.trim() : "";

  // Renders only when a request has actually failed.
  if (!text) {
    return null;
  }

  return (
    <div
      role="alert"
      className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900"
    >
      <div className="font-semibold">{title}</div>
      <div className="mt-1">{text}</div>
    </div>
  );
}

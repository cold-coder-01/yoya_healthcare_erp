export default function BillingWarning({
  blocked,
  message,
}: {
  blocked?: boolean | null;
  message?: string | null;
}) {
  if (!blocked && !message) {
    return null;
  }

  return (
    <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
      <div className="font-semibold">Billing clearance required</div>
      <div className="mt-1">{message ?? "This patient is blocked by billing clearance."}</div>
    </div>
  );
}

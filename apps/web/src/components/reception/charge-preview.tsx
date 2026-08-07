"use client";

/**
 * Fee quote before anything is created.
 *
 * Card fee and consultation fee are shown as separate lines so the cashier can
 * explain the total at the counter, and an existing-card patient is told
 * explicitly why there is no card charge.
 */
import { formatEtb } from "@/lib/reception-format";
import type { ReceptionVisitPreview } from "@/types/reception";

export default function ChargePreview({
  preview,
  loading,
  error,
}: {
  preview: ReceptionVisitPreview | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
        Calculating fees…
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        {error}
      </div>
    );
  }

  if (!preview) {
    return (
      <div className="rounded-lg border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
        Select a department to see the fee breakdown.
      </div>
    );
  }

  const cardRequired = preview.card.required;

  return (
    <div className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="border-b border-slate-100 bg-slate-50 px-4 py-2.5">
        <h3 className="text-sm font-semibold text-slate-900">Fee preview</h3>
      </div>

      <dl className="divide-y divide-slate-100">
        <div className="flex items-baseline justify-between gap-4 px-4 py-3">
          <dt className="text-sm text-slate-700">
            New patient card
            {preview.card.service ? (
              <span className="mt-0.5 block text-xs text-slate-500">
                {preview.card.service.name}
              </span>
            ) : null}
          </dt>
          <dd className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">
            {cardRequired ? formatEtb(preview.card.price) : "—"}
          </dd>
        </div>

        <div className="flex items-baseline justify-between gap-4 px-4 py-3">
          <dt className="text-sm text-slate-700">
            Consultation
            <span className="mt-0.5 block text-xs text-slate-500">
              {preview.consultation.service.name}
            </span>
          </dt>
          <dd className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">
            {formatEtb(preview.consultation.price)}
          </dd>
        </div>

        <div className="flex items-baseline justify-between gap-4 bg-slate-50 px-4 py-3">
          <dt className="text-sm font-semibold text-slate-900">
            Total due before triage
          </dt>
          <dd className="shrink-0 text-base font-bold tabular-nums text-slate-950">
            {formatEtb(preview.total, preview.currency)}
          </dd>
        </div>
      </dl>

      {!cardRequired ? (
        <p className="border-t border-emerald-100 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-900">
          Existing patient card found — no new card fee.
        </p>
      ) : (
        <p className="border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
          {preview.card.reason}
        </p>
      )}
    </div>
  );
}

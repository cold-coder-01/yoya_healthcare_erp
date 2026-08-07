/**
 * THE authoritative reception clearance.
 *
 * Encounter-wide: consultation AND patient card both participate. This is what
 * gates send-to-triage. Never confuse it with the consultation-only card.
 */
import { ClearanceBadge } from "@/components/reception/reception-badges";
import { formatEtb } from "@/lib/reception-format";
import type { ReceptionClearance } from "@/types/reception";

export default function ReceptionClearanceCard({
  clearance,
}: {
  clearance: ReceptionClearance;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-slate-50 px-4 py-2.5">
        <h2 className="text-sm font-semibold text-slate-900">
          Reception clearance
        </h2>
        <ClearanceBadge state={clearance.state} ok={clearance.ok} />
      </div>

      <dl className="grid grid-cols-1 divide-y divide-slate-100 sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        <div className="px-4 py-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Required
          </dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums text-slate-900">
            {formatEtb(clearance.required)}
          </dd>
        </div>
        <div className="px-4 py-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Received
          </dt>
          <dd className="mt-1 text-lg font-semibold tabular-nums text-slate-900">
            {formatEtb(clearance.received)}
          </dd>
        </div>
        <div className="px-4 py-3">
          <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Outstanding
          </dt>
          <dd
            className={`mt-1 text-lg font-bold tabular-nums ${
              clearance.outstanding > 0 ? "text-amber-700" : "text-emerald-700"
            }`}
          >
            {formatEtb(clearance.outstanding)}
          </dd>
        </div>
      </dl>

      {clearance.message ? (
        <p className="border-t border-slate-100 px-4 py-3 text-sm text-slate-600">
          {clearance.message}
        </p>
      ) : null}
    </section>
  );
}

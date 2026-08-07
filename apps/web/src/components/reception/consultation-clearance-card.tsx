/**
 * Consultation-only clearance, shown SEPARATELY and never used for a reception
 * decision.
 *
 * hospital_billing deliberately scopes appointment.billing_blocked to the
 * consultation charge alone -- correct for gating a consultation, wrong for
 * releasing a patient to triage. Presenting it in its own muted card, with the
 * API's own scope note, is what stops the two being conflated.
 */
import { formatEtb } from "@/lib/reception-format";
import type { ConsultationClearance } from "@/types/reception";

export default function ConsultationClearanceCard({
  clearance,
}: {
  clearance: ConsultationClearance;
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-4 py-2.5">
        <h2 className="text-sm font-semibold text-slate-700">
          Consultation clearance
        </h2>
        <span
          className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${
            clearance.blocked
              ? "bg-slate-200 text-slate-800 ring-slate-300"
              : "bg-white text-slate-600 ring-slate-200"
          }`}
        >
          {clearance.blocked ? "Blocked" : "Not blocking"}
        </span>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 px-4 py-3">
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Required
          </dt>
          <dd className="mt-1 text-sm font-semibold tabular-nums text-slate-800">
            {formatEtb(clearance.required)}
          </dd>
        </div>
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Outstanding
          </dt>
          <dd className="mt-1 text-sm font-semibold tabular-nums text-slate-800">
            {formatEtb(clearance.outstanding)}
          </dd>
        </div>
      </dl>

      {clearance.message ? (
        <p className="px-4 pb-2 text-sm text-slate-600">{clearance.message}</p>
      ) : null}

      <p className="border-t border-slate-200 px-4 py-2 text-xs italic text-slate-500">
        {clearance.scope_note}
      </p>
    </section>
  );
}

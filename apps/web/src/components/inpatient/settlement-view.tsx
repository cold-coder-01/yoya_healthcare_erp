"use client";

import type { InpatientSettlement } from "@/types/inpatient-settlement";
import { money } from "@/lib/cashier-format";
import {
  SETTLEMENT_STAGE_PLAN,
  settlementHeadline,
  stageProgress,
  stageStatusLabel,
} from "@/lib/settlement-format";

type Props = {
  settlement: InpatientSettlement | null;
  loading: boolean;
  error: string | null;
  currency: string | null;
};

function Line({
  label,
  value,
  strong = false,
  muted = false,
}: {
  label: string;
  value: string;
  strong?: boolean;
  muted?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <span className={`text-xs ${muted ? "pl-3 text-slate-500" : "text-slate-700"}`}>{label}</span>
      <span
        className={`font-mono tabular-nums ${
          strong ? "text-sm font-bold text-slate-950" : muted ? "text-xs text-slate-500" : "text-xs text-slate-900"
        }`}
      >
        {value}
      </span>
    </div>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-slate-200 pt-1.5">
      <h4 className="mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-500">{title}</h4>
      {children}
    </div>
  );
}

/**
 * The FINAL INPATIENT SETTLEMENT: calculation stages, the result, and the
 * breakdown behind it.
 *
 * THE PROGRESS IS THE SERVER'S. While the one settlement request is in flight
 * the bar is indeterminate and every stage reads "Calculating…"; when the
 * server answers, each stage shows the status the server computed for it and
 * the bar shows the share it reported complete. There is no timer: a stage
 * never reads Complete because time passed.
 */
export default function SettlementView({ settlement, loading, error, currency }: Props) {
  const progress = stageProgress(loading ? null : settlement?.stages);
  const stages = !loading && settlement ? settlement.stages : null;

  return (
    <div className="flex flex-col gap-2" aria-busy={loading}>
      <div className="rounded-md border border-slate-200 bg-white p-2.5">
        <div className="flex items-baseline justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">
            {loading ? "Calculating final inpatient settlement…" : "Final inpatient settlement"}
          </h3>
          <span className="font-mono text-[10px] text-slate-500">
            {progress.percent === null ? "…" : `${progress.percent}%`}
          </span>
        </div>
        <div
          className="mt-1.5 h-1.5 w-full overflow-hidden rounded bg-slate-200"
          role="progressbar"
          aria-label="Settlement calculation"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress.percent ?? undefined}
        >
          <div
            className={`h-full ${
              progress.percent === null
                ? "w-1/3 animate-pulse bg-slate-400"
                : progress.complete === progress.total
                  ? "bg-emerald-600"
                  : "bg-amber-500"
            }`}
            style={progress.percent === null ? undefined : { width: `${progress.percent}%` }}
          />
        </div>
        <ul className="mt-2 grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          {(stages ?? SETTLEMENT_STAGE_PLAN).map((stage: { key: string; label: string; status?: "complete" | "review" }) => {
            const status = stage.status ?? "pending";
            return (
              <li key={stage.key} className="flex items-baseline justify-between gap-2 py-0.5 text-xs">
                <span className="text-slate-700">{stage.label}</span>
                <span
                  className={`font-medium ${
                    status === "complete"
                      ? "text-emerald-700"
                      : status === "review"
                        ? "text-red-700"
                        : "text-slate-400"
                  }`}
                >
                  {stageStatusLabel(status)}
                </span>
              </li>
            );
          })}
        </ul>
      </div>

      {error ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-2.5 text-xs text-red-800">{error}</div>
      ) : null}

      {settlement && !loading ? <Result settlement={settlement} currency={currency} /> : null}
    </div>
  );
}

function Result({ settlement, currency }: { settlement: InpatientSettlement; currency: string | null }) {
  const headline = settlementHeadline(settlement);
  const delivered = settlement.delivered_by_category;
  return (
    <>
      <div className={`rounded-md border p-2.5 ${headline.tone}`}>
        <p className="text-[11px] font-bold uppercase tracking-wide">{headline.label}</p>
        {settlement.state === "credit" ? (
          <p className="text-xs">Patient funds are being held toward ongoing inpatient care.</p>
        ) : null}
        {headline.amount !== null ? (
          <p className="font-mono text-xl font-bold tabular-nums">
            {currency ? `${currency} ` : ""}
            {money(headline.amount)}
          </p>
        ) : null}
        {settlement.review_reasons.length ? (
          <ul className="mt-1 list-disc pl-4 text-xs">
            {settlement.review_reasons.map((reason) => (
              <li key={reason.code}>{reason.message}</li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="flex flex-col gap-1.5 rounded-md border border-slate-200 bg-white p-2.5">
        <Line label="Estimated amount" value={money(settlement.estimate_amount)} />
        <Line label="Advance received" value={money(settlement.advance_received)} />

        <Group title="Actual delivered">
          {delivered.map((entry) => (
            <Line key={entry.key} label={entry.label} value={money(entry.amount)} muted />
          ))}
          <Line label="Total delivered care" value={money(settlement.actual_delivered)} strong />
          {settlement.pending_delivery ? (
            <p className="text-[11px] text-slate-500">
              Ordered services not yet delivered are not included.
            </p>
          ) : null}
          {settlement.stay_unposted > 0 ? (
            <p className="text-[11px] text-slate-500">
              Includes {money(settlement.stay_unposted)} of bed-days not yet posted.
            </p>
          ) : null}
        </Group>

        <Group title="Responsibility">
          <Line label="Payer share" value={money(settlement.payer_authorized)} />
          <Line label="Patient responsibility" value={money(settlement.patient_responsibility)} strong />
        </Group>

        <Group title="Patient funds">
          <Line label="Advance" value={money(settlement.funds.advance)} muted />
          <Line label="Other payments" value={money(settlement.funds.other_payments)} muted />
          <Line label="Settlement payments" value={money(settlement.funds.settlement_payments)} muted />
          <Line label="Total patient funds" value={money(settlement.funds.total)} strong />
          <Line label="Advance applied to care" value={money(settlement.advance_applied)} muted />
        </Group>

        <Group title="Final">
          <Line label="Remaining due" value={money(settlement.remaining_due)} strong />
          <Line label="Refundable balance" value={money(settlement.refundable_balance)} strong />
        </Group>
      </div>
    </>
  );
}

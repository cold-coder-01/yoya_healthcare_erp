"use client";

import type { CashierInpatientDetail } from "@/types/cashier";
import {
  inpatientLaneLabel,
  inpatientLaneTone,
  inpatientLocation,
  inpatientStateLabel,
  money,
  shortTime,
} from "@/lib/cashier-format";

type Props = { account: CashierInpatientDetail };

function Row({
  label,
  value,
  tone = "normal",
}: {
  label: string;
  value: string;
  tone?: "normal" | "muted" | "total" | "sponsor" | "refund";
}) {
  const valueClass =
    tone === "total"
      ? "text-base font-bold text-emerald-800"
      : tone === "refund"
        ? "text-base font-bold text-sky-800"
        : tone === "sponsor"
          ? "text-sm font-semibold text-sky-800"
          : tone === "muted"
            ? "text-sm text-slate-500"
            : "text-sm font-semibold text-slate-900";
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-dotted border-slate-200 py-1">
      <span className="text-xs text-slate-600">{label}</span>
      <span className={`font-mono tabular-nums ${valueClass}`}>{value}</span>
    </div>
  );
}

/**
 * An inpatient account at the window.
 *
 * Every figure is the server's delivered-basis summary -- the one the
 * Admissions discharge gate applies. Nothing is added or compared here. What
 * is NOT shown is as deliberate as what is: no physician, no reason for
 * admission, no discharge summary. The cashier sees who, where, and how much.
 */
export default function CashierInpatientPanel({ account }: Props) {
  const { financial } = account;
  const refund = account.lane === "refund_due";

  return (
    <div className="flex min-h-0 flex-col gap-2">
      {/* Identity */}
      <div className="rounded-md border border-slate-200 bg-white p-2.5">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="truncate text-base font-bold text-slate-900">
            {account.patient.name}
          </h2>
          <span className="shrink-0 font-mono text-[11px] text-slate-500">
            {account.patient.identification_code ?? "-"}
          </span>
        </div>
        <p className="mt-0.5 font-mono text-[11px] text-slate-500">
          {account.encounter.name} · {account.admission.name} ·{" "}
          {inpatientLocation(account.location)}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        <span className="rounded border border-violet-300 bg-violet-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-800">
          Inpatient settlement
        </span>
        <span className="rounded border border-sky-300 bg-sky-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-800">
          {inpatientStateLabel(account.admission)}
        </span>
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${inpatientLaneTone(
            account.lane,
          )}`}
        >
          {inpatientLaneLabel(account.lane)}
        </span>
      </div>

      {/* The delivered-basis figures */}
      <div className="rounded-md border border-slate-200 bg-white p-2.5">
        <div className="mb-1 flex items-baseline justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Financial summary
          </h3>
          <span className="font-mono text-[10px] text-slate-400">
            {account.currency ?? ""}
          </span>
        </div>
        <Row label="Care delivered" value={money(financial.actual_delivered)} />
        {financial.payer_authorized > 0 ? (
          <Row
            label="Payer share"
            value={money(financial.payer_authorized)}
            tone="sponsor"
          />
        ) : null}
        <Row
          label="Patient responsibility"
          value={money(financial.patient_responsibility)}
        />
        <Row
          label="Patient payments held (advance + paid)"
          value={money(financial.patient_funds)}
          tone="muted"
        />
        {financial.settlement_paid > 0 ? (
          <Row
            label="of which settled here"
            value={money(financial.settlement_paid)}
            tone="muted"
          />
        ) : null}
        {refund ? (
          <Row
            label="Refundable to patient"
            value={money(financial.refundable_balance)}
            tone="refund"
          />
        ) : (
          <Row
            label="Remaining due"
            value={money(financial.remaining_due)}
            tone="total"
          />
        )}
        {financial.stay_unposted > 0 ? (
          <p className="mt-1 text-[11px] leading-4 text-slate-500">
            Includes {money(financial.stay_unposted)} of bed-days not yet posted;
            they are posted when the payment is taken.
          </p>
        ) : null}
        {financial.pending_delivery ? (
          <p className="mt-1 text-[11px] leading-4 text-slate-500">
            Some ordered services are not delivered yet. They are not included
            and nothing is collected for them here.
          </p>
        ) : null}
      </div>

      {financial.review_reasons.length ? (
        <div className="rounded-md border border-red-200 bg-red-50 p-2.5 text-xs leading-5 text-red-800">
          <p className="font-semibold">The figures need review:</p>
          <ul className="mt-1 list-disc pl-4">
            {financial.review_reasons.map((reason) => (
              <li key={reason.code}>{reason.message}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {/* Delivered care by category */}
      <div className="rounded-md border border-slate-200 bg-white p-2.5">
        <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          Delivered care
        </h3>
        {account.delivered_by_category
          .filter((entry) => entry.amount > 0)
          .map((entry) => (
            <Row key={entry.key} label={entry.label} value={money(entry.amount)} />
          ))}
        {!account.delivered_by_category.some((entry) => entry.amount > 0) ? (
          <p className="text-xs text-slate-400">Nothing delivered yet.</p>
        ) : null}
      </div>

      {account.settlement_receipts.length ? (
        <div className="rounded-md border border-slate-200 bg-white p-2.5">
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Settlement payments
          </h3>
          {account.settlement_receipts.map((receipt) => (
            <Row
              key={receipt.id}
              label={`${receipt.name} · ${receipt.payment_method} · ${shortTime(receipt.received_at)}`}
              value={money(receipt.amount)}
              tone="muted"
            />
          ))}
        </div>
      ) : null}
    </div>
  );
}

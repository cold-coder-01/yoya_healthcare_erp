"use client";

import type { CashierInpatientDetail } from "@/types/cashier";
import SettlementView from "@/components/inpatient/settlement-view";
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
  tone?: "normal" | "muted";
}) {
  const valueClass =
    tone === "muted" ? "text-sm text-slate-500" : "text-sm font-semibold text-slate-900";
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
          {inpatientLaneLabel(account.lane, account.advance)}
        </span>
      </div>

      {/* The final settlement: the server's one computation, its stages and
          its result. Nothing on this desk adds a figure. */}
      <SettlementView
        settlement={account.settlement}
        loading={false}
        error={null}
        currency={account.currency}
      />

      {/* The advance against the physician's estimate. */}
      <div className="rounded-md border border-slate-200 bg-white p-2.5">
        <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          Advance
        </h3>
        <Row label="Estimate (advance requested)" value={money(account.advance.requested)} />
        <Row label="Advance received" value={money(account.advance.received)} tone="muted" />
        {account.advance.open ? (
          <Row label="Remaining advance requirement" value={money(account.advance.outstanding)} />
        ) : null}
      </div>

      {account.advance_receipts.length ? (
        <div className="rounded-md border border-slate-200 bg-white p-2.5">
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Advance payments
          </h3>
          {account.advance_receipts.map((receipt) => (
            <Row
              key={receipt.id}
              label={`${receipt.name} · ${receipt.payment_method} · ${shortTime(receipt.received_at)}`}
              value={money(receipt.amount)}
              tone="muted"
            />
          ))}
        </div>
      ) : null}

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

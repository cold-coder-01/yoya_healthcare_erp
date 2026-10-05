"use client";

import { FormEvent, useState } from "react";

import type { CashierInpatientDetail } from "@/types/cashier";
import { money, settlementPreview } from "@/lib/cashier-format";

type Props = {
  account: CashierInpatientDetail;
  submitting: boolean;
  error: string | null;
  onRefund: (input: { amount: number; reason: string }) => void;
};

/**
 * REFUND DUE: shown to everyone at the window, recorded only by Accounting.
 *
 * The server says whether this user may record it (`refund.may_record`, which
 * mirrors hospital_billing's own refund_advance guard: Accountant / Manager /
 * Administrator). A cashier sees the amount and where it goes -- never a
 * button that would 403. A recorded refund lowers the patient's funds only;
 * delivered care, invoices and revenue do not move.
 */
export default function CashierInpatientRefund({ account, submitting, error, onRefund }: Props) {
  const { refund } = account;
  const [amount, setAmount] = useState(() => (refund.amount > 0 ? refund.amount.toFixed(2) : ""));
  const [reason, setReason] = useState("");
  const [confirming, setConfirming] = useState(false);

  if (!refund.refund_due) return null;

  if (!refund.may_record) {
    return (
      <div className="rounded-md border border-sky-200 bg-sky-50 p-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-sky-800">Refund due</h3>
        <p className="mt-1 font-mono text-lg font-bold tabular-nums text-sky-900">
          {account.currency ? `${account.currency} ` : ""}
          {money(refund.amount)}
        </p>
        <p className="mt-1 text-xs leading-5 text-sky-900">
          Routed to Accounting. Refunds are recorded by the Accountant, Hospital
          Manager or System Administrator; nothing is paid out at this desk.
        </p>
      </div>
    );
  }

  const parsed = Number(amount);
  const preview = settlementPreview(refund.amount, parsed);
  const valid = Number.isFinite(parsed) && parsed > 0 && !preview.exceeds && reason.trim().length > 0;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid || submitting) return;
    if (!confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    onRefund({ amount: parsed, reason: reason.trim() });
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-2 rounded-md border border-sky-200 bg-white p-3">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-sky-800">Record refund</h3>
      <p className="text-[11px] text-slate-600">
        Unapplied credit {money(refund.amount)}. A refund returns the patient&apos;s own
        money; it is not a reduction of revenue.
      </p>
      <label className="text-xs font-medium text-slate-700" htmlFor="refund-amount">
        Refund amount
      </label>
      <input
        id="refund-amount"
        type="number"
        inputMode="decimal"
        step="0.01"
        min="0.01"
        max={refund.amount}
        value={amount}
        onChange={(event) => {
          setAmount(event.target.value);
          setConfirming(false);
        }}
        className="h-10 w-full rounded-md border border-slate-300 px-3 text-right font-mono text-base font-bold tabular-nums outline-none focus:border-sky-600"
      />
      {preview.exceeds ? (
        <p className="text-[11px] font-medium text-red-700">More than the unapplied credit.</p>
      ) : null}
      <label className="text-xs font-medium text-slate-700" htmlFor="refund-reason">
        Reason <span className="text-red-600">*</span>
      </label>
      <input
        id="refund-reason"
        type="text"
        value={reason}
        onChange={(event) => {
          setReason(event.target.value);
          setConfirming(false);
        }}
        placeholder="e.g. Unused inpatient advance returned"
        className="h-9 w-full rounded-md border border-slate-300 px-2 text-sm outline-none focus:border-sky-600"
      />
      {error ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-2.5 py-2 text-xs text-red-700">{error}</div>
      ) : null}
      {confirming ? (
        <p className="rounded border border-sky-200 bg-sky-50 px-2 py-1 font-mono text-xs text-sky-900">
          Credit {money(preview.before)} − refund {money(preview.payment)} = {money(preview.after)}
        </p>
      ) : null}
      <button
        type="submit"
        disabled={!valid || submitting}
        className="h-10 w-full rounded-md bg-sky-700 text-sm font-semibold text-white hover:bg-sky-800 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        {submitting ? "Recording..." : confirming ? "Confirm refund" : "Record refund"}
      </button>
    </form>
  );
}

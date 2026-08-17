"use client";

import { FormEvent, useMemo, useState } from "react";

import {
  CASHIER_PAYMENT_METHODS,
  type CashierPaymentMethod,
  type CashierReceipt,
  type CashierVisitDetail,
} from "@/types/cashier";
import { money } from "@/lib/cashier-format";

type Props = {
  visit: CashierVisitDetail;
  receipt: CashierReceipt | null;
  submitting: boolean;
  error: string | null;
  onSubmit: (input: {
    amount: number;
    method: CashierPaymentMethod;
    reference: string | null;
    note: string | null;
  }) => void;
};

/**
 * Payment entry.
 *
 * The amount defaults to `patient_outstanding`, which is the server's own
 * mode-correct figure -- so this is right under enforce (patient residual) AND
 * under off/shadow (legacy gross) without the form ever branching on mode.
 *
 * The form does NOT cap the amount. Over-collection is legitimate in off and
 * shadow, where it lands as an advance, and it is refused by the backend under
 * enforce with a specific code. Blocking it client-side would break a
 * supported workflow to prevent an error the server already prevents properly.
 */
export default function CashierPaymentForm({
  visit,
  receipt,
  submitting,
  error,
  onSubmit,
}: Props) {
  const { financial, collectability } = visit;

  // RE-ARMED BY REMOUNT, not by an effect.
  //
  // The parent gives this component a key derived from the visit and its
  // outstanding figure, so any change -- selecting another patient, or a
  // refetch after a stale-state conflict moved the amount -- remounts the form
  // and these initializers run again against the server's current number.
  //
  // The effect this replaces called setState in its body, which React 19
  // rightly flags: it renders once with a stale amount and then again with the
  // fresh one, and the cashier can see the old figure flash.
  const [amount, setAmount] = useState(() =>
    financial.patient_outstanding > 0
      ? financial.patient_outstanding.toFixed(2)
      : "",
  );
  const [method, setMethod] = useState<CashierPaymentMethod>("cash");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");

  const methodSpec = useMemo(
    () => CASHIER_PAYMENT_METHODS.find((entry) => entry.key === method),
    [method],
  );
  const referenceRequired = methodSpec?.referenceRequired ?? false;

  const parsedAmount = Number(amount);
  const amountValid = Number.isFinite(parsedAmount) && parsedAmount > 0;
  const referenceValid = !referenceRequired || reference.trim().length > 0;
  const canSubmit =
    collectability.collectable && amountValid && referenceValid && !submitting;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    onSubmit({
      amount: parsedAmount,
      method,
      reference: reference.trim() || null,
      note: note.trim() || null,
    });
  }

  if (!collectability.collectable) {
    return (
      <div className="rounded-md border border-slate-200 bg-white p-3">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          Collect
        </h3>
        <p className="mt-2 text-sm leading-5 text-slate-600">
          {collectability.reason ??
            "This visit is not collectable at the cashier."}
        </p>
        {receipt ? <ReceiptResult receipt={receipt} /> : null}
      </div>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-3"
    >
      <div className="flex items-baseline justify-between">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          Collect
        </h3>
        <span className="font-mono text-[10px] text-slate-400">
          {financial.currency ?? ""}
        </span>
      </div>

      <label className="text-xs font-medium text-slate-700" htmlFor="cashier-amount">
        Amount
      </label>
      <input
        id="cashier-amount"
        // Focused on mount, and the key-driven remount above means "on mount"
        // is also "whenever the visit or the amount changes".
        autoFocus
        type="number"
        inputMode="decimal"
        step="0.01"
        min="0.01"
        value={amount}
        onChange={(event) => setAmount(event.target.value)}
        className="h-11 w-full rounded-md border border-slate-300 px-3 text-right font-mono text-lg font-bold tabular-nums outline-none transition focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
      />
      <p className="text-[11px] text-slate-500">
        Outstanding {money(financial.patient_outstanding)}
        {financial.patient_paid > 0
          ? ` · already paid ${money(financial.patient_paid)}`
          : ""}
      </p>

      <label className="mt-1 text-xs font-medium text-slate-700" htmlFor="cashier-method">
        Method
      </label>
      <select
        id="cashier-method"
        value={method}
        onChange={(event) =>
          setMethod(event.target.value as CashierPaymentMethod)
        }
        className="h-9 w-full rounded-md border border-slate-300 px-2 text-sm outline-none transition focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
      >
        {CASHIER_PAYMENT_METHODS.map((entry) => (
          <option key={entry.key} value={entry.key}>
            {entry.label}
          </option>
        ))}
      </select>

      {referenceRequired ? (
        <>
          <label
            className="mt-1 text-xs font-medium text-slate-700"
            htmlFor="cashier-reference"
          >
            Reference <span className="text-red-600">*</span>
          </label>
          <input
            id="cashier-reference"
            type="text"
            value={reference}
            onChange={(event) => setReference(event.target.value)}
            placeholder="Transaction / approval number"
            className="h-9 w-full rounded-md border border-slate-300 px-2 text-sm outline-none transition focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
          />
        </>
      ) : null}

      <label className="mt-1 text-xs font-medium text-slate-700" htmlFor="cashier-note">
        Note
      </label>
      <input
        id="cashier-note"
        type="text"
        value={note}
        onChange={(event) => setNote(event.target.value)}
        className="h-9 w-full rounded-md border border-slate-300 px-2 text-sm outline-none transition focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
      />

      {error ? (
        <div className="rounded-md border border-red-200 bg-red-50 px-2.5 py-2 text-xs leading-5 text-red-700">
          {error}
        </div>
      ) : null}

      <button
        type="submit"
        disabled={!canSubmit}
        className="mt-1 h-11 w-full rounded-md bg-emerald-700 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        {submitting ? "Recording..." : "Confirm payment"}
      </button>

      {receipt ? <ReceiptResult receipt={receipt} /> : null}
    </form>
  );
}

/**
 * The receipt just taken.
 *
 * Shows the number and the breakdown. No print or PDF: there is no QWeb report
 * for hospital.charge.receipt, and the accounting flags are surfaced honestly
 * as "not posted" rather than implying a fiscal document this phase does not
 * issue.
 */
function ReceiptResult({ receipt }: { receipt: CashierReceipt }) {
  return (
    <div className="mt-2 rounded-md border border-emerald-200 bg-emerald-50 p-2.5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-emerald-800">
          Receipt {receipt.name}
        </span>
        <span className="font-mono text-sm font-bold tabular-nums text-emerald-900">
          {money(receipt.amount)}
        </span>
      </div>
      <p className="mt-0.5 font-mono text-[11px] text-emerald-700">
        {receipt.payment_method}
        {receipt.payment_reference ? ` · ${receipt.payment_reference}` : ""} ·{" "}
        {receipt.allocations.length} allocation
        {receipt.allocations.length === 1 ? "" : "s"}
      </p>
      {!receipt.accounting.posted ? (
        <p className="mt-1 text-[10px] text-emerald-700">
          Operational receipt. Not yet posted to accounting.
        </p>
      ) : null}
    </div>
  );
}

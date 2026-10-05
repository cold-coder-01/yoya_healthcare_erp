"use client";

import { FormEvent, useMemo, useState } from "react";

import {
  CASHIER_PAYMENT_METHODS,
  type CashierInpatientDetail,
  type CashierPaymentMethod,
  type CashierReceipt,
} from "@/types/cashier";
import { money, settlementPreview } from "@/lib/cashier-format";

/** settlement: the delivered-basis balance (quote-checked by the server).
 *  advance: cash against the physician's estimate, before medical discharge. */
export type InpatientPaymentMode = "settlement" | "advance";

const MODE_TEXT: Record<
  InpatientPaymentMode,
  { title: string; action: string; review: string; outstanding: string; exceeds: string }
> = {
  settlement: {
    title: "Settle inpatient account",
    action: "Record payment",
    review: "Review inpatient payment",
    outstanding: "Outstanding",
    exceeds: "More than the outstanding balance. A settlement cannot exceed it.",
  },
  advance: {
    title: "Record advance",
    action: "Record advance",
    review: "Review inpatient advance",
    outstanding: "Uncovered estimate",
    exceeds: "More than the uncovered estimate. An advance cannot exceed it.",
  },
};

type Props = {
  account: CashierInpatientDetail;
  mode?: InpatientPaymentMode;
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
 * Inpatient settlement entry: enter, REVIEW, then record.
 *
 * The amount defaults to the server's `max_amount` -- the remaining
 * delivered-basis balance -- and may not exceed it. That is deliberate and
 * stricter than the outpatient form: a settlement that over-collects would
 * quietly become a new advance, and the patient came to settle, not to
 * prepay. The server refuses it too; the form just says so before the cashier
 * commits. (Taking an advance remains a Billing Account action.)
 *
 * Re-armed by remount: the parent keys this component on the admission and
 * its remaining balance, so a partial payment or a refetch resets the fields
 * against the server's new number.
 */
export default function CashierInpatientPayment({
  account,
  mode = "settlement",
  receipt,
  submitting,
  error,
  onSubmit,
}: Props) {
  const collectability =
    mode === "advance" ? account.advance_collectability : account.collectability;
  const text = MODE_TEXT[mode];
  const [amount, setAmount] = useState(() =>
    collectability.max_amount > 0 ? collectability.max_amount.toFixed(2) : "",
  );
  const [method, setMethod] = useState<CashierPaymentMethod>("cash");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [reviewing, setReviewing] = useState(false);

  const methodSpec = useMemo(
    () => CASHIER_PAYMENT_METHODS.find((entry) => entry.key === method),
    [method],
  );
  const referenceRequired = methodSpec?.referenceRequired ?? false;

  const parsedAmount = Number(amount);
  const preview = settlementPreview(collectability.max_amount, parsedAmount);
  const amountValid =
    Number.isFinite(parsedAmount) && parsedAmount > 0 && !preview.exceeds;
  const referenceValid = !referenceRequired || reference.trim().length > 0;
  const canReview =
    collectability.collectable && amountValid && referenceValid && !submitting;

  function handleReview(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (canReview) setReviewing(true);
  }

  function handleConfirm() {
    if (!canReview) return;
    setReviewing(false);
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
          {text.title}
        </h3>
        <p className="mt-2 text-sm leading-5 text-slate-600">
          {collectability.reason ?? "Nothing is collectable on this account."}
        </p>
        {receipt ? <SettlementReceipt receipt={receipt} /> : null}
      </div>
    );
  }

  return (
    <>
      <form
        onSubmit={handleReview}
        className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-3"
      >
        <div className="flex items-baseline justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            {text.title}
          </h3>
          <span className="font-mono text-[10px] text-slate-400">
            {account.currency ?? ""}
          </span>
        </div>

        <label className="text-xs font-medium text-slate-700" htmlFor={`${mode}-amount`}>
          Payment amount
        </label>
        <input
          id={`${mode}-amount`}
          autoFocus
          type="number"
          inputMode="decimal"
          step="0.01"
          min="0.01"
          max={collectability.max_amount}
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
          className="h-11 w-full rounded-md border border-slate-300 px-3 text-right font-mono text-lg font-bold tabular-nums outline-none transition focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
        />
        <p className="text-[11px] text-slate-500">
          {text.outstanding} {money(collectability.max_amount)}
        </p>
        {preview.exceeds ? (
          <p className="text-[11px] font-medium text-red-700">
            {text.exceeds}
          </p>
        ) : null}

        <label className="mt-1 text-xs font-medium text-slate-700" htmlFor={`${mode}-method`}>
          Method
        </label>
        <select
          id={`${mode}-method`}
          value={method}
          onChange={(event) => setMethod(event.target.value as CashierPaymentMethod)}
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
              htmlFor={`${mode}-reference`}
            >
              Reference <span className="text-red-600">*</span>
            </label>
            <input
              id={`${mode}-reference`}
              type="text"
              value={reference}
              onChange={(event) => setReference(event.target.value)}
              placeholder="Transaction / approval number"
              className="h-9 w-full rounded-md border border-slate-300 px-2 text-sm outline-none transition focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
            />
          </>
        ) : null}

        <label className="mt-1 text-xs font-medium text-slate-700" htmlFor={`${mode}-note`}>
          Note
        </label>
        <input
          id={`${mode}-note`}
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
          disabled={!canReview}
          className="mt-1 h-11 w-full rounded-md bg-emerald-700 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {submitting ? "Recording..." : text.action}
        </button>

        {receipt ? <SettlementReceipt receipt={receipt} /> : null}
      </form>

      {reviewing ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby={`${mode}-review-title`}
        >
          <div className="w-full max-w-sm rounded-md border border-slate-200 bg-white p-4 shadow-lg">
            <h2 id={`${mode}-review-title`} className="text-sm font-bold text-slate-900">
              {text.review}
            </h2>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              <dt className="text-slate-500">Patient</dt>
              <dd className="text-right font-semibold text-slate-900">
                {account.patient.name}
                {account.patient.identification_code
                  ? ` · ${account.patient.identification_code}`
                  : ""}
              </dd>
              <dt className="text-slate-500">Encounter</dt>
              <dd className="text-right font-mono text-slate-900">{account.encounter.name}</dd>
              <dt className="text-slate-500">Admission</dt>
              <dd className="text-right font-mono text-slate-900">{account.admission.name}</dd>
              <dt className="text-slate-500">Method</dt>
              <dd className="text-right text-slate-900">{methodSpec?.label ?? method}</dd>
              {reference.trim() ? (
                <>
                  <dt className="text-slate-500">Reference</dt>
                  <dd className="text-right font-mono text-slate-900">{reference.trim()}</dd>
                </>
              ) : null}
            </dl>
            <div className="mt-3 rounded border border-slate-200 bg-slate-50 p-2 font-mono text-xs tabular-nums">
              <div className="flex justify-between">
                <span className="font-sans text-slate-600">{text.outstanding} before</span>
                <span>{money(preview.before)}</span>
              </div>
              <div className="flex justify-between">
                <span className="font-sans text-slate-600">Payment</span>
                <span>- {money(preview.payment)}</span>
              </div>
              <div className="mt-1 flex justify-between border-t border-slate-300 pt-1 font-bold text-emerald-800">
                <span className="font-sans">{text.outstanding} after</span>
                <span>{money(preview.after)}</span>
              </div>
            </div>
            <p className="mt-2 text-[10px] leading-4 text-slate-500">
              An operational receipt. It is not posted to accounting here.
            </p>
            <div className="mt-3 flex gap-2">
              <button
                type="button"
                onClick={() => setReviewing(false)}
                className="h-10 flex-1 rounded-md border border-slate-300 bg-white text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Back
              </button>
              <button
                type="button"
                onClick={handleConfirm}
                disabled={!canReview}
                className="h-10 flex-1 rounded-md bg-emerald-700 text-sm font-semibold text-white hover:bg-emerald-800 disabled:bg-slate-300"
              >
                {text.action}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

function SettlementReceipt({ receipt }: { receipt: CashierReceipt }) {
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
        {receipt.payment_reference ? ` · ${receipt.payment_reference}` : ""}
      </p>
      {!receipt.accounting.posted ? (
        <p className="mt-1 text-[10px] text-emerald-700">
          Operational receipt. Not yet posted to accounting.
        </p>
      ) : null}
    </div>
  );
}

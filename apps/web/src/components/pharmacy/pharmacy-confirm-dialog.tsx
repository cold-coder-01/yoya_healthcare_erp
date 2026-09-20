"use client";

import { useEffect, useRef } from "react";

import type { PreparePlan, ValidateLineSummary } from "@/lib/pharmacy-desk-actions";
import { medicineLabel, orDash, qtyLabel } from "@/lib/pharmacy-desk-format";
import type { PharmacyDispenseDetail } from "@/types/pharmacy-desk";

/**
 * The Prepare and Validate confirmation step.
 *
 * FINAL CONFIRMATION IS A POINTER CLICK. There is no key handler anywhere in
 * this dialog, and the confirm button ignores keyboard activation: a browser
 * turns Enter or Space on a focused button into a click with `detail === 0`,
 * and that click is dropped. Initial focus goes to "Go back", so an Enter that
 * was meant for the quantity field can only ever step back.
 *
 * NOTHING PRICED IS SHOWN, because nothing priced reaches the browser.
 */

export type ConfirmMode =
  | { kind: "prepare"; plan: PreparePlan }
  | { kind: "validate"; summary: ValidateLineSummary[] };

function pointerOnly(handler: () => void) {
  return (event: React.MouseEvent<HTMLButtonElement>) => {
    // Keyboard-synthesized clicks carry detail === 0. Only a real pointer
    // click confirms.
    if (event.detail === 0) return;
    handler();
  };
}

export default function PharmacyConfirmDialog({
  detail,
  mode,
  busy,
  onBack,
  onConfirm,
}: {
  detail: PharmacyDispenseDetail;
  mode: ConfirmMode;
  busy: boolean;
  onBack: () => void;
  onConfirm: () => void;
}) {
  const backRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    backRef.current?.focus();
  }, []);

  const isValidate = mode.kind === "validate";
  const title = isValidate ? "Confirm validation" : "Confirm preparation";

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="pharmacy-confirm-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
    >
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-xl">
        <header className="border-b border-slate-200 bg-slate-50 px-4 py-3">
          <h2 id="pharmacy-confirm-title" className="cl-strong font-bold text-slate-900">
            {title}
          </h2>
          <p className="cl-meta text-slate-600">
            {detail.patient?.name ?? "—"} · MRN{" "}
            <span className="font-mono">{orDash(detail.patient?.mrn)}</span> ·{" "}
            <span className="font-mono font-semibold">{detail.dispense_code}</span>
          </p>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <p className="mb-2 cl-secondary text-slate-700">
            {isValidate
              ? "The quantities in the last column will be handed over now. Billing and stock record them in the same step."
              : "Quantities are cumulative: the total intended to have been supplied after the next validation. Nothing is handed over and no stock moves yet."}
          </p>
          <table className="w-full table-fixed border-collapse cl-secondary">
            <thead>
              <tr className="border-b border-slate-200 text-left cl-micro uppercase tracking-[0.06em] text-slate-400">
                <th className="w-[40%] py-1 font-bold">Medicine</th>
                {isValidate ? null : <th className="py-1 pr-2 text-right font-bold">Rx</th>}
                <th className="py-1 pr-2 text-right font-bold">Intended cumulative</th>
                <th className="py-1 pr-2 text-right font-bold">Already delivered</th>
                <th className="py-1 pr-2 text-right font-bold">
                  {isValidate ? "Hand over now" : "Increment"}
                </th>
              </tr>
            </thead>
            <tbody>
              {mode.kind === "prepare"
                ? mode.plan.lines.map((entry) => (
                    <tr key={entry.line.id} className="border-b border-slate-100">
                      <td className="py-1 pr-2 font-semibold text-slate-900">{medicineLabel(entry.line)}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{qtyLabel(entry.line.prescribed_quantity)}</td>
                      <td className="py-1 pr-2 text-right font-bold tabular-nums">{qtyLabel(entry.intended)}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{qtyLabel(entry.supplied)}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{qtyLabel(entry.increment)}</td>
                    </tr>
                  ))
                : mode.summary.map((entry) => (
                    <tr key={entry.line.id} className="border-b border-slate-100">
                      <td className="py-1 pr-2 font-semibold text-slate-900">{medicineLabel(entry.line)}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{qtyLabel(entry.intended)}</td>
                      <td className="py-1 pr-2 text-right tabular-nums">{qtyLabel(entry.delivered)}</td>
                      <td className="py-1 pr-2 text-right font-bold tabular-nums">{qtyLabel(entry.increment)}</td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-slate-200 bg-slate-50 px-4 py-2.5">
          <button
            ref={backRef}
            type="button"
            onClick={onBack}
            disabled={busy}
            className="rounded border border-slate-300 bg-white px-3 py-1.5 cl-secondary font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Go back
          </button>
          <button
            type="button"
            onClick={pointerOnly(onConfirm)}
            disabled={busy}
            className="rounded border border-violet-700 bg-violet-700 px-3 py-1.5 cl-secondary font-bold text-white hover:bg-violet-800 disabled:opacity-50"
          >
            {busy ? "Working…" : title}
          </button>
        </footer>
      </div>
    </div>
  );
}

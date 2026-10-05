"use client";

import { useCallback, useEffect, useRef, type KeyboardEvent as ReactKeyboardEvent } from "react";

import {
  PreviewField,
  PreviewHeader,
  PreviewPill,
  PreviewSection,
} from "@/components/workstation/workstation-preview";
import { accountantAdmissionStatus, REFUND_ACCOUNTING_PENDING } from "@/lib/accountant-format";
import { money } from "@/lib/cashier-format";
import type { AccountantDetail } from "@/types/accountant";

/**
 * REVIEW REFUND: the last look before a refund is recorded.
 *
 * The workstation action-wizard family (Finalize discharge, Review discharge):
 * header and footer fixed, the body the one scroll, X / Escape / Back close it,
 * focus starts on Back and stays inside, and "Record refund" is a pointer click
 * (detail === 0 is ignored). Presentational: the desk owns the token and the
 * request, and the server re-checks every figure under the admission's lock.
 */
export default function AccountantRefundReviewDialog({
  account,
  amount,
  after,
  reason,
  busy,
  error,
  onConfirm,
  onClose,
}: {
  account: AccountantDetail;
  amount: number;
  after: number;
  reason: string;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const backRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    backRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [busy, onClose]);

  const onKeyDownPanel = useCallback((event: ReactKeyboardEvent) => {
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const focusable = panel.querySelectorAll<HTMLElement>("button:not([disabled]), [href]");
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }, []);

  const titleId = "accountant-refund-review-title";
  const currency = account.currency ? `${account.currency} ` : "";
  const figure = (value: number) => `${currency}${money(value)}`;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/40 p-4"
    >
      <div
        ref={panelRef}
        onKeyDown={onKeyDownPanel}
        className="flex max-h-[88vh] w-full max-w-[640px] flex-col overflow-hidden rounded-lg border border-slate-300 bg-white shadow-xl"
      >
        <div className="flex shrink-0 items-start gap-2 border-b border-slate-200 px-4 py-2.5">
          <div className="min-w-0 flex-1">
            <PreviewHeader
              titleId={titleId}
              title="Review refund"
              identifiers={[
                { label: "Patient", value: account.patient.name },
                { label: "MRN", value: account.patient.identification_code ?? "—" },
                { label: "Encounter", value: account.encounter.name },
                { label: "Admission", value: account.admission.name },
              ]}
              status={<PreviewPill tone="warn">Refund out</PreviewPill>}
            />
          </div>
          <button
            type="button"
            aria-label="Close refund review"
            disabled={busy}
            onClick={onClose}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-600 disabled:opacity-40"
          >
            <svg aria-hidden viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
              <path d="M5.3 5.3a1 1 0 0 1 1.4 0L10 8.6l3.3-3.3a1 1 0 1 1 1.4 1.4L11.4 10l3.3 3.3a1 1 0 0 1-1.4 1.4L10 11.4l-3.3 3.3a1 1 0 0 1-1.4-1.4L8.6 10 5.3 6.7a1 1 0 0 1 0-1.4Z" />
            </svg>
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
          <PreviewSection title="Admission" columns={2}>
            <PreviewField label="Admission status">{accountantAdmissionStatus(account.admission)}</PreviewField>
            <PreviewField label="Financial state">{account.lane_label ?? "—"}</PreviewField>
          </PreviewSection>
          <PreviewSection title="Refund">
            <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-y-1 font-mono tabular-nums cl-body">
              <dt className="font-sans text-slate-600">Refundable before</dt>
              <dd className="text-right" data-testid="refund-before">{figure(account.refund.max_amount)}</dd>
              <dt className="font-sans text-slate-600">Refund amount</dt>
              <dd className="text-right font-bold text-amber-900" data-testid="refund-amount">− {figure(amount)}</dd>
              <dt className="border-t border-slate-300 pt-1 font-sans font-bold text-slate-900">Refundable after</dt>
              <dd className="border-t border-slate-300 pt-1 text-right font-bold" data-testid="refund-after">{figure(after)}</dd>
            </dl>
          </PreviewSection>
          <PreviewSection title="Reason">
            <p className="whitespace-pre-wrap break-words cl-body text-slate-900">{reason}</p>
          </PreviewSection>
          <PreviewSection title="Accounting">
            <p className="cl-meta text-slate-600">
              Records an operational refund of the patient&apos;s own unapplied money. It does not
              reopen the admission, the visit or the bed, and does not change delivered care.{" "}
              {REFUND_ACCOUNTING_PENDING}.
            </p>
          </PreviewSection>
        </div>

        {error ? (
          <p role="status" className="mx-4 my-2 shrink-0 rounded border border-red-300 bg-red-50 px-2 py-1 cl-meta text-red-900">
            {error}
          </p>
        ) : null}

        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-slate-200 px-4 py-3">
          <button
            ref={backRef}
            type="button"
            disabled={busy}
            onClick={onClose}
            className="h-8 rounded-md border border-slate-300 bg-white px-3 cl-meta font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >
            Back
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={(event) => {
              // Pointer only: a keyboard-activated click reports detail 0.
              if (event.detail === 0) return;
              onConfirm();
            }}
            className="h-8 rounded-md bg-amber-700 px-3 cl-meta font-bold text-white hover:bg-amber-800 disabled:opacity-40"
          >
            {busy ? "Recording…" : "Record refund"}
          </button>
        </footer>
      </div>
    </div>
  );
}

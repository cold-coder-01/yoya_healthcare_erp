"use client";

import { useEffect, useRef } from "react";

import type { AdmissionDetail } from "@/types/admissions-desk";

type ActionMessage = { tone: "red" | "amber" | "green"; text: string };

/**
 * CANCEL REQUEST (Admissions Slice 3): one confirmation for a DRAFT request.
 *
 * Offered only while the server says `can_cancel_request` -- a draft, and a
 * role that may cancel it. It is never shown after admission: a patient in a
 * bed leaves by discharge, not by withdrawing the request.
 *
 * The confirm is a pointer click (detail === 0 is ignored) and focus starts on
 * "Keep request", so no key press can cancel by accident.
 */
export default function CancelRequestDialog({
  detail,
  busy,
  message,
  onConfirm,
  onClose,
}: {
  detail: AdmissionDetail;
  busy: boolean;
  message: ActionMessage | null;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const keepRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    keepRef.current?.focus();
  }, []);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="cancel-request-dialog-title"
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/40 p-4"
    >
      <div className="flex w-full max-w-[520px] flex-col overflow-hidden rounded-lg border border-slate-300 bg-white shadow-xl">
        <header className="border-b border-slate-200 px-4 py-3">
          <h2 id="cancel-request-dialog-title" className="cl-head font-bold text-slate-950">
            Cancel admission request
          </h2>
          <p className="font-mono cl-meta text-slate-600">
            {detail.patient?.name ?? "—"} · {detail.reference} · rev {detail.workflow_revision}
          </p>
        </header>
        <div className="px-4 py-3">
          <ul className="list-disc pl-5 cl-body text-slate-800">
            <li>The patient has not been admitted.</li>
            <li>No bed is occupied by this request.</li>
            <li>The request will be cancelled. The doctor may request admission again if needed.</li>
          </ul>
        </div>
        {message ? (
          <p
            role="status"
            className={`mx-4 mb-2 rounded border px-2 py-1 cl-meta ${
              message.tone === "red"
                ? "border-red-300 bg-red-50 text-red-900"
                : message.tone === "amber"
                  ? "border-amber-300 bg-amber-50 text-amber-900"
                  : "border-emerald-300 bg-emerald-50 text-emerald-900"
            }`}
          >
            {message.text}
          </p>
        ) : null}
        <footer className="flex items-center justify-end gap-2 border-t border-slate-200 px-4 py-3">
          <button
            ref={keepRef}
            type="button"
            disabled={busy}
            onClick={onClose}
            className="h-8 rounded-md border border-slate-300 bg-white px-3 cl-meta font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >
            Keep request
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={(event) => {
              // Pointer only: a keyboard-activated click reports detail 0.
              if (event.detail === 0) return;
              onConfirm();
            }}
            className="h-8 rounded-md bg-red-700 px-3 cl-meta font-bold text-white hover:bg-red-800 disabled:opacity-40"
          >
            {busy ? "Cancelling…" : "Cancel request"}
          </button>
        </footer>
      </div>
    </div>
  );
}

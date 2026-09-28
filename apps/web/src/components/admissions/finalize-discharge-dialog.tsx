"use client";

import { useEffect, useRef } from "react";

import { formatHospitalDateTime } from "@/lib/clinical-format";
import { financialLabel, locationLabel } from "@/lib/admissions-desk-format";
import type { AdmissionDetail } from "@/types/admissions-desk";

type ActionMessage = { tone: "red" | "amber" | "green"; text: string };

/**
 * FINALIZE DISCHARGE (Admissions Slice 4): one review, one confirm.
 *
 * Offered only when the server says so twice over -- the role's `discharge`
 * capability AND this record's `discharge.can_finalize_discharge`. The review
 * shows the patient, the admission, where they are, the doctor's readiness,
 * the financial STATE (never an amount) and the server's warnings. The
 * server re-checks everything under its locks; an unsettled account is refused
 * there, not here.
 *
 * The confirm is a pointer click (detail === 0 is ignored) and focus starts on
 * "Go back", the rule every irreversible act on this desk follows.
 */
export default function FinalizeDischargeDialog({
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
  const backRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    backRef.current?.focus();
  }, []);

  const discharge = detail.discharge;
  const financial = financialLabel(detail.financial);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="finalize-discharge-dialog-title"
      className="fixed inset-0 z-40 flex items-center justify-center bg-slate-950/40 p-4"
    >
      <div className="flex max-h-[85vh] w-full max-w-[600px] flex-col overflow-hidden rounded-lg border border-slate-300 bg-white shadow-xl">
        <header className="shrink-0 border-b border-slate-200 px-4 py-3">
          <h2 id="finalize-discharge-dialog-title" className="cl-head font-bold text-slate-950">
            Finalize discharge
          </h2>
          <p className="font-mono cl-meta text-slate-600">
            {detail.patient?.name ?? "—"} · {detail.reference} · rev {detail.workflow_revision}
          </p>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <p className="cl-body text-slate-800">
            Discharge this patient now? The bed is released and the visit is completed immediately.
          </p>
          <dl className="mt-3 grid grid-cols-[140px_minmax(0,1fr)] gap-y-1 cl-body">
            <dt className="text-slate-500">Patient</dt>
            <dd className="font-bold text-slate-900">{detail.patient?.name ?? "—"}</dd>
            <dt className="text-slate-500">Admission</dt>
            <dd className="font-mono">{detail.reference}</dd>
            <dt className="text-slate-500">Ward / room / bed</dt>
            <dd className="font-mono">{locationLabel(detail.location)}</dd>
            <dt className="text-slate-500">Medical</dt>
            <dd>
              {discharge?.medical_ready
                ? `Ready for discharge${discharge.medical_ready_by ? ` · ${discharge.medical_ready_by}` : ""} · ${formatHospitalDateTime(discharge.medical_ready_at, "—")}`
                : "Not declared ready"}
            </dd>
            <dt className="text-slate-500">Financial</dt>
            <dd className={financial.tone === "warn" ? "text-amber-900" : financial.tone === "ok" ? "text-emerald-800" : "text-slate-700"}>
              {financial.text}
            </dd>
            <dt className="text-slate-500">Revision</dt>
            <dd className="font-mono">{detail.workflow_revision}</dd>
          </dl>
          {discharge?.warnings.length ? (
            <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2">
              <p className="cl-meta font-bold uppercase tracking-wide text-amber-900">Warnings</p>
              <ul className="mt-1 list-disc pl-5 cl-body text-amber-900">
                {discharge.warnings.map((warning) => (
                  <li key={warning.code}>{warning.message}</li>
                ))}
              </ul>
            </div>
          ) : null}
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
        <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-slate-200 px-4 py-3">
          <button
            ref={backRef}
            type="button"
            disabled={busy}
            onClick={onClose}
            className="h-8 rounded-md border border-slate-300 bg-white px-3 cl-meta font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-40"
          >
            Go back
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={(event) => {
              // Pointer only: a keyboard-activated click reports detail 0.
              if (event.detail === 0) return;
              onConfirm();
            }}
            className="h-8 rounded-md bg-emerald-700 px-3 cl-meta font-bold text-white hover:bg-emerald-800 disabled:opacity-40"
          >
            {busy ? "Discharging…" : "Finalize discharge"}
          </button>
        </footer>
      </div>
    </div>
  );
}

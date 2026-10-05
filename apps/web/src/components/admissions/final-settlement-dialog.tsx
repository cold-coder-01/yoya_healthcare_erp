"use client";

import { useEffect, useState } from "react";

import SettlementView from "@/components/inpatient/settlement-view";
import { messageFromPayload } from "@/lib/api-error";
import type { AdmissionSettlementResponse } from "@/types/inpatient-settlement";
import type { ApiEnvelope } from "@/types/reception";

type Props = {
  admissionId: number;
  onClose: () => void;
  /** Offered only when the server says the discharge gate is open. */
  onContinueToFinalize?: () => void;
};

/**
 * The Admissions Desk's FINAL SETTLEMENT window.
 *
 * One request to the server's settlement authority; the stages and the bar
 * report what that request computed. Read-only: the clerk sees why the
 * discharge is (or is not) allowed, and the Cashier collects. Opening it
 * again re-runs the calculation, so a bed-day that started meanwhile is in it.
 */
export default function FinalSettlementDialog({ admissionId, onClose, onContinueToFinalize }: Props) {
  const [data, setData] = useState<AdmissionSettlementResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`/api/admissions/${admissionId}/settlement`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<AdmissionSettlementResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success || !payload.data) {
          setError(messageFromPayload(payload, "Unable to calculate the final settlement."));
          return;
        }
        setData(payload.data);
      } catch (caught) {
        if (controller.signal.aborted || (caught as Error)?.name === "AbortError") return;
        setError("Unable to reach the YOYA EMR gateway.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [admissionId]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="final-settlement-title"
    >
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-md border border-slate-200 bg-slate-50 shadow-lg">
        <header className="border-b border-slate-200 bg-white px-4 py-2.5">
          <h2 id="final-settlement-title" className="text-sm font-bold text-slate-900">
            Final settlement
          </h2>
          {data ? (
            <p className="font-mono text-[11px] text-slate-500">
              {data.patient.name} · {data.patient.identification_code ?? "-"} ·{" "}
              {data.encounter.name} · {data.admission.reference}
            </p>
          ) : null}
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          <SettlementView
            settlement={data?.settlement ?? null}
            loading={loading}
            error={error}
            currency={data?.currency ?? null}
          />
          {data && !data.discharge_allowed ? (
            <p className="mt-2 text-xs text-slate-600">
              The discharge stays blocked until the Cashier settles the balance.
            </p>
          ) : null}
        </div>
        <footer className="flex gap-2 border-t border-slate-200 bg-white px-4 py-2.5">
          <button
            type="button"
            onClick={onClose}
            className="h-9 flex-1 rounded-md border border-slate-300 bg-white text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Close
          </button>
          {data?.discharge_allowed && onContinueToFinalize ? (
            <button
              type="button"
              onClick={onContinueToFinalize}
              className="h-9 flex-1 rounded-md bg-emerald-700 text-sm font-semibold text-white hover:bg-emerald-800"
            >
              Continue to finalize discharge
            </button>
          ) : null}
        </footer>
      </div>
    </div>
  );
}

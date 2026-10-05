"use client";

import { useEffect, useState } from "react";

import { money } from "@/lib/cashier-format";
import type { AdmissionFinancialClearance } from "@/types/admissions-desk";
import type { AdmissionSettlementResponse } from "@/types/inpatient-settlement";
import type { ApiEnvelope } from "@/types/reception";

type Props = {
  admissionId: number;
  clearance: AdmissionFinancialClearance;
  /** The discharging clerk / oversight: may read the figures. */
  mayReadAmounts: boolean;
};

const TONE: Record<string, string> = {
  cleared: "border-emerald-300 bg-emerald-50 text-emerald-900",
  emergency_bypass: "border-red-300 bg-red-50 text-red-900",
  sponsored: "border-sky-300 bg-sky-50 text-sky-900",
};

/**
 * PRE-ADMISSION FINANCIAL READINESS on a pending request.
 *
 * The state and its sentence come from the server's clearance verdict and are
 * shown to every desk role -- the ward nurse included -- so everyone can see
 * WHY Admit is not offered. The figures (estimate, received, remaining) come
 * only from the clerk's settlement route, and only for the roles that may read
 * it; nothing here computes them.
 */
export default function AdmissionClearanceNote({ admissionId, clearance, mayReadAmounts }: Props) {
  const [figures, setFigures] = useState<AdmissionSettlementResponse["admission_clearance"]>(null);
  const [currency, setCurrency] = useState<string | null>(null);

  useEffect(() => {
    if (!mayReadAmounts || clearance.state !== "awaiting_advance") return;
    const controller = new AbortController();
    (async () => {
      try {
        const response = await fetch(`/api/admissions/${admissionId}/settlement`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<AdmissionSettlementResponse>;
        if (controller.signal.aborted || !response.ok || !payload.success || !payload.data) return;
        setFigures(payload.data.admission_clearance);
        setCurrency(payload.data.currency);
      } catch {
        /* The sentence alone still says why; the figures are a convenience. */
      }
    })();
    return () => controller.abort();
  }, [admissionId, clearance.state, mayReadAmounts]);

  return (
    <div
      className={`mt-2 rounded-md border px-2.5 py-1.5 cl-meta ${
        TONE[clearance.state] ?? "border-amber-300 bg-amber-50 text-amber-900"
      }`}
      role="status"
    >
      <p className="font-bold">{clearance.message}</p>
      {figures && clearance.state === "awaiting_advance" ? (
        <p className="mt-0.5 font-mono tabular-nums">
          Advance required: {currency ? `${currency} ` : ""}
          {money(figures.estimate)} · Received: {money(figures.advance_received)} · Remaining:{" "}
          {money(figures.remaining)}
        </p>
      ) : null}
    </div>
  );
}

"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { codeFromPayload, messageFromPayload } from "@/lib/api-error";
import { money } from "@/lib/cashier-format";
import { formatHospitalDateTime } from "@/lib/clinical-format";
import { ADDITIONAL_ADVANCE_NOTICE, estimateLockLabel } from "@/lib/inpatient-estimate-lock";
import type { DoctorEstimateResponse } from "@/types/inpatient-settlement";
import type { ApiEnvelope } from "@/types/reception";

type Props = {
  appointmentId: number;
  /** The consultation strip: same section, tighter spacing. */
  compact?: boolean;
};

/**
 * INPATIENT ESTIMATE on the Doctor Desk (Advance slice).
 *
 * The physician states what the stay is expected to cost, and why. It is an
 * ESTIMATE: the doctor takes no money here and sees no payment, balance or
 * settlement -- the Cashier takes the advance against it. Revisions are
 * numbered and audited on the server.
 *
 * Offered only when the server says this doctor may edit (`can_edit`: the
 * admission's own physician, or oversight, and the estimate is not LOCKED).
 * It is a running forecast: revisable through the stay, each revision kept.
 * Locked (`locked_reason`) once the doctor's discharge request has recorded
 * medical readiness -- not when the review opens -- it stays visible,
 * read-only; the server refuses an edit either way. When the current estimate
 * asks more advance than was collected the server sends a FLAG and this says
 * so, with no figure: the Cashier owns the amounts. One operation token per intended
 * estimate: an unknown outcome keeps it, so a retry replays rather than
 * recording twice.
 */
export default function DoctorInpatientEstimate({ appointmentId, compact = false }: Props) {
  const [data, setData] = useState<DoctorEstimateResponse | null>(null);
  const [hidden, setHidden] = useState(false);
  const [editing, setEditing] = useState(false);
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Bumped to re-read the estimate after the server reports it frozen. */
  const [reloadKey, setReloadKey] = useState(0);
  const pending = useRef<{ signature: string; token: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`/api/doctor/visits/${appointmentId}/admission-estimate`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<DoctorEstimateResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success || !payload.data) {
          // No admission this doctor may estimate on this visit: say nothing.
          setHidden(true);
          return;
        }
        setData(payload.data);
      } catch {
        if (!controller.signal.aborted) setHidden(true);
      }
    }
    void load();
    return () => controller.abort();
  }, [appointmentId, reloadKey]);

  if (hidden || !data) return null;

  const parsed = Number(amount);
  const valid = Number.isFinite(parsed) && parsed > 0 && reason.trim().length > 0;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!valid || busy || !data) return;
    const signature = `${parsed.toFixed(2)}|${reason.trim()}|${data.admission.revision}`;
    if (pending.current?.signature !== signature) {
      pending.current = { signature, token: crypto.randomUUID() };
    }
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/api/doctor/visits/${appointmentId}/admission-estimate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          operation_token: pending.current.token,
          expected_revision: data.admission.revision,
          amount: parsed,
          reason: reason.trim(),
        }),
      });
      const payload = (await response.json()) as ApiEnvelope<DoctorEstimateResponse>;
      if (!response.ok || !payload.success || !payload.data) {
        // A definite refusal: the next attempt is a new request.
        if (response.status < 500) pending.current = null;
        setError(messageFromPayload(payload, "Unable to record the estimate."));
        const code = codeFromPayload(payload);
        if (code === "admission_estimate_locked") {
          // Locked since this screen loaded: show the locked estimate instead.
          setEditing(false);
          setReloadKey((key) => key + 1);
        }
        return;
      }
      pending.current = null;
      setData(payload.data);
      setEditing(false);
    } catch {
      setError("Unable to reach the YOYA EMR gateway. Retry to finish the same request safely.");
    } finally {
      setBusy(false);
    }
  }

  const estimate = data.estimate;
  const locked = estimateLockLabel(data.locked_reason);
  const earlier = (data.history ?? []).filter((row) => row.revision !== estimate.revision);
  return (
    <div className={compact ? "rounded-md border border-slate-200 bg-white px-2 py-1.5" : "mt-2 rounded-md border border-slate-200 bg-white p-2.5"}>
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-slate-600">
          Inpatient estimate
        </h4>
        {locked ? (
          <span className="shrink-0 rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-600">
            Estimate locked
          </span>
        ) : null}
        {data.can_edit && !locked && !editing ? (
          <button
            type="button"
            onClick={() => {
              setAmount(estimate.amount > 0 ? estimate.amount.toFixed(2) : "");
              setReason(estimate.reason ?? "");
              setError(null);
              setEditing(true);
            }}
            className="rounded border border-slate-300 px-2 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-slate-50"
          >
            {estimate.amount > 0 ? "Revise estimate" : "Give estimate"}
          </button>
        ) : null}
      </div>

      {estimate.amount > 0 ? (
        <div className="mt-1 text-xs text-slate-700">
          <p className="font-mono text-sm font-bold tabular-nums text-slate-900">
            {data.currency ? `${data.currency} ` : ""}
            {money(estimate.amount)}
          </p>
          {estimate.reason ? <p className="mt-0.5">{estimate.reason}</p> : null}
          <p className="mt-0.5 text-[11px] text-slate-500">
            Revision {estimate.revision}
            {estimate.estimated_by ? ` · ${estimate.estimated_by}` : ""}
            {estimate.estimated_at ? ` · ${formatHospitalDateTime(estimate.estimated_at, "")}` : ""}
          </p>
          {locked ? <p className="mt-0.5 text-[11px] text-slate-500">{locked}</p> : null}
          {!locked && data.additional_advance_required ? (
            <p className="mt-1 rounded border border-violet-200 bg-violet-50 px-2 py-0.5 text-[11px] font-medium text-violet-900">
              {ADDITIONAL_ADVANCE_NOTICE}
            </p>
          ) : null}
          {earlier.length ? (
            <details className="mt-1 text-[11px] text-slate-500">
              <summary className="cursor-pointer select-none">Earlier revisions ({earlier.length})</summary>
              <ol className="mt-0.5 flex flex-col gap-0.5">
                {[...earlier].reverse().map((row) => (
                  <li key={row.revision} className="break-words">
                    <span className="font-mono tabular-nums text-slate-700">
                      Rev {row.revision} · {data.currency ? `${data.currency} ` : ""}
                      {money(row.amount)}
                    </span>
                    {row.estimated_by ? ` · ${row.estimated_by}` : ""}
                    {row.estimated_at ? ` · ${formatHospitalDateTime(row.estimated_at, "")}` : ""}
                    {row.reason ? ` · ${row.reason}` : ""}
                  </li>
                ))}
              </ol>
            </details>
          ) : null}
        </div>
      ) : !editing ? (
        <p className="mt-1 text-xs text-slate-500">
          No estimate yet. The Cashier takes the advance against it before a bed is assigned.
        </p>
      ) : null}

      {error && !editing ? (
        <p className="mt-1 rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">{error}</p>
      ) : null}

      {editing && !locked ? (
        <form onSubmit={handleSubmit} className="mt-2 flex flex-col gap-1.5">
          <label className="text-xs font-medium text-slate-700" htmlFor="estimate-amount">
            Estimated cost of the stay
          </label>
          <input
            id="estimate-amount"
            type="number"
            inputMode="decimal"
            step="0.01"
            min="0.01"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 px-2 text-right font-mono text-sm font-bold tabular-nums outline-none focus:border-sky-600"
          />
          <label className="text-xs font-medium text-slate-700" htmlFor="estimate-reason">
            Basis for the estimate <span className="text-red-600">*</span>
          </label>
          <textarea
            id="estimate-reason"
            rows={2}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="e.g. Appendectomy, 4 bed-days, post-op antibiotics"
            className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm outline-none focus:border-sky-600"
          />
          <p className="text-[11px] text-slate-500">
            An estimate is not a bill. Only care actually delivered is charged.
          </p>
          {error ? (
            <p className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700">{error}</p>
          ) : null}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setError(null);
              }}
              className="h-8 flex-1 rounded-md border border-slate-300 bg-white text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!valid || busy}
              className="h-8 flex-1 rounded-md bg-sky-700 text-xs font-semibold text-white hover:bg-sky-800 disabled:bg-slate-300"
            >
              {busy ? "Saving..." : "Save estimate"}
            </button>
          </div>
        </form>
      ) : null}
    </div>
  );
}

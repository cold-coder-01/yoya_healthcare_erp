"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { hospitalToday } from "@/lib/clinical-format";
import { newIdempotencyKey } from "@/lib/cashier-format";
import type {
  ApiEnvelope,
  CashierCapabilities,
  CashierPaymentMethod,
  CashierPaymentResult,
  CashierReceipt,
  CashierVisitDetail,
  CashierWorklist,
  CashierWorklistRow,
} from "@/types/cashier";

import CashierFinancialPanel from "./cashier-financial-panel";
import CashierPaymentForm from "./cashier-payment-form";
import CashierQueue from "./cashier-queue";

const LANES = [
  { key: "", label: "All" },
  { key: "collect", label: "Awaiting" },
  { key: "partial", label: "Part paid" },
  { key: "blocked", label: "Blocked" },
] as const;

/**
 * The Cashier workstation.
 *
 * THE RULE THIS COMPONENT IS BUILT AROUND: the server is the only source of
 * financial truth. Every amount rendered comes from a payload; nothing is
 * added, subtracted or compared here to decide what may be collected. After
 * ANY failed payment the visit is refetched rather than retried, because the
 * most likely cause of a refusal is that the split changed underneath -- and a
 * blind retry would then charge against a number that no longer exists.
 */
export default function CashierWorkstation() {
  const [date, setDate] = useState(hospitalToday());
  const [lane, setLane] = useState<string>("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [rows, setRows] = useState<CashierWorklistRow[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [capabilities, setCapabilities] = useState<CashierCapabilities | null>(
    null,
  );
  const [truncated, setTruncated] = useState(false);
  const [queueLoading, setQueueLoading] = useState(true);
  const [queueError, setQueueError] = useState<string | null>(null);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [visit, setVisit] = useState<CashierVisitDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [receipt, setReceipt] = useState<CashierReceipt | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [paymentError, setPaymentError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [detailToken, setDetailToken] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (debouncedSearch) params.set("q", debouncedSearch);
    return params.toString();
  }, [date, debouncedSearch]);

  // --- queue -------------------------------------------------------
  useEffect(() => {
    const controller = new AbortController();

    async function loadQueue() {
      setQueueLoading(true);
      try {
        const response = await fetch(`/api/cashier/worklist?${queryString}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<CashierWorklist>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success || !payload.data) {
          setRows([]);
          setQueueError(
            messageFromPayload(payload, "Unable to load the cashier queue."),
          );
          return;
        }

        setRows(payload.data.rows);
        setCounts(payload.data.lane_counts ?? {});
        setCapabilities(payload.data.capabilities);
        setTruncated(payload.data.truncated);
        setQueueError(null);
      } catch (error) {
        if (controller.signal.aborted) return;
        if ((error as Error)?.name === "AbortError") return;
        setQueueError("Unable to reach the YOYA EMR gateway.");
      } finally {
        if (!controller.signal.aborted) setQueueLoading(false);
      }
    }

    void loadQueue();
    return () => controller.abort();
  }, [queryString, refreshToken]);

  // --- detail ------------------------------------------------------
  //
  // Fetched by an effect keyed on the selection and a refresh token, mirroring
  // the queue effect above. The token is what lets the payment handler ask for
  // a re-read WITHOUT calling a setState-bearing function synchronously in an
  // effect body -- the pattern React 19 flags, because it renders once with
  // stale data and again with fresh.
  useEffect(() => {
    if (selectedId === null) return;
    const controller = new AbortController();

    async function loadVisit(appointmentId: number) {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await fetch(`/api/cashier/visits/${appointmentId}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload =
          (await response.json()) as ApiEnvelope<CashierVisitDetail>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success || !payload.data) {
          setVisit(null);
          setDetailError(
            messageFromPayload(payload, "Unable to load the selected visit."),
          );
          return;
        }
        setVisit(payload.data);
      } catch (error) {
        if (controller.signal.aborted) return;
        if ((error as Error)?.name === "AbortError") return;
        setVisit(null);
        setDetailError("Unable to reach the YOYA EMR gateway.");
      } finally {
        if (!controller.signal.aborted) setDetailLoading(false);
      }
    }

    void loadVisit(selectedId);
    return () => controller.abort();
  }, [selectedId, detailToken]);

  function handleSelect(appointmentId: number) {
    setSelectedId(appointmentId);
    setVisit(null);
    setReceipt(null);
    setPaymentError(null);
  }

  // --- payment -----------------------------------------------------
  const handlePayment = useCallback(
    async (input: {
      amount: number;
      method: CashierPaymentMethod;
      reference: string | null;
      note: string | null;
    }) => {
      if (selectedId === null) return;
      setSubmitting(true);
      setPaymentError(null);

      try {
        const response = await fetch(
          `/api/cashier/visits/${selectedId}/payments`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              amount: input.amount,
              payment_method: input.method,
              payment_reference: input.reference,
              note: input.note,
              // One key per ATTEMPT. A retry after a refusal is a new attempt
              // against a re-read state, so it correctly gets a new key.
              idempotency_key: newIdempotencyKey(),
            }),
          },
        );
        const payload = (await response.json()) as ApiEnvelope<CashierPaymentResult>;

        if (!response.ok || !payload.success || !payload.data) {
          setPaymentError(
            messageFromPayload(payload, "Unable to record the payment."),
          );
          // SERVER-AUTHORITATIVE RECOVERY. Whatever went wrong -- the sponsor
          // share moved, another cashier paid, the visit already cleared -- the
          // cure is the same: re-read the canonical state and show it. Never
          // resubmit automatically.
          setDetailToken((token) => token + 1);
          setRefreshToken((token) => token + 1);
          return;
        }

        // The payment response IS the canonical detail plus the receipt, so the
        // whole panel is replaced from one payload rather than re-derived.
        const { receipt: created, ...detail } = payload.data;
        setVisit(detail as CashierVisitDetail);
        setReceipt(created);
        setPaymentError(null);
        // Refresh the queue so a now-cleared visit drops out of it. The stage
        // is derived server-side; nothing here writes one.
        setRefreshToken((token) => token + 1);
      } catch {
        setPaymentError("Unable to reach the YOYA EMR gateway.");
        // A network failure is the one case where the payment may or may not
        // have landed. Re-read rather than assume either way.
        setDetailToken((token) => token + 1);
      } finally {
        setSubmitting(false);
      }
    },
    [selectedId],
  );

  const laneRows = useMemo(
    () => (lane ? rows.filter((row) => row.lane === lane) : rows),
    [rows, lane],
  );

  const deniedDesk = capabilities !== null && !capabilities.cashier_desk;

  return (
    <div className="flex h-[calc(100vh-5rem)] min-h-0 gap-2">
      {/* LEFT: queue */}
      <section className="flex w-[300px] shrink-0 flex-col rounded-md border border-slate-200 bg-white">
        <div className="border-b border-slate-200 p-2">
          <div className="flex items-center gap-1.5">
            <input
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
              className="h-8 rounded border border-slate-300 px-1.5 text-xs outline-none focus:border-emerald-600"
              aria-label="Queue date"
            />
            <input
              type="search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Name, MRN, visit"
              className="h-8 min-w-0 flex-1 rounded border border-slate-300 px-2 text-xs outline-none focus:border-emerald-600"
              aria-label="Search the cashier queue"
            />
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {LANES.map((entry) => {
              const active = lane === entry.key;
              const count = entry.key ? (counts[entry.key] ?? 0) : rows.length;
              return (
                <button
                  key={entry.key || "all"}
                  type="button"
                  onClick={() => setLane(entry.key)}
                  className={`rounded border px-1.5 py-0.5 text-[11px] font-medium transition ${
                    active
                      ? "border-emerald-600 bg-emerald-600 text-white"
                      : "border-slate-300 bg-white text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {entry.label} {count}
                </button>
              );
            })}
          </div>
        </div>

        <CashierQueue
          rows={laneRows}
          selectedId={selectedId}
          loading={queueLoading}
          error={queueError}
          truncated={truncated}
          onSelect={handleSelect}
        />
      </section>

      {/* CENTER: the split */}
      <section className="flex min-w-0 flex-1 flex-col">
        {deniedDesk ? (
          <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            This account does not have access to the Cashier Desk.
          </div>
        ) : detailLoading ? (
          <div className="p-4 text-sm text-slate-500" role="status">
            Loading visit...
          </div>
        ) : detailError ? (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {detailError}
          </div>
        ) : visit ? (
          <CashierFinancialPanel visit={visit} />
        ) : (
          <div className="flex flex-1 items-center justify-center rounded-md border border-dashed border-slate-300 bg-white">
            <p className="text-sm text-slate-500">
              Select a patient from the queue.
            </p>
          </div>
        )}
      </section>

      {/* RIGHT: collect */}
      <section className="w-[320px] shrink-0 overflow-y-auto">
        {visit ? (
          <CashierPaymentForm
            // Remount on a new visit OR a changed outstanding figure, so the
            // amount field re-arms from the server's current number without an
            // effect. See the comment in cashier-payment-form.
            key={`${visit.appointment.id}:${visit.financial.patient_outstanding}`}
            visit={visit}
            receipt={receipt}
            submitting={submitting}
            error={paymentError}
            onSubmit={handlePayment}
          />
        ) : (
          <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
            Payment entry appears once a patient is selected.
          </div>
        )}
      </section>
    </div>
  );
}

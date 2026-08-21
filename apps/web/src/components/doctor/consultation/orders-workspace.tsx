"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  EMPTY_ORDER_FORM,
  ORDER_KINDS,
  addTest,
  buildOrderPayload,
  canSubmitOrder,
  isSelected,
  labPriorityLabel,
  orderTestSummary,
  removeTest,
  testLabel,
} from "@/lib/laboratory-format";
import type { ApiEnvelope } from "@/types/doctor";
import type {
  DoctorDiagnosis,
  DoctorDiagnosisResponse,
} from "@/types/doctor-diagnosis";
import type {
  DoctorLabOrder,
  DoctorLabOrderResponse,
  LabCatalogueResponse,
  LabOrderForm,
  LabTestOption,
} from "@/types/doctor-laboratory";
import { LAB_PRIORITIES } from "@/types/doctor-laboratory";

/**
 * The ORDERS section of the active consultation.
 *
 * ONLY LABORATORY IS LIVE. The other order kinds are rendered as inert text
 * with no handler and no tab stop, because a control that looks pressable and
 * does nothing is worse than an honest label in a clinical tool.
 *
 * THE DESK PLACES A CLINICAL ORDER; IT NEVER CREATES A CHARGE. Submitting runs
 * Odoo's own action_confirm_request(), where hospital_billing validates every
 * test's billing configuration and raises one charge per test, all-or-nothing.
 * Nothing here knows what a test costs, and the payload carries no money.
 *
 * NOTHING IS SENT UNTIL "PLACE LAB ORDER". Searching and selecting are local;
 * the one request that leaves this component is the submission itself, and it
 * carries an idempotency token so a double click cannot bill the patient twice.
 */

const STATUS_TONE: Record<string, string> = {
  awaiting_clearance: "border-amber-300 bg-amber-50 text-amber-900",
  ready_for_collection: "border-emerald-300 bg-emerald-50 text-emerald-900",
  collected: "border-sky-300 bg-sky-50 text-sky-900",
  result_pending: "border-sky-300 bg-sky-50 text-sky-900",
  result_available: "border-emerald-400 bg-emerald-50 text-emerald-900",
  cancelled: "border-slate-300 bg-slate-100 text-slate-600",
  draft: "border-slate-300 bg-slate-100 text-slate-600",
};

export default function OrdersWorkspace({
  appointmentId,
}: {
  appointmentId: number;
}) {
  /*
    THIS CONSULTATION'S diagnoses, fetched here rather than passed down.

    The indication picker needs them, and the server refuses any diagnosis from
    another consultation -- including the same patient's from an earlier visit.
    Reading them directly keeps the Diagnosis and Orders sections independent:
    neither has to be mounted for the other to work, and there is no shared
    array for the two to disagree about.
  */
  const [diagnoses, setDiagnoses] = useState<DoctorDiagnosis[]>([]);
  const [kind, setKind] = useState<string>("laboratory");

  const [orders, setOrders] = useState<DoctorLabOrder[]>([]);
  const [canOrder, setCanOrder] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [placing, setPlacing] = useState(false);
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  const [confirmCancelId, setConfirmCancelId] = useState<number | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<LabTestOption[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<LabTestOption[]>([]);
  const [form, setForm] = useState<LabOrderForm>(EMPTY_ORDER_FORM);

  const applyResponse = useCallback((data: DoctorLabOrderResponse) => {
    setOrders(data.orders);
    setCanOrder(data.can_order);
  }, []);

  /* ---------------- load ---------------- */
  useEffect(() => {
    const controller = new AbortController();

    async function load() {
      setLoading(true);
      setLoadError(null);
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/orders/laboratory`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorLabOrderResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success) {
          setLoadError(
            messageFromPayload(payload, "Unable to load laboratory orders."),
          );
          return;
        }
        applyResponse(payload.data);
      } catch {
        if (!controller.signal.aborted) {
          setLoadError("Unable to reach the laboratory service.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void load();
    return () => controller.abort();
  }, [appointmentId, applyResponse]);

  /* ---------------- this consultation's diagnoses ---------------- */
  useEffect(() => {
    const controller = new AbortController();

    async function loadDiagnoses() {
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/diagnoses`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorDiagnosisResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setDiagnoses(payload.data.diagnoses);
        }
      } catch {
        /* the indication picker simply offers none; ordering is unaffected */
      }
    }

    void loadDiagnoses();
    return () => controller.abort();
  }, [appointmentId]);

  /* ---------------- catalogue search ---------------- */
  useEffect(() => {
    const term = query.trim();
    // Below the threshold there is nothing to fetch AND nothing to clear:
    // `visibleResults` derives emptiness during render, so this effect never
    // writes state just to blank the list.
    if (term.length < 2) return;

    const controller = new AbortController();
    // Debounced: a clinician types faster than a round trip, and a query per
    // keystroke would queue responses that arrive out of order.
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        const response = await fetch(
          `/api/doctor/catalogue/laboratory-tests?q=${encodeURIComponent(term)}`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<LabCatalogueResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setResults(payload.data.tests);
          setTruncated(payload.data.truncated);
        }
      } catch {
        /* the picker shows nothing; ordering is unaffected */
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, 220);

    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [query]);

  /* ---------------- mutations ---------------- */
  const place = useCallback(async () => {
    if (!selected.length) return;
    setPlacing(true);
    setActionError(null);
    const token =
      globalThis.crypto?.randomUUID?.() ??
      `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    try {
      const response = await fetch(
        `/api/doctor/visits/${appointmentId}/orders/laboratory`,
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildOrderPayload(selected, form, token)),
        },
      );
      const payload =
        (await response.json()) as ApiEnvelope<DoctorLabOrderResponse>;
      if (!response.ok || !payload.success) {
        setActionError(
          messageFromPayload(payload, "The laboratory order could not be placed."),
        );
        return;
      }
      applyResponse(payload.data);
      setSelected([]);
      setForm(EMPTY_ORDER_FORM);
      setQuery("");
      setResults([]);
    } catch {
      setActionError("Unable to reach the laboratory service.");
    } finally {
      setPlacing(false);
    }
  }, [appointmentId, applyResponse, form, selected]);

  const cancel = useCallback(
    async (order: DoctorLabOrder) => {
      setCancellingId(order.id);
      setActionError(null);
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/orders/laboratory/${order.id}/cancel`,
          {
            method: "POST",
            cache: "no-store",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorLabOrderResponse>;
        if (!response.ok || !payload.success) {
          // Odoo's own sentence: it names which gate refused, which no message
          // invented here could do.
          setActionError(
            messageFromPayload(payload, "The order could not be cancelled."),
          );
          return;
        }
        applyResponse(payload.data);
        setConfirmCancelId(null);
      } catch {
        setActionError("Unable to reach the laboratory service.");
      } finally {
        setCancellingId(null);
      }
    },
    [appointmentId, applyResponse],
  );

  const searchTerm = query.trim();
  const visibleResults = searchTerm.length >= 2 ? results : [];
  const submittable = useMemo(
    () => canSubmitOrder(selected, placing),
    [selected, placing],
  );

  return (
    <div className="flex flex-col gap-3">
      {/* ---- Order kind sub-navigation ---- */}
      <div className="flex items-center gap-1 border-b border-slate-200">
        {ORDER_KINDS.map((entry) =>
          entry.live ? (
            <button
              key={entry.key}
              type="button"
              aria-current={kind === entry.key ? "page" : undefined}
              onClick={() => setKind(entry.key)}
              className={`-mb-px border-b-2 px-2 py-1.5 text-[10.5px] font-bold uppercase tracking-[0.07em] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 ${
                kind === entry.key
                  ? "border-emerald-600 text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              {entry.label}
            </button>
          ) : (
            <span
              key={entry.key}
              title="Arrives in a later clinical slice"
              className="cursor-default border-b-2 border-transparent px-2 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.07em] text-slate-400"
            >
              {entry.label}
            </span>
          ),
        )}
      </div>

      {loadError ? (
        <p
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] leading-snug text-amber-900"
        >
          {loadError}
        </p>
      ) : null}

      {actionError ? (
        <p
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-[11px] leading-snug text-red-900"
        >
          {actionError}
        </p>
      ) : null}

      {loading && orders.length === 0 ? (
        <p className="py-8 text-center text-xs text-slate-500">
          Loading laboratory orders…
        </p>
      ) : (
        <>
          {/* ---- Place an order ---- */}
          {canOrder ? (
            <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-2">
              <div className="flex items-center gap-2">
                <h3 className="text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500">
                  New laboratory order
                </h3>
                <span aria-hidden className="h-px flex-1 bg-slate-200" />
                {searching ? (
                  <span className="text-[9px] text-slate-400">Searching…</span>
                ) : null}
              </div>

              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search laboratory tests by name or code…"
                className="h-8 w-full rounded border border-slate-300 bg-white px-2.5 text-[12px] text-slate-900 outline-none placeholder:text-slate-400 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
              />

              {searchTerm.length >= 2 && visibleResults.length === 0 && !searching ? (
                <p className="text-[11px] text-slate-500">No matching test.</p>
              ) : null}

              {visibleResults.length > 0 ? (
                <ul className="max-h-44 overflow-y-auto rounded border border-slate-200 bg-white">
                  {visibleResults.map((test) => {
                    const already = isSelected(selected, test.id);
                    return (
                      <li key={test.id}>
                        <button
                          type="button"
                          disabled={already}
                          onClick={() => setSelected((s) => addTest(s, test))}
                          className="flex w-full items-baseline gap-2 border-b border-slate-100 px-2.5 py-1.5 text-left outline-none last:border-b-0 hover:bg-emerald-50/70 focus-visible:bg-emerald-50 disabled:cursor-not-allowed disabled:bg-slate-50"
                        >
                          <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-slate-800">
                            {test.name}
                          </span>
                          {test.code ? (
                            <span className="shrink-0 font-mono text-[10px] text-slate-500">
                              {test.code}
                            </span>
                          ) : null}
                          <span className="shrink-0 text-[10px] font-bold text-emerald-700">
                            {already ? "Added" : "+"}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : null}

              {truncated && visibleResults.length > 0 ? (
                <p className="text-[9px] text-slate-400">
                  Showing the first matches only. Refine your search to narrow it.
                </p>
              ) : null}

              {selected.length > 0 ? (
                <>
                  <ul className="flex flex-wrap gap-1.5">
                    {selected.map((test) => (
                      <li
                        key={test.id}
                        className="inline-flex items-center gap-1.5 rounded border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-900"
                      >
                        {testLabel(test)}
                        <button
                          type="button"
                          aria-label={`Remove ${test.name}`}
                          onClick={() =>
                            setSelected((s) => removeTest(s, test.id))
                          }
                          className="text-[11px] font-bold text-emerald-700 outline-none hover:text-emerald-900 focus-visible:ring-1 focus-visible:ring-emerald-600"
                        >
                          ×
                        </button>
                      </li>
                    ))}
                  </ul>

                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    <label className="flex min-w-0 flex-col gap-0.5">
                      <span className="text-[9px] font-bold uppercase tracking-[0.07em] text-slate-500">
                        Priority
                      </span>
                      <select
                        value={form.priority}
                        onChange={(event) =>
                          setForm((f) => ({
                            ...f,
                            priority: event.target
                              .value as LabOrderForm["priority"],
                          }))
                        }
                        className="h-7 rounded border border-slate-300 bg-white px-1.5 text-[11.5px] font-semibold text-slate-800 outline-none focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
                      >
                        {LAB_PRIORITIES.map((priority) => (
                          <option key={priority} value={priority}>
                            {labPriorityLabel(priority)}
                          </option>
                        ))}
                      </select>
                    </label>

                    <label className="flex min-w-0 flex-col gap-0.5">
                      <span className="text-[9px] font-bold uppercase tracking-[0.07em] text-slate-500">
                        Indication (diagnosis)
                      </span>
                      <select
                        value={form.diagnosis_id ?? ""}
                        onChange={(event) =>
                          setForm((f) => ({
                            ...f,
                            diagnosis_id: event.target.value
                              ? Number(event.target.value)
                              : null,
                          }))
                        }
                        className="h-7 rounded border border-slate-300 bg-white px-1.5 text-[11.5px] font-semibold text-slate-800 outline-none focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
                      >
                        <option value="">— none —</option>
                        {/* Only THIS consultation's diagnoses. The server
                            refuses any other, including the same patient's
                            diagnosis from an earlier visit. */}
                        {diagnoses.map((diagnosis) => (
                          <option key={diagnosis.id} value={diagnosis.id}>
                            {diagnosis.disease?.name ?? "Diagnosis"}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <textarea
                    value={form.clinical_notes}
                    rows={2}
                    placeholder="Clinical indication (optional)…"
                    onChange={(event) =>
                      setForm((f) => ({ ...f, clinical_notes: event.target.value }))
                    }
                    className="w-full resize-y rounded border border-slate-300 bg-white px-2 py-1.5 text-[12px] leading-relaxed text-slate-900 caret-emerald-700 outline-none placeholder:text-slate-400 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
                  />

                  <div className="flex justify-end">
                    <button
                      type="button"
                      disabled={!submittable}
                      onClick={() => void place()}
                      className="h-8 rounded-md bg-emerald-700 px-4 text-[11px] font-bold uppercase tracking-[0.06em] text-white shadow-sm outline-none hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:bg-slate-200 disabled:text-slate-500"
                    >
                      {placing ? "Placing…" : "Place lab order"}
                    </button>
                  </div>
                </>
              ) : null}
            </div>
          ) : (
            <p className="rounded-md border border-slate-300 bg-white px-3 py-2 text-[11px] leading-snug text-slate-700">
              This consultation is completed. No new laboratory orders can be
              placed.
            </p>
          )}

          {/* ---- Current orders ---- */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <h3 className="text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500">
                Laboratory orders
              </h3>
              <span aria-hidden className="h-px flex-1 bg-slate-200" />
            </div>

            {orders.length === 0 ? (
              <p className="text-[12px] text-slate-500">
                No laboratory orders for this consultation.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {orders.map((order) => {
                  const busy = cancellingId === order.id;
                  return (
                    <li
                      key={order.id}
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-2"
                    >
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="shrink-0 font-mono text-[11px] font-bold text-slate-700">
                          {order.request_code}
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-slate-900">
                          {orderTestSummary(order)}
                        </span>
                        {order.priority && order.priority !== "routine" ? (
                          <span className="shrink-0 rounded border border-amber-300 bg-amber-50 px-1.5 py-px text-[9px] font-bold uppercase tracking-wide text-amber-900">
                            {labPriorityLabel(order.priority)}
                          </span>
                        ) : null}
                        {/* The SERVER's label. Statuses are derived from real
                            backend workflow state, never invented here. */}
                        <span
                          className={`shrink-0 rounded border px-1.5 py-px text-[9px] font-bold uppercase tracking-wide ${
                            STATUS_TONE[order.status] ?? STATUS_TONE.draft
                          }`}
                        >
                          {order.status_label}
                        </span>

                        {order.cancellable ? (
                          confirmCancelId === order.id ? (
                            <span className="flex shrink-0 items-center gap-1">
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => void cancel(order)}
                                className="rounded border border-red-400 bg-red-50 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-red-800 outline-none hover:bg-red-100 focus-visible:ring-2 focus-visible:ring-red-600 disabled:opacity-60"
                              >
                                {busy ? "Cancelling…" : "Confirm"}
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmCancelId(null)}
                                className="rounded border border-slate-300 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-600 outline-none hover:bg-slate-50"
                              >
                                Keep
                              </button>
                            </span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setConfirmCancelId(order.id)}
                              className="shrink-0 rounded border border-slate-300 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-600 outline-none hover:border-red-300 hover:bg-red-50 hover:text-red-800 focus-visible:ring-2 focus-visible:ring-red-600"
                            >
                              Cancel
                            </button>
                          )
                        ) : null}
                      </div>

                      {order.diagnosis || order.clinical_indication ? (
                        <p className="mt-1 text-[11px] leading-relaxed text-slate-600">
                          {order.diagnosis ? (
                            <span className="font-semibold text-slate-700">
                              {order.diagnosis.name}
                              {order.clinical_indication ? " · " : ""}
                            </span>
                          ) : null}
                          {order.clinical_indication ? (
                            <span className="whitespace-pre-wrap">
                              {order.clinical_indication}
                            </span>
                          ) : null}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}

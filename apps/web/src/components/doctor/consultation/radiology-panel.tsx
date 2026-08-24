"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  EMPTY_ORDER_FORM,
  addExam,
  buildOrderPayload,
  canSubmitOrder,
  examContext,
  examLabel,
  isSelected,
  orderExamSummary,
  orderNeedsContrast,
  radPriorityLabel,
  removeExam,
  selectionNeedsContrast,
} from "@/lib/radiology-format";
import type { ApiEnvelope } from "@/types/doctor";
import type { DoctorDiagnosis } from "@/types/doctor-diagnosis";
import type {
  DoctorRadOrder,
  DoctorRadOrderResponse,
  RadCatalogueResponse,
  RadExamOption,
  RadOrderForm,
} from "@/types/doctor-radiology";
import { RAD_PRIORITIES } from "@/types/doctor-radiology";

/**
 * The RADIOLOGY tab of the ORDERS section.
 *
 * A SIBLING OF LaboratoryPanel, in the same dense clinical style, differing
 * only where radiology genuinely differs.
 *
 * THE DESK PLACES A CLINICAL ORDER; IT NEVER CREATES A CHARGE. Submitting runs
 * Odoo's own action_confirm_request(), where hospital_billing validates every
 * exam's billing configuration and raises one charge per study,
 * all-or-nothing. Nothing here knows what a scan costs, and the payload carries
 * no money.
 *
 * THE PICKER ONLY EVER OFFERS ORDERABLE STUDIES. The server filters the
 * catalogue to exams whose billing configuration would survive confirmation, so
 * a doctor is never shown a study that pressing Place Order would refuse. That
 * is not cosmetic: most of the shipped radiology catalogue is unmapped.
 *
 * NO REPORT IS SHOWN HERE. A completed study says "Result available" and
 * nothing more; reading the report is a later slice with its own screen.
 */

const STATUS_TONE: Record<string, string> = {
  awaiting_clearance: "border-amber-300 bg-amber-50 text-amber-900",
  awaiting_scheduling: "border-sky-300 bg-sky-50 text-sky-900",
  scheduled: "border-sky-300 bg-sky-50 text-sky-900",
  in_progress: "border-sky-400 bg-sky-50 text-sky-900",
  result_available: "border-emerald-400 bg-emerald-50 text-emerald-900",
  cancelled: "border-slate-300 bg-slate-100 text-slate-600",
  draft: "border-slate-300 bg-slate-100 text-slate-600",
};

export default function RadiologyPanel({
  appointmentId,
  diagnoses,
}: {
  appointmentId: number;
  diagnoses: DoctorDiagnosis[];
}) {
  const [orders, setOrders] = useState<DoctorRadOrder[]>([]);
  const [canOrder, setCanOrder] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [placing, setPlacing] = useState(false);
  const [cancellingId, setCancellingId] = useState<number | null>(null);
  const [confirmCancelId, setConfirmCancelId] = useState<number | null>(null);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<RadExamOption[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<RadExamOption[]>([]);
  const [form, setForm] = useState<RadOrderForm>(EMPTY_ORDER_FORM);

  const applyResponse = useCallback((data: DoctorRadOrderResponse) => {
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
          `/api/doctor/visits/${appointmentId}/orders/radiology`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorRadOrderResponse>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success) {
          setLoadError(
            messageFromPayload(payload, "Unable to load radiology orders."),
          );
          return;
        }
        applyResponse(payload.data);
      } catch {
        if (!controller.signal.aborted) {
          setLoadError("Unable to reach the radiology service.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    void load();
    return () => controller.abort();
  }, [appointmentId, applyResponse]);

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
          `/api/doctor/catalogue/radiology-exams?q=${encodeURIComponent(term)}`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<RadCatalogueResponse>;
        if (controller.signal.aborted) return;
        if (response.ok && payload.success) {
          setResults(payload.data.exams);
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
        `/api/doctor/visits/${appointmentId}/orders/radiology`,
        {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildOrderPayload(selected, form, token)),
        },
      );
      const payload =
        (await response.json()) as ApiEnvelope<DoctorRadOrderResponse>;
      if (!response.ok || !payload.success) {
        setActionError(
          messageFromPayload(payload, "The radiology order could not be placed."),
        );
        return;
      }
      applyResponse(payload.data);
      setSelected([]);
      setForm(EMPTY_ORDER_FORM);
      setQuery("");
      setResults([]);
    } catch {
      setActionError("Unable to reach the radiology service.");
    } finally {
      setPlacing(false);
    }
  }, [appointmentId, applyResponse, form, selected]);

  const cancel = useCallback(
    async (order: DoctorRadOrder) => {
      setCancellingId(order.id);
      setActionError(null);
      try {
        const response = await fetch(
          `/api/doctor/visits/${appointmentId}/orders/radiology/${order.id}/cancel`,
          {
            method: "POST",
            cache: "no-store",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          },
        );
        const payload =
          (await response.json()) as ApiEnvelope<DoctorRadOrderResponse>;
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
        setActionError("Unable to reach the radiology service.");
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
  const contrastPending = useMemo(
    () => selectionNeedsContrast(selected),
    [selected],
  );

  return (
    <div className="flex flex-col gap-3">
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
          Loading radiology orders…
        </p>
      ) : (
        <>
          {/* ---- Place an order ---- */}
          {canOrder ? (
            <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-2">
              <div className="flex items-center gap-2">
                <h3 className="text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500">
                  New radiology order
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
                placeholder="Search studies by name, code or body part…"
                className="h-8 w-full rounded border border-slate-300 bg-white px-2.5 text-[12px] text-slate-900 outline-none placeholder:text-slate-400 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
              />

              {searchTerm.length >= 2 && visibleResults.length === 0 && !searching ? (
                <p className="text-[11px] text-slate-500">No matching study.</p>
              ) : null}

              {visibleResults.length > 0 ? (
                <ul className="max-h-44 overflow-y-auto rounded border border-slate-200 bg-white">
                  {visibleResults.map((exam) => {
                    const already = isSelected(selected, exam.id);
                    const context = examContext(exam);
                    return (
                      <li key={exam.id}>
                        <button
                          type="button"
                          disabled={already}
                          onClick={() => setSelected((s) => addExam(s, exam))}
                          className="flex w-full items-baseline gap-2 border-b border-slate-100 px-2.5 py-1.5 text-left outline-none last:border-b-0 hover:bg-emerald-50/70 focus-visible:bg-emerald-50 disabled:cursor-not-allowed disabled:bg-slate-50"
                        >
                          <span className="min-w-0 flex-1 truncate text-[12px] font-semibold text-slate-800">
                            {exam.name}
                          </span>
                          {context ? (
                            <span className="shrink-0 text-[10px] text-slate-500">
                              {context}
                            </span>
                          ) : null}
                          {/* Contrast is patient preparation, so it is visible
                              at the moment of choosing, not only after. */}
                          {exam.contrast_required ? (
                            <span className="shrink-0 rounded border border-violet-300 bg-violet-50 px-1 py-px text-[8.5px] font-bold uppercase tracking-wide text-violet-900">
                              Contrast
                            </span>
                          ) : null}
                          {exam.code ? (
                            <span className="shrink-0 font-mono text-[10px] text-slate-500">
                              {exam.code}
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
                    {selected.map((exam) => (
                      <li
                        key={exam.id}
                        className="inline-flex items-center gap-1.5 rounded border border-emerald-300 bg-emerald-50 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-900"
                      >
                        {examLabel(exam)}
                        <button
                          type="button"
                          aria-label={`Remove ${exam.name}`}
                          onClick={() =>
                            setSelected((s) => removeExam(s, exam.id))
                          }
                          className="text-[11px] font-bold text-emerald-700 outline-none hover:text-emerald-900 focus-visible:ring-1 focus-visible:ring-emerald-600"
                        >
                          ×
                        </button>
                      </li>
                    ))}
                  </ul>

                  {contrastPending ? (
                    <p className="rounded border border-violet-300 bg-violet-50 px-2 py-1 text-[10.5px] leading-snug text-violet-900">
                      A selected study requires contrast. Confirm the patient&apos;s
                      preparation and allergy history before ordering.
                    </p>
                  ) : null}

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
                              .value as RadOrderForm["priority"],
                          }))
                        }
                        className="h-7 rounded border border-slate-300 bg-white px-1.5 text-[11.5px] font-semibold text-slate-800 outline-none focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
                      >
                        {RAD_PRIORITIES.map((priority) => (
                          <option key={priority} value={priority}>
                            {radPriorityLabel(priority)}
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
                    value={form.clinical_indication}
                    rows={2}
                    placeholder="Clinical indication (optional)…"
                    onChange={(event) =>
                      setForm((f) => ({
                        ...f,
                        clinical_indication: event.target.value,
                      }))
                    }
                    className="w-full resize-y rounded border border-slate-300 bg-white px-2 py-1.5 text-[12px] leading-relaxed text-slate-900 caret-emerald-700 outline-none placeholder:text-slate-400 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
                  />

                  {/* Radiology has a second free-text field laboratory does
                      not: preparation the department needs before the patient
                      arrives. Kept separate from the indication rather than
                      merged, because they are read by different people. */}
                  <textarea
                    value={form.instructions}
                    rows={2}
                    placeholder="Preparation / instructions for the imaging department (optional)…"
                    onChange={(event) =>
                      setForm((f) => ({ ...f, instructions: event.target.value }))
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
                      {placing ? "Placing…" : "Place radiology order"}
                    </button>
                  </div>
                </>
              ) : null}
            </div>
          ) : (
            <p className="rounded-md border border-slate-300 bg-white px-3 py-2 text-[11px] leading-snug text-slate-700">
              This consultation is completed. No new radiology orders can be
              placed.
            </p>
          )}

          {/* ---- Current orders ---- */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <h3 className="text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500">
                Radiology orders
              </h3>
              <span aria-hidden className="h-px flex-1 bg-slate-200" />
            </div>

            {orders.length === 0 ? (
              <p className="text-[12px] text-slate-500">
                No radiology orders for this consultation.
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
                          {orderExamSummary(order)}
                        </span>
                        {orderNeedsContrast(order) ? (
                          <span className="shrink-0 rounded border border-violet-300 bg-violet-50 px-1.5 py-px text-[9px] font-bold uppercase tracking-wide text-violet-900">
                            Contrast
                          </span>
                        ) : null}
                        {order.priority && order.priority !== "routine" ? (
                          <span className="shrink-0 rounded border border-amber-300 bg-amber-50 px-1.5 py-px text-[9px] font-bold uppercase tracking-wide text-amber-900">
                            {radPriorityLabel(order.priority)}
                          </span>
                        ) : null}
                        {/* The SERVER's label. Statuses are derived from real
                            backend workflow state AND hospital_billing's own
                            clearance verdict, never invented here. */}
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

                      {order.instructions ? (
                        <p className="mt-0.5 text-[11px] leading-relaxed text-slate-500">
                          <span className="font-semibold uppercase tracking-wide text-[9px] text-slate-400">
                            Prep{" "}
                          </span>
                          <span className="whitespace-pre-wrap">
                            {order.instructions}
                          </span>
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

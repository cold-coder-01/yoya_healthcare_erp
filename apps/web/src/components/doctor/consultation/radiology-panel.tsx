"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import type { RadOrderDraft } from "@/lib/order-draft-format";
import {
  addExam,
  buildOrderPayload,
  canSubmitOrder,
  examContext,
  examCountLabel,
  isSelected,
  orderExamSummary,
  orderNeedsContrast,
  radPriorityLabel,
  selectionNeedsContrast,
  selectionSummary,
} from "@/lib/radiology-format";
import type { ApiEnvelope } from "@/types/doctor";
import type { DoctorDiagnosis } from "@/types/doctor-diagnosis";
import type {
  DoctorRadOrder,
  DoctorRadOrderResponse,
  RadCatalogueResponse,
  RadExamOption,
} from "@/types/doctor-radiology";

import { useRadOrderDraft } from "./order-draft-context";
import RadiologyOrderModal from "./radiology-order-modal";

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
 *
 * THE UNSENT ORDER IS NOT THIS COMPONENT'S TO LOSE. Selecting a study opens a
 * centred editor and Save commits the request to the CONSULTATION's draft
 * store, above the ORDERS tabs, so moving to Medication and back returns the
 * half-written request exactly as it was left.
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
  /*
    NULL UNTIL THE SERVER HAS SAID. "Not yet known" is a third state that
    matters: a false starting value would read as "this consultation is
    completed" for the duration of every load, and the read-only rule below
    would discard the doctor's draft on every tab switch.
  */
  const [canOrder, setCanOrder] = useState<boolean | null>(null);
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

  /* The unsent request, owned by the consultation rather than by this panel. */
  const { draft, setDraft, discard } = useRadOrderDraft();
  const [editor, setEditor] = useState<{
    entry: RadOrderDraft;
    mode: "add" | "edit";
    returnFocus: HTMLElement;
  } | null>(null);

  /*
    THE SERVER'S VERDICT, APPLIED WHERE IT ARRIVES.

    A COMPLETED CONSULTATION HAS NOTHING LEFT TO COMPOSE, so the same response
    that says so closes the editor and discards the unsent request. Not an
    effect watching `canOrder`: that would cascade a render, and a rule keyed on
    "canOrder is not true" would also fire while a load was in flight -- which
    is every tab switch -- and destroy the draft on each one.

    This is one of the four things allowed to clear a draft. Tab navigation is
    not among them and never reaches here.
  */
  const applyResponse = useCallback(
    (data: DoctorRadOrderResponse) => {
      setOrders(data.orders);
      setCanOrder(data.can_order);
      if (!data.can_order) {
        setEditor(null);
        discard();
      }
    },
    [discard],
  );

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
    if (!draft.exams.length) return;
    setPlacing(true);
    setActionError(null);
    /*
      MINTED HERE, PER ATTEMPT, exactly as it always has been. The token is
      deliberately NOT kept with the draft: radiology has never retained one
      between attempts, and changing that would change this endpoint's
      idempotency behaviour rather than merely move where an unsent form is
      held.
    */
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
          body: JSON.stringify(buildOrderPayload(draft.exams, draft.form, token)),
        },
      );
      const payload =
        (await response.json()) as ApiEnvelope<DoctorRadOrderResponse>;
      if (!response.ok || !payload.success) {
        // The draft is KEPT: a refused order is work the doctor still has.
        setActionError(
          messageFromPayload(payload, "The radiology order could not be placed."),
        );
        return;
      }
      applyResponse(payload.data);
      // Definitive success, and the ONLY place this panel discards a draft on
      // the doctor's behalf.
      discard();
      setQuery("");
      setResults([]);
    } catch {
      setActionError("Unable to reach the radiology service.");
    } finally {
      setPlacing(false);
    }
  }, [appointmentId, applyResponse, discard, draft]);

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
    () => canSubmitOrder(draft.exams, placing),
    [draft.exams, placing],
  );
  const contrastPending = useMemo(
    () => selectionNeedsContrast(draft.exams),
    [draft.exams],
  );

  /* Selecting a study opens the editor on the request it would join. */
  const openWithExam = useCallback(
    (exam: RadExamOption, returnFocus: HTMLElement) => {
      setEditor({
        entry: { ...draft, exams: addExam(draft.exams, exam) },
        mode: "add",
        returnFocus,
      });
    },
    [draft],
  );

  const openStagedOrder = useCallback(
    (returnFocus: HTMLElement) => {
      setEditor({ entry: draft, mode: "edit", returnFocus });
    },
    [draft],
  );

  const saveEditor = useCallback(
    (entry: RadOrderDraft) => {
      setDraft(entry);
      setEditor(null);
    },
    [setDraft],
  );

  /* The staged row's second line: how much, how urgent, and against what. */
  const stagedContext = useMemo(() => {
    const diagnosis = draft.form.diagnosis_id
      ? (diagnoses.find((entry) => entry.id === draft.form.diagnosis_id)?.disease
          ?.name ?? null)
      : null;
    return [
      examCountLabel(draft.exams.length),
      radPriorityLabel(draft.form.priority),
      diagnosis,
    ]
      .filter((part): part is string => Boolean(part))
      .join(" · ");
  }, [diagnoses, draft.form.diagnosis_id, draft.form.priority, draft.exams.length]);

  return (
    <div className="flex flex-col gap-3">
      {editor && canOrder !== false ? (
        <RadiologyOrderModal
          entry={editor.entry}
          mode={editor.mode}
          diagnoses={diagnoses}
          readOnly={canOrder !== true}
          returnFocus={editor.returnFocus}
          onSave={saveEditor}
          onClose={() => setEditor(null)}
        />
      ) : null}

      {loadError ? (
        <p
          role="alert"
          className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 cl-secondary leading-snug text-amber-900"
        >
          {loadError}
        </p>
      ) : null}

      {actionError ? (
        <p
          role="alert"
          className="rounded-md border border-red-300 bg-red-50 px-3 py-2 cl-secondary leading-snug text-red-900"
        >
          {actionError}
        </p>
      ) : null}

      {loading && orders.length === 0 ? (
        <p className="py-8 text-center cl-body text-slate-500">
          Loading radiology orders…
        </p>
      ) : (
        <>
          {/* ---- Place an order ---- */}
          {canOrder === true ? (
            <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-2">
              <div className="flex items-center gap-2">
                <h3 className="cl-meta font-bold uppercase tracking-[0.08em] text-slate-600">
                  New radiology order
                </h3>
                <span aria-hidden className="h-px flex-1 bg-slate-200" />
                {searching ? (
                  <span className="cl-micro text-slate-400">Searching…</span>
                ) : null}
              </div>

              <input
                type="search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                aria-label="Search radiology studies"
                placeholder="Search studies by name, code or body part…"
                className="h-9 w-full rounded border border-slate-300 bg-white px-2.5 cl-body text-slate-900 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
              />

              {searchTerm.length >= 2 && visibleResults.length === 0 && !searching ? (
                <p className="cl-secondary text-slate-500">No matching study.</p>
              ) : null}

              {visibleResults.length > 0 ? (
                <ul className="max-h-44 overflow-y-auto rounded border border-slate-200 bg-white">
                  {visibleResults.map((exam) => {
                    const already = isSelected(draft.exams, exam.id);
                    const context = examContext(exam);
                    return (
                      <li key={exam.id}>
                        <button
                          type="button"
                          disabled={already}
                          onClick={(event) =>
                            openWithExam(exam, event.currentTarget)
                          }
                          className="flex w-full items-baseline gap-2 border-b border-slate-100 px-2.5 py-1.5 text-left outline-none last:border-b-0 hover:bg-emerald-50/70 focus-visible:bg-emerald-50 disabled:cursor-not-allowed disabled:bg-slate-50"
                        >
                          <span className="min-w-0 flex-1 truncate cl-body font-semibold text-slate-900">
                            {exam.name}
                          </span>
                          {context ? (
                            <span className="shrink-0 cl-meta text-slate-500">
                              {context}
                            </span>
                          ) : null}
                          {/* Contrast is patient preparation, so it is visible
                              at the moment of choosing, not only after. */}
                          {exam.contrast_required ? (
                            <span className="shrink-0 rounded border border-violet-300 bg-violet-50 px-1 py-px cl-micro font-bold uppercase tracking-wide text-violet-900">
                              Contrast
                            </span>
                          ) : null}
                          {exam.code ? (
                            <span className="shrink-0 font-mono cl-meta text-slate-500">
                              {exam.code}
                            </span>
                          ) : null}
                          <span className="shrink-0 cl-meta font-bold text-emerald-700">
                            {already ? "Added" : "+"}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : null}

              {truncated && visibleResults.length > 0 ? (
                <p className="cl-micro text-slate-400">
                  Showing the first matches only. Refine your search to narrow it.
                </p>
              ) : null}

              {/* ---- The staged request ---- */}
              {draft.exams.length > 0 ? (
                <>
                  {contrastPending ? (
                    <p className="rounded border border-violet-300 bg-violet-50 px-2 py-1 cl-meta leading-snug text-violet-900">
                      A selected study requires contrast. Confirm the patient&apos;s
                      preparation and allergy history before ordering.
                    </p>
                  ) : null}

                  <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
                    <div className="flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="truncate cl-strong font-semibold text-slate-900">
                          {selectionSummary(draft.exams)}
                        </p>
                        <p className="truncate cl-secondary text-slate-700">
                          {stagedContext}
                        </p>
                        {draft.form.clinical_indication.trim() ||
                        draft.form.instructions.trim() ? (
                          <p
                            title={
                              draft.form.clinical_indication.trim() ||
                              draft.form.instructions
                            }
                            className="truncate cl-secondary text-slate-500"
                          >
                            {draft.form.clinical_indication.trim() ||
                              draft.form.instructions}
                          </p>
                        ) : null}
                      </div>
                      <button
                        type="button"
                        aria-label="Edit the radiology order"
                        onClick={(event) => openStagedOrder(event.currentTarget)}
                        className="shrink-0 rounded border border-sky-300 bg-sky-50 px-2 py-0.5 cl-secondary font-semibold text-sky-800 outline-none hover:bg-sky-100 focus-visible:ring-2 focus-visible:ring-sky-600"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        aria-label="Remove the radiology order"
                        onClick={discard}
                        className="shrink-0 rounded border border-red-200 px-2 py-0.5 cl-secondary font-semibold text-red-700 outline-none hover:border-red-300 hover:bg-red-50 focus-visible:ring-2 focus-visible:ring-red-600"
                      >
                        Remove
                      </button>
                    </div>
                  </div>

                  <div className="flex items-center justify-end gap-2">
                    <span className="cl-meta text-slate-500">
                      {examCountLabel(draft.exams.length)} · one request
                    </span>
                    <button
                      type="button"
                      disabled={!submittable}
                      onClick={() => void place()}
                      className="h-9 rounded-md bg-emerald-700 px-4 cl-body font-semibold text-white shadow-sm outline-none hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:bg-slate-300 disabled:text-slate-600"
                    >
                      {placing ? "Placing…" : "Place radiology order"}
                    </button>
                  </div>
                </>
              ) : null}
            </div>
          ) : canOrder === false ? (
            <div className="rounded-md border border-red-200 bg-red-50/60 px-3 py-2">
              <div className="flex items-center gap-2">
                <span className="rounded border border-red-300 bg-white px-1.5 py-px cl-micro font-bold uppercase tracking-wide text-red-800">
                  Read only
                </span>
                <p className="cl-secondary leading-snug text-red-900">
                  This consultation is completed. Radiology orders can be
                  reviewed but no new order can be placed.
                </p>
              </div>
            </div>
          ) : null}

          {/* ---- Current orders ---- */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <h3 className="cl-meta font-bold uppercase tracking-[0.08em] text-slate-600">
                Radiology orders
              </h3>
              <span aria-hidden className="h-px flex-1 bg-slate-200" />
            </div>

            {orders.length === 0 ? (
              <p className="cl-body text-slate-600">
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
                        <span className="shrink-0 font-mono cl-meta font-semibold text-slate-500">
                          {order.request_code}
                        </span>
                        <span className="min-w-0 flex-1 truncate cl-strong font-semibold leading-snug text-slate-900">
                          {orderExamSummary(order)}
                        </span>
                        {orderNeedsContrast(order) ? (
                          <span className="shrink-0 rounded border border-violet-300 bg-violet-50 px-1.5 py-px cl-meta font-semibold uppercase tracking-wide text-violet-900">
                            Contrast
                          </span>
                        ) : null}
                        {order.priority && order.priority !== "routine" ? (
                          <span className="shrink-0 rounded border border-amber-300 bg-amber-50 px-1.5 py-px cl-meta font-semibold uppercase tracking-wide text-amber-900">
                            {radPriorityLabel(order.priority)}
                          </span>
                        ) : null}
                        {/* The SERVER's label. Statuses are derived from real
                            backend workflow state AND hospital_billing's own
                            clearance verdict, never invented here. */}
                        <span
                          className={`shrink-0 rounded border px-1.5 py-px cl-meta font-semibold uppercase tracking-wide ${
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
                                className="rounded border border-red-400 bg-red-50 px-2 py-0.5 cl-meta font-semibold text-red-800 outline-none hover:bg-red-100 focus-visible:ring-2 focus-visible:ring-red-600 disabled:opacity-60"
                              >
                                {busy ? "Cancelling…" : "Confirm"}
                              </button>
                              <button
                                type="button"
                                onClick={() => setConfirmCancelId(null)}
                                className="rounded border border-slate-300 px-2 py-0.5 cl-meta font-semibold text-slate-600 outline-none hover:bg-slate-50"
                              >
                                Keep
                              </button>
                            </span>
                          ) : (
                            <button
                              type="button"
                              onClick={() => setConfirmCancelId(order.id)}
                              className="shrink-0 rounded border border-slate-300 px-2 py-0.5 cl-meta font-semibold text-slate-600 outline-none hover:border-red-300 hover:bg-red-50 hover:text-red-800 focus-visible:ring-2 focus-visible:ring-red-600"
                            >
                              Cancel
                            </button>
                          )
                        ) : null}
                      </div>

                      {order.diagnosis || order.clinical_indication ? (
                        <p className="mt-1 cl-secondary leading-[1.55] text-slate-700">
                          {order.diagnosis ? (
                            <span className="font-semibold text-slate-900">
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
                        <p className="mt-0.5 cl-secondary leading-[1.55] text-slate-600">
                          <span className="font-bold uppercase tracking-wide cl-micro text-slate-500">
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

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  addTest,
  buildOrderPayload,
  canSubmitOrder,
  isSelected,
  labPriorityLabel,
  orderTestSummary,
  selectionSummary,
  testCountLabel,
} from "@/lib/laboratory-format";
import type { ApiEnvelope } from "@/types/doctor";
import type { DoctorDiagnosis } from "@/types/doctor-diagnosis";
import type {
  DoctorLabOrder,
  DoctorLabOrderResponse,
  LabCatalogueResponse,
  LabTestOption,
} from "@/types/doctor-laboratory";
import type { LabOrderDraft } from "@/lib/order-draft-format";

import { useLabOrderDraft } from "./order-draft-context";
import LaboratoryOrderModal from "./laboratory-order-modal";

/**
 * The LABORATORY tab of the ORDERS section.
 *
 * Moved out of orders-workspace unchanged when radiology went live in Slice 5.
 * The two order kinds are siblings, not variants: they carry different fields,
 * different status vocabularies and different workflows, and a single
 * parameterised panel would have needed a union type at every line of JSX.
 *
 * THE DESK PLACES A CLINICAL ORDER; IT NEVER CREATES A CHARGE. Submitting runs
 * Odoo's own action_confirm_request(), where hospital_billing validates every
 * test's billing configuration and raises one charge per test, all-or-nothing.
 * Nothing here knows what a test costs, and the payload carries no money.
 *
 * NOTHING IS SENT UNTIL "PLACE LAB ORDER". Searching, selecting and composing
 * are local; the one request that leaves this component is the submission
 * itself, and it carries an idempotency token so a double click cannot bill the
 * patient twice.
 *
 * THE UNSENT ORDER IS NOT THIS COMPONENT'S TO LOSE. Selecting a test opens a
 * centred editor and Save commits the request to the CONSULTATION's draft
 * store, which lives above the ORDERS tabs. Switching to Radiology and back
 * unmounts this panel and re-reads the placed orders -- a queue another
 * department is working really can move while the doctor is away -- but the
 * half-written request comes back exactly as it was left.
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

export default function LaboratoryPanel({
  appointmentId,
  diagnoses,
}: {
  appointmentId: number;
  diagnoses: DoctorDiagnosis[];
}) {
  const [orders, setOrders] = useState<DoctorLabOrder[]>([]);
  /*
    NULL UNTIL THE SERVER HAS SAID. Ordering permission is the server's verdict,
    and "not yet known" is a third state that matters: a false starting value
    would read as "this consultation is completed" for the duration of every
    load, and the read-only rule below would discard the doctor's draft on every
    tab switch -- the exact loss this slice exists to stop.
  */
  const [canOrder, setCanOrder] = useState<boolean | null>(null);
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

  /* The unsent request, owned by the consultation rather than by this panel. */
  const { draft, setDraft, discard } = useLabOrderDraft();
  const [editor, setEditor] = useState<{
    entry: LabOrderDraft;
    mode: "add" | "edit";
    returnFocus: HTMLElement;
  } | null>(null);

  /*
    THE SERVER'S VERDICT, APPLIED WHERE IT ARRIVES.

    A COMPLETED CONSULTATION HAS NOTHING LEFT TO COMPOSE, so the same response
    that says so closes the editor and discards the unsent request. It is done
    here rather than in an effect watching `canOrder` for two reasons: an effect
    would fire a cascading render, and -- the one that matters clinically -- the
    verdict is only ever known from a response. A rule keyed on "canOrder is not
    true" would also fire while a load was still in flight, which is every tab
    switch, and would destroy the draft on each one.

    This is one of the four things allowed to clear a draft. Tab navigation is
    not among them and never reaches here.
  */
  const applyResponse = useCallback(
    (data: DoctorLabOrderResponse) => {
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
    if (!draft.tests.length) return;
    setPlacing(true);
    setActionError(null);
    /*
      MINTED HERE, PER ATTEMPT, exactly as it always has been. The token is
      deliberately NOT kept with the draft: laboratory has never retained one
      between attempts, and changing that would change this endpoint's
      idempotency behaviour rather than merely move where an unsent form is
      held.
    */
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
          body: JSON.stringify(buildOrderPayload(draft.tests, draft.form, token)),
        },
      );
      const payload =
        (await response.json()) as ApiEnvelope<DoctorLabOrderResponse>;
      if (!response.ok || !payload.success) {
        // The draft is KEPT: a refused order is work the doctor still has, and
        // clearing it here would make a failed submission indistinguishable
        // from a successful one.
        setActionError(
          messageFromPayload(payload, "The laboratory order could not be placed."),
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
      setActionError("Unable to reach the laboratory service.");
    } finally {
      setPlacing(false);
    }
  }, [appointmentId, applyResponse, discard, draft]);

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
    () => canSubmitOrder(draft.tests, placing),
    [draft.tests, placing],
  );

  /* Selecting a test opens the editor on the request it would join. */
  const openWithTest = useCallback(
    (test: LabTestOption, returnFocus: HTMLElement) => {
      setEditor({
        entry: { ...draft, tests: addTest(draft.tests, test) },
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
    (entry: LabOrderDraft) => {
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
      testCountLabel(draft.tests.length),
      labPriorityLabel(draft.form.priority),
      diagnosis,
    ]
      .filter((part): part is string => Boolean(part))
      .join(" · ");
  }, [diagnoses, draft.form.diagnosis_id, draft.form.priority, draft.tests.length]);

  return (
    <div className="flex flex-col gap-3">
      {editor && canOrder !== false ? (
        <LaboratoryOrderModal
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
          Loading laboratory orders…
        </p>
      ) : (
        <>
          {/* ---- Place an order ---- */}
          {canOrder === true ? (
            <div className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50/60 px-2.5 py-2">
              <div className="flex items-center gap-2">
                <h3 className="cl-meta font-bold uppercase tracking-[0.08em] text-slate-600">
                  New laboratory order
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
                aria-label="Search laboratory tests"
                placeholder="Search laboratory tests by name or code…"
                className="h-9 w-full rounded border border-slate-300 bg-white px-2.5 cl-body text-slate-900 outline-none placeholder:text-slate-500 focus-visible:border-emerald-600 focus-visible:ring-1 focus-visible:ring-emerald-600"
              />

              {searchTerm.length >= 2 && visibleResults.length === 0 && !searching ? (
                <p className="cl-secondary text-slate-500">No matching test.</p>
              ) : null}

              {visibleResults.length > 0 ? (
                <ul className="max-h-44 overflow-y-auto rounded border border-slate-200 bg-white">
                  {visibleResults.map((test) => {
                    const already = isSelected(draft.tests, test.id);
                    return (
                      <li key={test.id}>
                        <button
                          type="button"
                          disabled={already}
                          onClick={(event) =>
                            openWithTest(test, event.currentTarget)
                          }
                          className="flex w-full items-baseline gap-2 border-b border-slate-100 px-2.5 py-1.5 text-left outline-none last:border-b-0 hover:bg-emerald-50/70 focus-visible:bg-emerald-50 disabled:cursor-not-allowed disabled:bg-slate-50"
                        >
                          <span className="min-w-0 flex-1 truncate cl-body font-semibold text-slate-900">
                            {test.name}
                          </span>
                          {test.code ? (
                            <span className="shrink-0 font-mono cl-meta text-slate-500">
                              {test.code}
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
              {draft.tests.length > 0 ? (
                <>
                  <div className="rounded-md border border-slate-200 bg-white px-3 py-2">
                    <div className="flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="truncate cl-strong font-semibold text-slate-900">
                          {selectionSummary(draft.tests)}
                        </p>
                        <p className="truncate cl-secondary text-slate-700">
                          {stagedContext}
                        </p>
                        {draft.form.clinical_notes.trim() ? (
                          <p
                            title={draft.form.clinical_notes}
                            className="truncate cl-secondary text-slate-500"
                          >
                            {draft.form.clinical_notes}
                          </p>
                        ) : null}
                      </div>
                      <button
                        type="button"
                        aria-label="Edit the laboratory order"
                        onClick={(event) => openStagedOrder(event.currentTarget)}
                        className="shrink-0 rounded border border-sky-300 bg-sky-50 px-2 py-0.5 cl-secondary font-semibold text-sky-800 outline-none hover:bg-sky-100 focus-visible:ring-2 focus-visible:ring-sky-600"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        aria-label="Remove the laboratory order"
                        onClick={discard}
                        className="shrink-0 rounded border border-red-200 px-2 py-0.5 cl-secondary font-semibold text-red-700 outline-none hover:border-red-300 hover:bg-red-50 focus-visible:ring-2 focus-visible:ring-red-600"
                      >
                        Remove
                      </button>
                    </div>
                  </div>

                  <div className="flex items-center justify-end gap-2">
                    <span className="cl-meta text-slate-500">
                      {testCountLabel(draft.tests.length)} · one request
                    </span>
                    <button
                      type="button"
                      disabled={!submittable}
                      onClick={() => void place()}
                      className="h-9 rounded-md bg-emerald-700 px-4 cl-body font-semibold text-white shadow-sm outline-none hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 disabled:bg-slate-300 disabled:text-slate-600"
                    >
                      {placing ? "Placing…" : "Place lab order"}
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
                  This consultation is completed. Laboratory orders can be
                  reviewed but no new order can be placed.
                </p>
              </div>
            </div>
          ) : null}

          {/* ---- Current orders ---- */}
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <h3 className="cl-meta font-bold uppercase tracking-[0.08em] text-slate-600">
                Laboratory orders
              </h3>
              <span aria-hidden className="h-px flex-1 bg-slate-200" />
            </div>

            {orders.length === 0 ? (
              <p className="cl-body text-slate-600">
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
                        <span className="shrink-0 font-mono cl-meta font-semibold text-slate-500">
                          {order.request_code}
                        </span>
                        <span className="min-w-0 flex-1 truncate cl-strong font-semibold leading-snug text-slate-900">
                          {orderTestSummary(order)}
                        </span>
                        {order.priority && order.priority !== "routine" ? (
                          <span className="shrink-0 rounded border border-amber-300 bg-amber-50 px-1.5 py-px cl-meta font-semibold uppercase tracking-wide text-amber-900">
                            {labPriorityLabel(order.priority)}
                          </span>
                        ) : null}
                        {/* The SERVER's label. Statuses are derived from real
                            backend workflow state, never invented here. */}
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

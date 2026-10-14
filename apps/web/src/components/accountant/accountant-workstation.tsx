"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  ACCOUNTANT_EMPTY_STATE,
  ACCOUNTANT_TABS,
  DEFAULT_REFUND_REASON,
  accountantAdmissionStatus,
  accountantLaneLabel,
  accountantLaneTone,
  accountingStatusTone,
  locationText,
  paymentKindLabel,
  refundMethodLabel,
  refundPreview,
  refundReferenceRequired,
  type AccountantTab,
} from "@/lib/accountant-format";
import { messageFromPayload } from "@/lib/api-error";
import { money } from "@/lib/cashier-format";
import { formatHospitalDateTime } from "@/lib/clinical-format";
import type { AccountantDetail, AccountantRow, AccountantWorklist } from "@/types/accountant";
import type { ApiEnvelope } from "@/types/reception";

import AccountantRefundReviewDialog from "./accountant-refund-review-dialog";

/**
 * THE ACCOUNTANT DESK: inpatient refunds and settlement review.
 *
 * Queue (left) -> the selected account's settlement (centre) -> the
 * authorized actions (right): the stay's accounting status, Post accounting,
 * Post legacy accounting and Record refund. Every figure and every verdict is
 * the server's; the most a refund may be is `refund.max_amount`, the refund
 * METHODS are the server's list, and the browser never names an account or a
 * journal. There is NO payment intake, estimate, admission, transfer,
 * discharge, bed or clinical control here.
 *
 * Every money act carries ONE operation token per intended act: an unknown
 * outcome keeps it, so a retry replays on the server instead of posting or
 * refunding twice. After any answer the account and the queue are re-read.
 */
export default function AccountantWorkstation() {
  const [tab, setTab] = useState<AccountantTab>("refund_due");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [worklist, setWorklist] = useState<AccountantWorklist | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [queueLoading, setQueueLoading] = useState(true);
  const [queueToken, setQueueToken] = useState(0);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<AccountantDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailToken, setDetailToken] = useState(0);

  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState(DEFAULT_REFUND_REASON);
  const [method, setMethod] = useState("");
  const [reference, setReference] = useState("");
  const [legacyMethod, setLegacyMethod] = useState("");
  const [legacyReference, setLegacyReference] = useState("");
  const [legacyDate, setLegacyDate] = useState("");
  const [posting, setPosting] = useState(false);
  const [postError, setPostError] = useState<string | null>(null);
  const pendingPost = useRef<{ signature: string; token: string } | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refundError, setRefundError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const pending = useRef<{ signature: string; token: string } | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  // --- queue -------------------------------------------------------
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setQueueLoading(true);
      const params = new URLSearchParams({ lane: tab });
      if (debouncedSearch) params.set("q", debouncedSearch);
      try {
        const response = await fetch(`/api/accountant/worklist?${params.toString()}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<AccountantWorklist>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success || !payload.data) {
          setWorklist(null);
          setQueueError(messageFromPayload(payload, "Unable to load the accountant queue."));
          return;
        }
        setQueueError(null);
        setWorklist(payload.data);
      } catch {
        if (!controller.signal.aborted) setQueueError("Unable to reach the YOYA EMR gateway.");
      } finally {
        if (!controller.signal.aborted) setQueueLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [tab, debouncedSearch, queueToken]);

  const applyDetail = useCallback((next: AccountantDetail) => {
    setDetail(next);
    // Default: the server's whole refundable balance.
    setAmount(next.refund.may_record ? next.refund.max_amount.toFixed(2) : "");
  }, []);

  // --- detail ------------------------------------------------------
  useEffect(() => {
    if (selectedId === null) return;
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(`/api/accountant/admissions/${selectedId}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<AccountantDetail>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success || !payload.data) {
          setDetail(null);
          setDetailError(messageFromPayload(payload, "Unable to load the selected account."));
          return;
        }
        setDetailError(null);
        applyDetail(payload.data);
      } catch {
        if (!controller.signal.aborted) setDetailError("Unable to reach the YOYA EMR gateway.");
      }
    }
    void load();
    return () => controller.abort();
  }, [selectedId, detailToken, applyDetail]);

  const select = useCallback((row: AccountantRow) => {
    setSelectedId(row.admission.id);
    setReviewing(false);
    setRefundError(null);
    setNotice(null);
    setReason(DEFAULT_REFUND_REASON);
    setMethod("");
    setReference("");
    setLegacyMethod("");
    setLegacyReference("");
    setLegacyDate("");
    setPostError(null);
    pending.current = null;
    pendingPost.current = null;
  }, []);

  const preview = useMemo(
    () => refundPreview(detail?.refund.max_amount ?? 0, amount),
    [detail, amount],
  );
  const methods = detail?.accounting.refund_methods ?? [];
  const referenceRequired = Boolean(method) && refundReferenceRequired(methods, method);
  const canReview = Boolean(
    detail?.refund.may_record && preview.valid && reason.trim() && method &&
      (!referenceRequired || reference.trim()),
  );
  const legacyReferenceRequired = Boolean(legacyMethod) && refundReferenceRequired(methods, legacyMethod);
  const canPostLegacy = Boolean(
    detail?.accounting.may_post_legacy && legacyMethod && (!legacyReferenceRequired || legacyReference.trim()),
  );

  /** Post accounting / Post legacy accounting: one token per intended post. */
  async function postAccounting(kind: "post-accounting" | "post-legacy-accounting") {
    if (!detail || posting) return;
    const body: Record<string, string> =
      kind === "post-accounting"
        ? {}
        : {
            payment_method: legacyMethod,
            ...(legacyReference.trim() ? { payment_reference: legacyReference.trim() } : {}),
            ...(legacyDate ? { accounting_date: legacyDate } : {}),
          };
    const signature = `${kind}|${detail.admission.id}|${JSON.stringify(body)}`;
    if (pendingPost.current?.signature !== signature) {
      pendingPost.current = { signature, token: crypto.randomUUID() };
    }
    setPosting(true);
    setPostError(null);
    try {
      const response = await fetch(`/api/accountant/admissions/${detail.admission.id}/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({ ...body, idempotency_key: pendingPost.current.token }),
      });
      const payload = (await response.json()) as ApiEnvelope<AccountantDetail>;
      if (!response.ok || !payload.success || !payload.data) {
        if (response.status < 500) pendingPost.current = null;
        setPostError(messageFromPayload(payload, "Unable to post accounting."));
        setDetailToken((token) => token + 1);
        setQueueToken((token) => token + 1);
        return;
      }
      pendingPost.current = null;
      applyDetail(payload.data);
      setNotice(
        payload.data.replayed
          ? "This accounting was already posted."
          : `Accounting posted${payload.data.posted_reference ? ` (${payload.data.posted_reference})` : ""}.`,
      );
      setQueueToken((token) => token + 1);
    } catch {
      setPostError("The posting outcome is unknown. Retry to finish the same request safely.");
    } finally {
      setPosting(false);
    }
  }

  async function recordRefund() {
    if (!detail || !canReview || busy) return;
    const signature = `${detail.admission.id}|${preview.refund.toFixed(2)}|${reason.trim()}|${method}|${reference.trim()}`;
    if (pending.current?.signature !== signature) {
      pending.current = { signature, token: crypto.randomUUID() };
    }
    setBusy(true);
    setRefundError(null);
    try {
      const response = await fetch(`/api/accountant/admissions/${detail.admission.id}/refund`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        cache: "no-store",
        body: JSON.stringify({
          amount: preview.refund,
          reason: reason.trim(),
          payment_method: method,
          ...(reference.trim() ? { payment_reference: reference.trim() } : {}),
          idempotency_key: pending.current.token,
        }),
      });
      const payload = (await response.json()) as ApiEnvelope<AccountantDetail>;
      if (!response.ok || !payload.success || !payload.data) {
        // A definite refusal: the next attempt is a new request. Re-read the
        // account -- the most likely cause is that the balance moved.
        if (response.status < 500) pending.current = null;
        setRefundError(messageFromPayload(payload, "Unable to record the refund."));
        setDetailToken((token) => token + 1);
        setQueueToken((token) => token + 1);
        return;
      }
      pending.current = null;
      applyDetail(payload.data);
      setReviewing(false);
      setNotice(
        payload.data.replayed
          ? "This refund was already recorded."
          : "Refund recorded. Accounting posted.",
      );
      setQueueToken((token) => token + 1);
    } catch {
      // Unknown outcome: keep the token so a retry replays, never refunds twice.
      setRefundError("The refund outcome is unknown. Retry to finish the same request safely.");
    } finally {
      setBusy(false);
    }
  }

  const rows = worklist?.rows ?? [];
  const currency = (value: number, code: string | null | undefined) =>
    `${code ? `${code} ` : ""}${money(value)}`;

  return (
    <div className="grid h-[calc(100vh-4.25rem)] min-h-0 grid-cols-1 gap-3 lg:grid-cols-[320px_minmax(0,1fr)_300px]">
      {/* ---- Queue ---- */}
      <section aria-label="Accountant queue" className="flex min-h-0 flex-col rounded-md border border-slate-200 bg-white">
        <div className="shrink-0 border-b border-slate-200 p-2">
          <h1 className="text-[11px] font-bold uppercase tracking-wide text-slate-600">Accountant Desk</h1>
          <div role="tablist" aria-label="Accountant lanes" className="mt-1.5 flex flex-wrap gap-1">
            {ACCOUNTANT_TABS.map((item) => {
              const count =
                item.key === "all"
                  ? Object.values(worklist?.counts ?? {}).reduce((sum, value) => sum + value, 0)
                  : worklist?.counts?.[item.key] ?? 0;
              return (
                <button
                  key={item.key}
                  type="button"
                  role="tab"
                  aria-selected={tab === item.key}
                  onClick={() => setTab(item.key)}
                  className={`rounded border px-2 py-0.5 text-[11px] font-semibold ${
                    tab === item.key
                      ? "border-slate-800 bg-slate-800 text-white"
                      : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                  }`}
                >
                  {item.label} <span className="font-mono tabular-nums">{count}</span>
                </button>
              );
            })}
          </div>
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Patient, MRN, encounter, admission"
            aria-label="Search the accountant queue"
            className="mt-2 h-8 w-full rounded-md border border-slate-300 px-2 text-xs outline-none focus:border-sky-600"
          />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {queueError ? (
            <p className="m-2 rounded border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-700">{queueError}</p>
          ) : queueLoading && !worklist ? (
            <p className="m-2 text-xs text-slate-500">Loading…</p>
          ) : rows.length === 0 ? (
            <p className="m-3 text-xs text-slate-500">{ACCOUNTANT_EMPTY_STATE}</p>
          ) : (
            <ul>
              {rows.map((row) => (
                <li key={row.admission.id}>
                  <button
                    type="button"
                    onClick={() => select(row)}
                    aria-current={selectedId === row.admission.id ? "true" : undefined}
                    className={`block w-full border-b border-slate-100 px-2.5 py-2 text-left hover:bg-slate-50 ${
                      selectedId === row.admission.id ? "bg-sky-50" : ""
                    }`}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-slate-900">{row.patient.name}</span>
                      <span className={`shrink-0 rounded border px-1.5 py-px text-[10px] font-bold uppercase tracking-wide ${accountantLaneTone(row.lane)}`}>
                        {accountantLaneLabel(row.lane)}
                      </span>
                    </div>
                    <div className="mt-0.5 truncate font-mono text-[11px] text-slate-600">
                      {row.patient.identification_code ?? "—"} · {row.encounter.name} · {row.admission.name}
                    </div>
                    <div className="mt-0.5 flex items-baseline justify-between gap-2 text-[11px]">
                      <span className="text-slate-500">{accountantAdmissionStatus(row.admission)}</span>
                      <span className="font-mono font-bold tabular-nums text-slate-900">
                        {row.lane === "refunded"
                          ? currency(row.refunded_total, row.currency)
                          : currency(row.refundable_balance, row.currency)}
                      </span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {worklist?.truncated ? (
            <p className="m-2 text-[11px] text-amber-800">More accounts exist than shown. Narrow with search.</p>
          ) : null}
        </div>
      </section>

      {/* ---- Settlement detail ---- */}
      <section aria-label="Settlement detail" className="min-h-0 overflow-y-auto rounded-md border border-slate-200 bg-white">
        {detailError ? (
          <p className="m-3 rounded border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-700">{detailError}</p>
        ) : !detail ? (
          <p className="m-4 text-sm text-slate-500">Select an account from the queue.</p>
        ) : (
          <AccountantSettlementDetail detail={detail} />
        )}
      </section>

      {/* ---- Authorized action ---- */}
      <aside aria-label="Refund action" className="min-h-0 overflow-y-auto rounded-md border border-slate-200 bg-white p-3">
        {!detail ? (
          <p className="text-xs text-slate-500">The authorized action appears here.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {notice ? (
              <p role="status" className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1.5 text-xs text-emerald-900">
                {notice}
              </p>
            ) : null}
            <AccountingPanel
              detail={detail}
              posting={posting}
              error={postError}
              legacyMethod={legacyMethod}
              legacyReference={legacyReference}
              legacyDate={legacyDate}
              legacyReferenceRequired={legacyReferenceRequired}
              canPostLegacy={canPostLegacy}
              onLegacyMethod={setLegacyMethod}
              onLegacyReference={setLegacyReference}
              onLegacyDate={setLegacyDate}
              onPost={() => void postAccounting("post-accounting")}
              onPostLegacy={() => void postAccounting("post-legacy-accounting")}
            />
            <h2 className="mt-1 text-[11px] font-bold uppercase tracking-wide text-slate-600">Record refund</h2>
            {detail.refund.may_record ? (
              <>
                <div className="rounded border border-amber-300 bg-amber-50 px-2 py-1.5">
                  <p className="text-[10px] font-bold uppercase tracking-wide text-amber-900">Refund due</p>
                  <p className="font-mono text-lg font-bold tabular-nums text-amber-900">
                    {currency(detail.refund.max_amount, detail.currency)}
                  </p>
                </div>
                <label htmlFor="accountant-refund-amount" className="text-xs font-medium text-slate-700">
                  Refund amount
                </label>
                <input
                  id="accountant-refund-amount"
                  type="number"
                  inputMode="decimal"
                  step="0.01"
                  min="0.01"
                  max={detail.refund.max_amount}
                  value={amount}
                  onChange={(event) => setAmount(event.target.value)}
                  className="h-10 w-full rounded-md border border-slate-300 px-3 text-right font-mono text-base font-bold tabular-nums outline-none focus:border-amber-600"
                />
                {preview.exceeds ? (
                  <p className="text-[11px] font-medium text-red-700">More than the refundable balance.</p>
                ) : null}
                <label htmlFor="accountant-refund-method" className="text-xs font-medium text-slate-700">
                  Refund method <span className="text-red-600">*</span>
                </label>
                <select
                  id="accountant-refund-method"
                  value={method}
                  onChange={(event) => setMethod(event.target.value)}
                  className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm outline-none focus:border-amber-600"
                >
                  <option value="">Select method…</option>
                  {methods.map((option) => (
                    <option key={option.key} value={option.key}>{option.label}</option>
                  ))}
                </select>
                {referenceRequired ? (
                  <>
                    <label htmlFor="accountant-refund-reference" className="text-xs font-medium text-slate-700">
                      Payment reference <span className="text-red-600">*</span>
                    </label>
                    <input
                      id="accountant-refund-reference"
                      type="text"
                      maxLength={128}
                      value={reference}
                      onChange={(event) => setReference(event.target.value)}
                      className="h-9 w-full rounded-md border border-slate-300 px-2 text-sm outline-none focus:border-amber-600"
                    />
                  </>
                ) : null}
                <label htmlFor="accountant-refund-reason" className="text-xs font-medium text-slate-700">
                  Reason <span className="text-red-600">*</span>
                </label>
                <textarea
                  id="accountant-refund-reason"
                  rows={3}
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm outline-none focus:border-amber-600"
                />
                {refundError && !reviewing ? (
                  <p className="rounded border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-700">{refundError}</p>
                ) : null}
                <button
                  type="button"
                  disabled={!canReview || busy}
                  onClick={() => {
                    setRefundError(null);
                    setReviewing(true);
                  }}
                  className="h-10 w-full rounded-md bg-amber-700 text-sm font-semibold text-white hover:bg-amber-800 disabled:cursor-not-allowed disabled:bg-slate-300"
                >
                  Record refund
                </button>
                <p className="text-[11px] text-slate-500">
                  Returns the patient&apos;s own unapplied money. Delivered care, the admission, the
                  visit and the bed do not change.
                </p>
              </>
            ) : (
              <div className={`rounded border px-2 py-1.5 text-xs ${accountantLaneTone(detail.lane)}`}>
                <p className="text-[10px] font-bold uppercase tracking-wide">
                  {detail.lane === "refunded" ? "Financially resolved" : accountantLaneLabel(detail.lane)}
                </p>
                <p className="mt-0.5">{detail.refund.reason}</p>
              </div>
            )}
          </div>
        )}
      </aside>

      {reviewing && detail ? (
        <AccountantRefundReviewDialog
          account={detail}
          amount={preview.refund}
          after={preview.after}
          reason={reason.trim()}
          methodLabel={refundMethodLabel(methods, method)}
          reference={reference.trim() || null}
          busy={busy}
          error={refundError}
          onConfirm={() => void recordRefund()}
          onClose={() => setReviewing(false)}
        />
      ) : null}
    </div>
  );
}

function AccountantSettlementDetail({ detail }: { detail: AccountantDetail }) {
  const s = detail.settlement;
  const code = detail.currency ? `${detail.currency} ` : "";
  const figure = (value: number) => `${code}${money(value)}`;
  const admission = detail.admission;

  return (
    <div className="flex flex-col">
      <header className="flex flex-wrap items-start justify-between gap-2 border-b border-slate-200 px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-base font-bold text-slate-950">{detail.patient.name}</h2>
          <p className="font-mono text-[11px] text-slate-600">
            MRN {detail.patient.identification_code ?? "—"} · {detail.encounter.name} · {admission.name}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-1">
          <span className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-700">
            {accountantAdmissionStatus(admission)}
          </span>
          <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${accountantLaneTone(detail.lane)}`}>
            {detail.lane === "refunded" ? "Refunded" : accountantLaneLabel(detail.lane)}
          </span>
        </div>
      </header>

      <Band title="Admission">
        <Grid>
          <Field label="Status">{accountantAdmissionStatus(admission)}</Field>
          <Field label="Ward / room / bed">{locationText(detail.location)}</Field>
          <Field label="Admitted">{formatHospitalDateTime(admission.admission_date, "—")}</Field>
          <Field label="Discharged">{formatHospitalDateTime(admission.discharge_date, "—")}</Field>
          <Field label="Responsible physician">{admission.physician ?? "—"}</Field>
          <Field label="Encounter">{detail.encounter.name}</Field>
        </Grid>
      </Band>

      <Band title="Financial settlement">
        <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-0.5 text-xs">
          <Money label="Estimated amount" value={figure(s.estimate_amount)} muted />
          <Money label="Advance received" value={figure(s.funds.advance)} />
          <Money label="Other patient payments" value={figure(s.funds.other_payments)} />
          <Money label="Settlement payments" value={figure(s.funds.settlement_payments)} />
          <Money label="Total patient funds" value={figure(s.funds.total)} strong />
          <Money label="Actual delivered care" value={figure(s.actual_delivered)} />
          <Money label="Payer share" value={figure(s.payer_authorized)} />
          <Money label="Patient responsibility" value={figure(s.patient_responsibility)} strong />
          <Money label="Advance applied" value={figure(s.advance_applied)} />
          <Money label="Remaining due" value={figure(s.remaining_due)} />
          <Money label="Refundable balance" value={figure(s.refundable_balance)} strong tone={s.refundable_balance > 0 ? "amber" : undefined} />
          <dt className="text-slate-500">Financial state</dt>
          <dd className="text-right font-semibold text-slate-900">{s.financial_state}</dd>
        </dl>
        {s.review_reasons.length ? (
          <ul className="mt-1.5 list-disc rounded border border-red-200 bg-red-50 py-1 pl-5 pr-2 text-xs text-red-800">
            {s.review_reasons.map((item) => (
              <li key={item.code}>{item.message}</li>
            ))}
          </ul>
        ) : null}
      </Band>

      <Band title="Delivered care">
        {s.delivered_by_category.length ? (
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-0.5 text-xs">
            {s.delivered_by_category.map((item) => (
              <Money key={item.key} label={item.label} value={figure(item.amount)} />
            ))}
            <Money label="Total delivered" value={figure(s.actual_delivered)} strong />
          </dl>
        ) : (
          <p className="text-xs text-slate-500">No care delivered.</p>
        )}
      </Band>

      <Band title="Payment in">
        {detail.payments_in.length ? (
          <HistoryTable>
            {detail.payments_in.map((row) => (
              <tr key={`in-${row.id}`} className="border-t border-slate-100">
                <td className="py-1 pr-2 font-mono">{row.reference}</td>
                <td className="py-1 pr-2">{paymentKindLabel(row.kind)}</td>
                <td className="py-1 pr-2">{row.payment_method ?? "—"}</td>
                <td className="py-1 pr-2">{row.actor ?? "—"}</td>
                <td className="py-1 pr-2 whitespace-nowrap">{formatHospitalDateTime(row.at, "—")}</td>
                <td className="py-1 pr-2">{row.state}</td>
                <td className="py-1 text-right font-mono font-semibold tabular-nums text-emerald-800">+ {figure(row.amount)}</td>
              </tr>
            ))}
          </HistoryTable>
        ) : (
          <p className="text-xs text-slate-500">No payments received.</p>
        )}
      </Band>

      <Band title="Refund out">
        {detail.refunds_out.length ? (
          <HistoryTable>
            {detail.refunds_out.map((row) => (
              <tr key={`out-${row.id}`} className="border-t border-slate-100">
                <td className="py-1 pr-2 font-mono">{row.reference}</td>
                <td className="py-1 pr-2">Refund{row.reason ? ` · ${row.reason}` : ""}</td>
                <td className="py-1 pr-2">
                  {refundMethodLabel(detail.accounting.refund_methods, row.payment_method)}
                  {row.payment_reference ? <span className="block font-mono text-[10px]">{row.payment_reference}</span> : null}
                </td>
                <td className="py-1 pr-2">{row.actor ?? "—"}</td>
                <td className="py-1 pr-2 whitespace-nowrap">{formatHospitalDateTime(row.at, "—")}</td>
                <td className="py-1 pr-2">
                  Operational refund recorded
                  <span className={`block text-[10px] ${row.accounting_posted ? "text-emerald-800" : "text-amber-800"}`}>
                    {row.accounting_posted
                      ? `Accounting posted${row.accounting_reference ? ` · ${row.accounting_reference}` : ""}`
                      : "Legacy accounting pending"}
                  </span>
                </td>
                <td className="py-1 text-right font-mono font-semibold tabular-nums text-amber-900">− {figure(row.amount)}</td>
              </tr>
            ))}
          </HistoryTable>
        ) : (
          <p className="text-xs text-slate-500">No refunds recorded.</p>
        )}
      </Band>
    </div>
  );
}

/**
 * The stay's ACCOUNTING: the server's status, its reference, and the two
 * posting actions it allows. A status, not a ledger: no journal lines.
 */
function AccountingPanel({
  detail,
  posting,
  error,
  legacyMethod,
  legacyReference,
  legacyDate,
  legacyReferenceRequired,
  canPostLegacy,
  onLegacyMethod,
  onLegacyReference,
  onLegacyDate,
  onPost,
  onPostLegacy,
}: {
  detail: AccountantDetail;
  posting: boolean;
  error: string | null;
  legacyMethod: string;
  legacyReference: string;
  legacyDate: string;
  legacyReferenceRequired: boolean;
  canPostLegacy: boolean;
  onLegacyMethod: (value: string) => void;
  onLegacyReference: (value: string) => void;
  onLegacyDate: (value: string) => void;
  onPost: () => void;
  onPostLegacy: () => void;
}) {
  const accounting = detail.accounting;
  return (
    <section aria-label="Accounting" className="flex flex-col gap-1.5">
      <h2 className="text-[11px] font-bold uppercase tracking-wide text-slate-600">Accounting</h2>
      <div className={`rounded border px-2 py-1.5 text-xs ${accountingStatusTone(accounting.status)}`}>
        <p className="text-[10px] font-bold uppercase tracking-wide" data-testid="accounting-status">
          {accounting.label}
        </p>
        {accounting.reference ? (
          <p className="mt-0.5 font-mono text-[11px]">
            {accounting.reference}
            {accounting.posted_at ? ` · ${formatHospitalDateTime(accounting.posted_at, "")}` : ""}
          </p>
        ) : null}
        {accounting.status === "legacy_pending" ? (
          <p className="mt-0.5">
            Refunds recorded before refund accounting: {money(accounting.legacy_refund_total)}
          </p>
        ) : null}
        {accounting.reason && !accounting.may_post && !accounting.may_post_legacy ? (
          <p className="mt-0.5">{accounting.reason}</p>
        ) : null}
      </div>
      {error ? (
        <p className="rounded border border-red-200 bg-red-50 px-2 py-1.5 text-xs text-red-700">{error}</p>
      ) : null}
      {accounting.may_post ? (
        <button
          type="button"
          disabled={posting}
          onClick={onPost}
          className="h-9 w-full rounded-md bg-sky-700 text-sm font-semibold text-white hover:bg-sky-800 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {posting ? "Posting…" : "Post accounting"}
        </button>
      ) : null}
      {accounting.may_post_legacy ? (
        <div className="flex flex-col gap-1.5 rounded border border-amber-200 p-2">
          <p className="text-[11px] text-slate-600">
            State how the earlier refund was paid. No method is assumed.
          </p>
          <label htmlFor="accountant-legacy-method" className="text-xs font-medium text-slate-700">
            Refund method <span className="text-red-600">*</span>
          </label>
          <select
            id="accountant-legacy-method"
            value={legacyMethod}
            onChange={(event) => onLegacyMethod(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 bg-white px-2 text-sm outline-none focus:border-amber-600"
          >
            <option value="">Select method…</option>
            {accounting.refund_methods.map((option) => (
              <option key={option.key} value={option.key}>{option.label}</option>
            ))}
          </select>
          {legacyReferenceRequired ? (
            <>
              <label htmlFor="accountant-legacy-reference" className="text-xs font-medium text-slate-700">
                Payment reference <span className="text-red-600">*</span>
              </label>
              <input
                id="accountant-legacy-reference"
                type="text"
                maxLength={128}
                value={legacyReference}
                onChange={(event) => onLegacyReference(event.target.value)}
                className="h-9 w-full rounded-md border border-slate-300 px-2 text-sm outline-none focus:border-amber-600"
              />
            </>
          ) : null}
          <label htmlFor="accountant-legacy-date" className="text-xs font-medium text-slate-700">
            Accounting date (optional)
          </label>
          <input
            id="accountant-legacy-date"
            type="date"
            value={legacyDate}
            onChange={(event) => onLegacyDate(event.target.value)}
            className="h-9 w-full rounded-md border border-slate-300 px-2 text-sm outline-none focus:border-amber-600"
          />
          <button
            type="button"
            disabled={!canPostLegacy || posting}
            onClick={onPostLegacy}
            className="h-9 w-full rounded-md bg-amber-700 text-sm font-semibold text-white hover:bg-amber-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {posting ? "Posting…" : "Post legacy accounting"}
          </button>
        </div>
      ) : null}
    </section>
  );
}

function Band({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-slate-200 px-3 py-2 last:border-b-0">
      <h3 className="mb-1 text-[11px] font-bold uppercase tracking-wide text-slate-600">{title}</h3>
      {children}
    </section>
  );
}

function Grid({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-2 gap-x-3 gap-y-1 min-[900px]:grid-cols-3">{children}</dl>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="text-[10px] font-bold uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="truncate text-xs text-slate-900">{children}</dd>
    </div>
  );
}

function Money({
  label,
  value,
  strong = false,
  muted = false,
  tone,
}: {
  label: string;
  value: string;
  strong?: boolean;
  muted?: boolean;
  tone?: "amber";
}) {
  const color = tone === "amber" ? "text-amber-900" : muted ? "text-slate-500" : "text-slate-900";
  return (
    <>
      <dt className={`${muted ? "text-slate-400" : "text-slate-500"} ${strong ? "font-semibold text-slate-700" : ""}`}>{label}</dt>
      <dd className={`text-right font-mono tabular-nums ${color} ${strong ? "font-bold" : ""}`}>{value}</dd>
    </>
  );
}

function HistoryTable({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-[11px] text-slate-700">
        <thead className="text-[10px] uppercase tracking-wide text-slate-500">
          <tr>
            <th className="py-1 pr-2 font-semibold">Reference</th>
            <th className="py-1 pr-2 font-semibold">Type</th>
            <th className="py-1 pr-2 font-semibold">Method</th>
            <th className="py-1 pr-2 font-semibold">By</th>
            <th className="py-1 pr-2 font-semibold">When</th>
            <th className="py-1 pr-2 font-semibold">Status</th>
            <th className="py-1 text-right font-semibold">Amount</th>
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

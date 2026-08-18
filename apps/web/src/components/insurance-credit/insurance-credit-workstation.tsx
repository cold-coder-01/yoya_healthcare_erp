"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { hospitalToday } from "@/lib/clinical-format";
import { money, newIdempotencyKey } from "@/lib/cashier-format";
import type {
  ApiEnvelope,
  AuthorizationDecision,
  OfficerCapabilities,
  OfficerChargeRow,
  OfficerVisitDetail,
  OfficerWorklist,
  OfficerWorklistRow,
} from "@/types/insurance-credit";

/**
 * The Insurance/Credit workstation.
 *
 * THE RULE THIS IS BUILT AROUND: the officer proposes, the server decides.
 * Every permitted figure shown here was computed when the page rendered and
 * may be stale; the server re-evaluates under a lock and REFUSES rather than
 * silently capping, so a refusal is answered with a refetch, never a retry.
 *
 * No arithmetic lives here. Sponsor eligibility, residuals and remaining
 * benefit all arrive computed.
 */
export default function InsuranceCreditWorkstation() {
  const [date, setDate] = useState(hospitalToday());
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [rows, setRows] = useState<OfficerWorklistRow[]>([]);
  const [capabilities, setCapabilities] = useState<OfficerCapabilities | null>(null);
  const [queueLoading, setQueueLoading] = useState(true);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [visit, setVisit] = useState<OfficerVisitDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailToken, setDetailToken] = useState(0);

  // charge id -> officer's entered amount / reason
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [reasons, setReasons] = useState<Record<number, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authNotice, setAuthNotice] = useState<string | null>(null);

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
        const response = await fetch(
          `/api/insurance-credit/worklist?${queryString}`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload = (await response.json()) as ApiEnvelope<OfficerWorklist>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success || !payload.data) {
          setRows([]);
          setQueueError(
            messageFromPayload(payload, "Unable to load the review queue."),
          );
          return;
        }
        setRows(payload.data.rows);
        setCapabilities(payload.data.capabilities);
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
  useEffect(() => {
    if (selectedId === null) return;
    const controller = new AbortController();

    async function loadVisit(id: number) {
      setDetailLoading(true);
      setDetailError(null);
      try {
        const response = await fetch(`/api/insurance-credit/visits/${id}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<OfficerVisitDetail>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success || !payload.data) {
          setVisit(null);
          setDetailError(
            messageFromPayload(payload, "Unable to load the selected visit."),
          );
          return;
        }
        setVisit(payload.data);
        // Pre-fill each pending charge with the full permitted amount: the
        // common decision is "approve what the agreement allows".
        const seeded: Record<number, string> = {};
        for (const charge of payload.data.charges) {
          if (charge.needs_decision) {
            seeded[charge.id] = charge.permitted_sponsor_amount.toFixed(2);
          }
        }
        setAmounts(seeded);
        setReasons({});
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
    setAuthError(null);
    setAuthNotice(null);
  }

  const pending = useMemo(
    () => (visit?.charges ?? []).filter((c) => c.needs_decision),
    [visit],
  );

  const handleAuthorize = useCallback(async () => {
    if (selectedId === null || !visit) return;
    setSubmitting(true);
    setAuthError(null);
    setAuthNotice(null);

    const decisions: AuthorizationDecision[] = pending.map((charge) => {
      const raw = amounts[charge.id];
      const amount = Number(raw);
      return {
        charge_id: charge.id,
        amount: Number.isFinite(amount) ? amount : 0,
        reason: (reasons[charge.id] ?? "").trim() || undefined,
      };
    });

    try {
      const response = await fetch(
        `/api/insurance-credit/visits/${selectedId}/authorize`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decisions,
            idempotency_key: newIdempotencyKey(),
          }),
        },
      );
      const payload = (await response.json()) as ApiEnvelope<OfficerVisitDetail>;

      if (!response.ok || !payload.success || !payload.data) {
        setAuthError(
          messageFromPayload(payload, "Unable to record the authorization."),
        );
        // SERVER-AUTHORITATIVE RECOVERY. The usual cause is that the available
        // benefit moved after this page rendered, so re-read rather than
        // resubmit -- a blind retry would authorize against a figure that no
        // longer exists.
        setDetailToken((token) => token + 1);
        setRefreshToken((token) => token + 1);
        return;
      }

      setVisit(payload.data);
      setAuthNotice("Authorization recorded.");
      setRefreshToken((token) => token + 1);
    } catch {
      setAuthError("Unable to reach the YOYA EMR gateway.");
      setDetailToken((token) => token + 1);
    } finally {
      setSubmitting(false);
    }
  }, [selectedId, visit, pending, amounts, reasons]);

  const denied = capabilities !== null && !capabilities.insurance_credit_desk;

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
              aria-label="Search the review queue"
            />
          </div>
          <p className="mt-1.5 text-[11px] text-slate-500">
            {rows.length} visit{rows.length === 1 ? "" : "s"} awaiting review
          </p>
        </div>

        {queueLoading ? (
          <p className="p-3 text-sm text-slate-500" role="status">
            Loading queue...
          </p>
        ) : queueError ? (
          <div className="m-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {queueError}
          </div>
        ) : !rows.length ? (
          <div className="p-4 text-sm text-slate-500">
            <p className="font-medium text-slate-700">Nothing awaiting review.</p>
            <p className="mt-1 leading-5">
              Sponsored visits appear here once charges exist and no sponsor
              decision has been made.
            </p>
          </div>
        ) : (
          <ul className="min-h-0 flex-1 overflow-y-auto">
            {rows.map((row) => {
              const selected = row.appointment_id === selectedId;
              return (
                <li key={row.appointment_id}>
                  <button
                    type="button"
                    onClick={() => handleSelect(row.appointment_id)}
                    aria-current={selected ? "true" : undefined}
                    className={`w-full border-b border-slate-200 px-2.5 py-2 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 ${
                      selected
                        ? "bg-emerald-50 ring-1 ring-inset ring-emerald-300"
                        : "hover:bg-slate-50"
                    }`}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-sm font-semibold text-slate-900">
                        {row.patient.name}
                      </span>
                      <span className="shrink-0 font-mono text-sm font-semibold tabular-nums text-sky-800">
                        {money(row.permitted_sponsor_total)}
                      </span>
                    </div>
                    <div className="mt-0.5 truncate font-mono text-[11px] text-slate-500">
                      {row.patient.identification_code ?? "-"} ·{" "}
                      {row.payer_name ?? "-"}
                    </div>
                    <div className="mt-0.5 font-mono text-[11px] text-slate-500">
                      {row.pending_charge_count} charge
                      {row.pending_charge_count === 1 ? "" : "s"} to decide
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {/* CENTER: visit + charges */}
      <section className="flex min-w-0 flex-1 flex-col gap-2">
        {denied ? (
          <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
            This account does not have access to the Insurance/Credit Desk.
          </div>
        ) : detailLoading ? (
          <p className="p-4 text-sm text-slate-500" role="status">
            Loading visit...
          </p>
        ) : detailError ? (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {detailError}
          </div>
        ) : visit ? (
          <>
            <div className="rounded-md border border-slate-200 bg-white p-2.5">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="truncate text-base font-bold text-slate-900">
                  {visit.patient.name}
                </h2>
                <span className="shrink-0 font-mono text-[11px] text-slate-500">
                  {visit.patient.identification_code ?? "-"}
                </span>
              </div>
              <p className="mt-0.5 font-mono text-[11px] text-slate-500">
                {visit.appointment.appointment_code ?? "-"}
                {visit.eligibility
                  ? ` · ${visit.eligibility.payer_name ?? "-"} · ${
                      visit.eligibility.member_reference ??
                      visit.eligibility.membership_number ??
                      visit.eligibility.reference ??
                      "-"
                    }`
                  : " · no eligibility"}
              </p>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-slate-200 bg-white">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-2 py-1.5 font-semibold">Charge</th>
                    <th className="px-2 py-1.5 text-right font-semibold">Amount</th>
                    <th className="px-2 py-1.5 text-right font-semibold">Permitted</th>
                    <th className="px-2 py-1.5 text-right font-semibold">Authorized</th>
                    <th className="px-2 py-1.5 text-right font-semibold">Patient</th>
                  </tr>
                </thead>
                <tbody>
                  {visit.charges.map((charge) => (
                    <ChargeRow key={charge.id} charge={charge} />
                  ))}
                  {!visit.charges.length ? (
                    <tr>
                      <td colSpan={5} className="px-2 py-3 text-center text-slate-400">
                        No live charges on this visit.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center rounded-md border border-dashed border-slate-300 bg-white">
            <p className="text-sm text-slate-500">Select a visit from the queue.</p>
          </div>
        )}
      </section>

      {/* RIGHT: decisions */}
      <section className="w-[330px] shrink-0 overflow-y-auto">
        {!visit ? (
          <div className="rounded-md border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
            Authorization appears once a visit is selected.
          </div>
        ) : !pending.length ? (
          <div className="rounded-md border border-slate-200 bg-white p-3">
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Decisions
            </h3>
            <p className="mt-2 text-sm leading-5 text-slate-600">
              Every charge on this visit has a sponsor decision. Patient
              responsibility is{" "}
              <span className="font-mono font-semibold text-slate-900">
                {money(visit.summary.patient_responsibility_total)}
              </span>
              , which the cashier collects.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2 rounded-md border border-slate-200 bg-white p-3">
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
              Authorize {pending.length} charge{pending.length === 1 ? "" : "s"}
            </h3>

            {pending.map((charge) => {
              const entered = Number(amounts[charge.id] ?? "0");
              const reduced =
                Number.isFinite(entered) &&
                entered < charge.permitted_sponsor_amount - 0.005;
              return (
                <div
                  key={charge.id}
                  className="rounded border border-slate-200 p-2"
                >
                  <p className="truncate text-xs font-semibold text-slate-800">
                    {charge.description ?? charge.name}
                  </p>
                  <p className="font-mono text-[11px] text-slate-500">
                    {money(charge.amount)} · permitted{" "}
                    {money(charge.permitted_sponsor_amount)}
                    {charge.limit_available !== null
                      ? ` · benefit left ${money(charge.limit_available)}`
                      : ""}
                  </p>
                  {charge.requires_authorization ? (
                    <p className="mt-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">
                      Prior authorization required
                    </p>
                  ) : null}
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    value={amounts[charge.id] ?? ""}
                    onChange={(event) =>
                      setAmounts((prev) => ({
                        ...prev,
                        [charge.id]: event.target.value,
                      }))
                    }
                    className="mt-1 h-9 w-full rounded-md border border-slate-300 px-2 text-right font-mono text-sm font-semibold tabular-nums outline-none focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
                    aria-label={`Authorized sponsor amount for ${charge.name}`}
                  />
                  {reduced ? (
                    <input
                      type="text"
                      value={reasons[charge.id] ?? ""}
                      onChange={(event) =>
                        setReasons((prev) => ({
                          ...prev,
                          [charge.id]: event.target.value,
                        }))
                      }
                      placeholder="Reason required when reducing or denying"
                      className="mt-1 h-8 w-full rounded-md border border-amber-300 px-2 text-xs outline-none focus:border-amber-500"
                      aria-label={`Reason for ${charge.name}`}
                    />
                  ) : null}
                </div>
              );
            })}

            {authError ? (
              <div className="rounded-md border border-red-200 bg-red-50 px-2.5 py-2 text-xs leading-5 text-red-700">
                {authError}
              </div>
            ) : null}
            {authNotice ? (
              <div className="rounded-md border border-emerald-200 bg-emerald-50 px-2.5 py-2 text-xs text-emerald-800">
                {authNotice}
              </div>
            ) : null}

            <button
              type="button"
              onClick={handleAuthorize}
              disabled={submitting || !visit.capabilities.authorize_sponsor}
              className="h-11 w-full rounded-md bg-emerald-700 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {submitting ? "Recording..." : "Authorize selected"}
            </button>
          </div>
        )}
      </section>
    </div>
  );
}

function ChargeRow({ charge }: { charge: OfficerChargeRow }) {
  return (
    <tr className="border-t border-slate-100">
      <td className="px-2 py-1.5">
        <span className="block truncate text-slate-800">
          {charge.description ?? charge.name}
        </span>
        <span className="font-mono text-[10px] text-slate-400">
          {charge.matched_rule ?? charge.reason_code ?? ""}
          {charge.excluded ? " · excluded" : ""}
          {charge.decision_frozen_reason ? " · frozen" : ""}
        </span>
      </td>
      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-slate-700">
        {money(charge.amount)}
      </td>
      <td className="px-2 py-1.5 text-right font-mono tabular-nums text-sky-800">
        {money(charge.permitted_sponsor_amount)}
      </td>
      <td className="px-2 py-1.5 text-right font-mono tabular-nums font-semibold text-emerald-800">
        {money(charge.authorized_sponsor_amount)}
      </td>
      <td className="px-2 py-1.5 text-right font-mono tabular-nums font-semibold text-slate-900">
        {money(charge.patient_responsibility)}
      </td>
    </tr>
  );
}

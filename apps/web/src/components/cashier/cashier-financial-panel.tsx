"use client";

import type { CashierVisitDetail } from "@/types/cashier";
import { cashierLabel, modeLabel, money } from "@/lib/cashier-format";

type Props = { visit: CashierVisitDetail };

function Row({
  label,
  value,
  tone = "normal",
}: {
  label: string;
  value: string;
  tone?: "normal" | "muted" | "total" | "sponsor";
}) {
  const valueClass =
    tone === "total"
      ? "text-base font-bold text-emerald-800"
      : tone === "sponsor"
        ? "text-sm font-semibold text-sky-800"
        : tone === "muted"
          ? "text-sm text-slate-500"
          : "text-sm font-semibold text-slate-900";
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-dotted border-slate-200 py-1">
      <span className="text-xs text-slate-600">{label}</span>
      <span className={`font-mono tabular-nums ${valueClass}`}>{value}</span>
    </div>
  );
}

/**
 * The authoritative split.
 *
 * Every figure is read straight from the payload. Nothing here adds, subtracts
 * or compares amounts to decide what to show -- the server already decided, and
 * a second calculation in the browser is how a cashier ends up quoting a number
 * the backend will refuse.
 */
export default function CashierFinancialPanel({ visit }: Props) {
  const { financial, collectability, charge_lines: lines } = visit;
  const sponsored = financial.sponsor_responsibility > 0;

  return (
    <div className="flex min-h-0 flex-col gap-2">
      {/* Identity */}
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
          {visit.encounter ? ` · ${visit.encounter.name}` : ""}
          {visit.patient.age !== null ? ` · ${visit.patient.age}y` : ""}
          {visit.patient.gender ? ` · ${visit.patient.gender}` : ""}
        </p>
      </div>

      {/* Mode + clearance badges. The mode is always visible: a cashier must
          never have to guess whether the split is being applied. */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
            financial.responsibility_advisory
              ? "border-sky-300 bg-sky-50 text-sky-800"
              : "border-emerald-300 bg-emerald-50 text-emerald-800"
          }`}
        >
          {modeLabel(financial.responsibility_mode)}
        </span>
        {financial.responsibility_advisory && sponsored ? (
          <span className="rounded border border-sky-200 bg-white px-1.5 py-0.5 text-[10px] font-medium text-sky-700">
            Advisory only — collect the full amount
          </span>
        ) : null}
        <span
          className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
            financial.financially_cleared
              ? "border-emerald-300 bg-emerald-50 text-emerald-800"
              : "border-slate-300 bg-slate-100 text-slate-600"
          }`}
        >
          {financial.financially_cleared ? "Cleared" : "Not cleared"}
        </span>
      </div>

      {/* The split */}
      <div className="rounded-md border border-slate-200 bg-white p-2.5">
        <div className="mb-1 flex items-baseline justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Responsibility
          </h3>
          <span className="font-mono text-[10px] text-slate-400">
            {financial.currency ?? ""}
          </span>
        </div>
        <Row label="Visit total" value={money(financial.amount_estimated)} />
        {sponsored ? (
          <>
            <Row
              label="Sponsor authorized"
              value={money(financial.sponsor_authorized)}
              tone="sponsor"
            />
            {financial.sponsor_responsibility >
            financial.sponsor_authorized ? (
              <Row
                label="Sponsor proposed (not authorized)"
                value={money(
                  financial.sponsor_responsibility -
                    financial.sponsor_authorized,
                )}
                tone="muted"
              />
            ) : null}
          </>
        ) : null}
        <Row
          label="Patient responsibility"
          value={money(financial.patient_responsibility)}
        />
        <Row label="Already paid" value={money(financial.patient_paid)} tone="muted" />
        <Row
          label="To collect now"
          value={money(financial.patient_outstanding)}
          tone="total"
        />
      </div>

      {/* Why payment is closed, in the server's own words. */}
      {!collectability.collectable && collectability.reason ? (
        <div
          className={`rounded-md border p-2.5 text-xs leading-5 ${
            collectability.reason_code === "sponsor_authorization_pending"
              ? "border-red-200 bg-red-50 text-red-800"
              : "border-slate-200 bg-slate-50 text-slate-700"
          }`}
        >
          {collectability.reason}
        </div>
      ) : null}

      {/* Charges */}
      <div className="min-h-0 flex-1 overflow-y-auto rounded-md border border-slate-200 bg-white">
        <table className="w-full text-left text-xs">
          <thead className="sticky top-0 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-2 py-1.5 font-semibold">Charge</th>
              <th className="px-2 py-1.5 text-right font-semibold">Total</th>
              {sponsored ? (
                <th className="px-2 py-1.5 text-right font-semibold">Sponsor</th>
              ) : null}
              <th className="px-2 py-1.5 text-right font-semibold">Patient</th>
              <th className="px-2 py-1.5 text-right font-semibold">Paid</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.id} className="border-t border-slate-100">
                <td className="px-2 py-1.5">
                  <span className="block truncate text-slate-800">
                    {line.description ?? line.name}
                  </span>
                  <span className="font-mono text-[10px] text-slate-400">
                    {cashierLabel(line.responsibility_state, "")}
                  </span>
                </td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums text-slate-700">
                  {money(line.amount)}
                </td>
                {sponsored ? (
                  <td className="px-2 py-1.5 text-right font-mono tabular-nums text-sky-800">
                    {money(line.sponsor_authorized)}
                  </td>
                ) : null}
                <td className="px-2 py-1.5 text-right font-mono tabular-nums font-semibold text-slate-900">
                  {money(line.patient_responsibility)}
                </td>
                <td className="px-2 py-1.5 text-right font-mono tabular-nums text-slate-500">
                  {money(line.received)}
                </td>
              </tr>
            ))}
            {!lines.length ? (
              <tr>
                <td colSpan={5} className="px-2 py-3 text-center text-slate-400">
                  No charges on this visit.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </div>
    </div>
  );
}

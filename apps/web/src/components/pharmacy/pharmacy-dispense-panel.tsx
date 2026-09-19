"use client";

import { formatHospitalDate, formatHospitalDateTime } from "@/lib/clinical-format";
import {
  ageSexLabel,
  billingLabel,
  clearanceNotice,
  lineProgressLabel,
  medicineLabel,
  orDash,
  qtyLabel,
  reasonNotice,
  stockLabel,
} from "@/lib/pharmacy-desk-format";
import type { PharmacyDispenseDetail, PharmacyDispenseLine } from "@/types/pharmacy-desk";

import { PharmacyLanePill, PharmacyPriorityPill, VerdictChip } from "./pharmacy-status-pill";

/**
 * The Pharmacy Desk dispense panel. READ ONLY, and it renders no control.
 *
 * NOT EVEN A DISABLED BUTTON. Preparing quantities, Mark Ready, Validate
 * Dispense and cancellation are later slices; a greyed-out button is a promise
 * about workflow that has not shipped.
 *
 * NOTHING PRICED APPEARS HERE. Billing is shown as verdicts only -- blocked or
 * not, mapped or not -- because that is all the payload carries.
 *
 * AN ANOMALY IS STATED, NOT RESOLVED. The server's fixed sentence is shown and
 * the record's own state travels beside the derived lane, so a pharmacist can
 * see both what the record claims and why the desk does not believe it.
 *
 * NO "DISPENSED BY". The record does not reliably know who handed medication
 * over, so the panel does not claim it.
 */

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="cl-micro font-bold uppercase tracking-[0.06em] text-slate-400">{label}</dt>
      <dd className={`truncate cl-secondary font-semibold text-slate-800 ${mono ? "font-mono" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

function Banner({ tone, children }: { tone: "amber" | "red"; children: React.ReactNode }) {
  const style =
    tone === "red" ? "border-red-300 bg-red-50 text-red-900" : "border-amber-300 bg-amber-50 text-amber-900";
  return (
    <div role="status" className={`flex items-start gap-2 rounded border px-3 py-2 cl-secondary ${style}`}>
      <span aria-hidden className="mt-px font-bold">
        !
      </span>
      <p className="font-semibold">{children}</p>
    </div>
  );
}

/** One quantity cell. Remaining and pending are emphasised when non-zero. */
function Qty({ value, strong = false }: { value: number; strong?: boolean }) {
  return (
    <td
      className={`py-1 pr-2 text-right tabular-nums ${
        strong && value > 0 ? "font-bold text-slate-900" : "text-slate-700"
      }`}
    >
      {qtyLabel(value)}
    </td>
  );
}

function LineRows({ line }: { line: PharmacyDispenseLine }) {
  const sig = [line.dosage, line.route, line.frequency, line.duration].filter(Boolean).join(" · ");
  return (
    <>
      <tr className="border-t border-slate-100 align-top">
        <td className="py-1 pr-2">
          <span className="block font-semibold text-slate-900">{medicineLabel(line)}</span>
          <span className="block cl-meta text-slate-500">{sig || "No directions recorded"}</span>
        </td>
        <Qty value={line.prescribed_quantity} />
        <Qty value={line.intended_quantity} />
        <Qty value={line.delivered_quantity} />
        <Qty value={line.consumed_quantity} />
        <Qty value={line.remaining_quantity} strong />
        <td className="py-1 pr-2">
          <span className="flex flex-col items-start gap-0.5">
            <VerdictChip ok={line.billing_mapped} text={`Billing ${billingLabel(line)}`} />
            <VerdictChip
              ok={line.inventory_mapped ? line.stock_sufficient : false}
              text={stockLabel(line)}
            />
          </span>
        </td>
      </tr>
      {line.instruction ? (
        <tr>
          <td colSpan={7} className="pb-1 pr-2 cl-meta text-slate-600">
            <span className="font-bold uppercase tracking-[0.06em] text-slate-400">Instruction </span>
            {line.instruction}
          </td>
        </tr>
      ) : null}
    </>
  );
}

export default function PharmacyDispensePanel({
  detail,
  loading,
  error,
  empty,
  stale,
}: {
  detail: PharmacyDispenseDetail | null;
  loading: boolean;
  error: string | null;
  empty: boolean;
  stale: boolean;
}) {
  if (error) {
    return (
      <section className="flex min-h-[340px] items-center justify-center rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <p role="alert" className="cl-body text-red-800">
          {error}
        </p>
      </section>
    );
  }

  if (loading) {
    return (
      <section
        aria-label="Loading dispense"
        className="flex min-h-[340px] flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4 shadow-sm"
      >
        <div className="h-4 w-1/3 animate-pulse rounded bg-slate-200" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-slate-100" />
        <div className="h-24 w-full animate-pulse rounded bg-slate-100" />
      </section>
    );
  }

  if (!detail) {
    return (
      <section className="flex min-h-[340px] flex-col items-center justify-center gap-1 rounded-lg border border-slate-200 bg-white p-6 text-center shadow-sm">
        <p className="cl-body font-semibold text-slate-600">
          {empty ? "No dispense selected" : "Select a dispense"}
        </p>
        <p className="cl-secondary text-slate-500">
          {empty
            ? "The queue is empty for the current lane and filters."
            : "Choose a dispense from the queue to see its details."}
        </p>
      </section>
    );
  }

  const reason = reasonNotice(detail);
  const clearance = clearanceNotice(detail);

  return (
    <section className="flex min-h-[340px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <div className="flex min-w-0 flex-col">
          <span className="truncate cl-strong font-bold text-slate-900">{detail.patient?.name ?? "—"}</span>
          <span className="cl-meta text-slate-500">
            <span className="font-mono font-semibold text-slate-700">{detail.dispense_code}</span> · MRN{" "}
            <span className="font-mono">{orDash(detail.patient?.mrn)}</span> · {ageSexLabel(detail.patient)}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {stale ? <span className="cl-meta text-amber-700">Showing last loaded data</span> : null}
          <PharmacyPriorityPill priority={detail.priority} label={detail.priority_label} />
          <PharmacyLanePill lane={detail.lane} label={detail.lane_label} />
        </div>
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3">
        {reason ? <Banner tone={reason.tone}>{reason.text}</Banner> : null}
        {clearance ? <Banner tone="amber">{clearance}</Banner> : null}

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 min-[700px]:grid-cols-4">
          <Field label="Doctor" value={orDash(detail.prescriber?.name)} />
          <Field label="Prescription" value={orDash(detail.prescription?.code)} mono />
          <Field
            label="Prescription record"
            value={orDash(detail.prescription?.state_label ?? detail.prescription?.state)}
          />
          <Field label="Prescribed" value={formatHospitalDate(detail.prescription?.date ?? null, "—")} />
          <Field label="Dispense record" value={orDash(detail.state_label ?? detail.state)} />
          <Field label="Sent to pharmacy" value={formatHospitalDateTime(detail.dispense_date, "—")} />
          <Field label="Financial clearance" value={detail.billing_blocked ? "Blocked" : "Not blocking"} />
          <Field label="Lines supplied" value={lineProgressLabel(detail)} />
          <Field
            label="Source"
            value={detail.ordered_from_consultation ? "Doctor Desk prescription" : "Legacy / Odoo prescription"}
          />
          <Field label="Pharmacy Store" value={detail.pharmacy_store_configured ? "Configured" : "Not configured"} />
        </dl>

        <section className="flex flex-col gap-1.5">
          <h3 className="cl-micro font-bold uppercase tracking-[0.08em] text-slate-500">
            Medicines · quantities are cumulative
          </h3>
          {detail.lines.length === 0 ? (
            <p className="cl-secondary text-slate-500">No medicine lines could be read for this dispense.</p>
          ) : (
            <table className="w-full table-fixed border-collapse cl-secondary">
              <thead>
                <tr className="border-b border-slate-200 text-left cl-micro uppercase tracking-[0.06em] text-slate-400">
                  <th className="w-[30%] py-1 font-bold">Medicine · directions</th>
                  <th className="py-1 pr-2 text-right font-bold" title="Prescribed quantity">Rx</th>
                  <th className="py-1 pr-2 text-right font-bold" title="Intended cumulative quantity">Intended</th>
                  <th className="py-1 pr-2 text-right font-bold" title="Delivered (billing record)">Delivered</th>
                  <th className="py-1 pr-2 text-right font-bold" title="Consumed from stock">Consumed</th>
                  <th className="py-1 pr-2 text-right font-bold" title="Prescribed minus supplied">Remaining</th>
                  <th className="w-[22%] py-1 font-bold">Configuration</th>
                </tr>
              </thead>
              <tbody>
                {detail.lines.map((line) => (
                  <LineRows key={line.id} line={line} />
                ))}
              </tbody>
            </table>
          )}
        </section>

        {detail.notes ? (
          <section className="flex flex-col gap-1">
            <h3 className="cl-micro font-bold uppercase tracking-[0.08em] text-slate-500">Pharmacy notes</h3>
            <p className="whitespace-pre-wrap cl-body text-slate-800">{detail.notes}</p>
          </section>
        ) : null}
      </div>

      <footer className="flex h-7 shrink-0 items-center border-t border-slate-200 bg-slate-50 px-3 cl-meta text-slate-500">
        Read-only view. Dispensing actions are not available on this desk yet.
      </footer>
    </section>
  );
}

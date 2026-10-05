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
import { lineIssueText, type PrepareDraft, type PreparePlan } from "@/lib/pharmacy-desk-actions";
import type { PharmacyDispenseDetail, PharmacyDispenseLine } from "@/types/pharmacy-desk";

import { PharmacyLanePill, PharmacyPriorityPill, VerdictChip } from "./pharmacy-status-pill";

/**
 * The Pharmacy Desk dispense panel.
 *
 * TWO ACTIONS (Slice 2), BOTH OFFERED BY THE SERVER. The intended quantity is
 * an input only when the record's own `can_prepare` says so, and Validate is
 * offered only when `can_validate` does. Prescribed, delivered, consumed,
 * remaining and the billing/stock verdicts are always read-only. The panel
 * never saves a line: both actions open a confirmation step, and the
 * workstation sends ONE atomic request after it.
 *
 * Cancellation, returns and substitution are not offered: no route exists.
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

function Banner({ tone, children }: { tone: "amber" | "red" | "green"; children: React.ReactNode }) {
  const style =
    tone === "red"
      ? "border-red-300 bg-red-50 text-red-900"
      : tone === "green"
        ? "border-emerald-300 bg-emerald-50 text-emerald-900"
        : "border-amber-300 bg-amber-50 text-amber-900";
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

function IntendedInput({
  line,
  value,
  issue,
  disabled,
  onChange,
}: {
  line: PharmacyDispenseLine;
  value: string;
  issue: string | null;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <td className="py-1 pr-2 text-right">
      <input
        type="text"
        inputMode="decimal"
        aria-label={`Intended cumulative quantity for ${medicineLabel(line)}`}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className={`w-[72px] rounded border px-1.5 py-0.5 text-right tabular-nums outline-none focus:ring-1 ${
          issue
            ? "border-red-500 bg-red-50 focus:ring-red-500"
            : "border-violet-300 bg-white focus:border-violet-600 focus:ring-violet-600"
        }`}
      />
      {issue ? <span className="block cl-micro font-bold text-red-700">{issue}</span> : null}
    </td>
  );
}

function LineRows({
  line,
  draftValue,
  issue,
  editable,
  busy,
  onDraftChange,
}: {
  line: PharmacyDispenseLine;
  draftValue: string | null;
  issue: string | null;
  editable: boolean;
  busy: boolean;
  onDraftChange: (lineId: number, value: string) => void;
}) {
  const sig = [line.dosage, line.route, line.frequency, line.duration].filter(Boolean).join(" · ");
  return (
    <>
      <tr className="border-t border-slate-100 align-top">
        <td className="py-1 pr-2">
          <span className="block font-semibold text-slate-900">{medicineLabel(line)}</span>
          <span className="block cl-meta text-slate-500">{sig || "No directions recorded"}</span>
        </td>
        <Qty value={line.prescribed_quantity} />
        {editable && draftValue !== null ? (
          <IntendedInput
            line={line}
            value={draftValue}
            issue={issue}
            disabled={busy}
            onChange={(value) => onDraftChange(line.id, value)}
          />
        ) : (
          <Qty value={line.intended_quantity} />
        )}
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
  draft,
  plan,
  busy,
  actionMessage,
  onDraftChange,
  onRequestPrepare,
  onRequestValidate,
}: {
  detail: PharmacyDispenseDetail | null;
  loading: boolean;
  error: string | null;
  empty: boolean;
  stale: boolean;
  /** The Prepare inputs, seeded from the server; null when not editable. */
  draft: PrepareDraft | null;
  plan: PreparePlan | null;
  busy: boolean;
  actionMessage: { tone: "red" | "amber" | "green"; text: string } | null;
  onDraftChange: (lineId: number, value: string) => void;
  onRequestPrepare: () => void;
  onRequestValidate: () => void;
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
  const editable = detail.can_prepare && draft !== null;
  const issueFor = (lineId: number) =>
    lineIssueText(plan?.lines.find((entry) => entry.line.id === lineId)?.issue ?? null);
  const prepareReady = Boolean(plan && plan.valid && plan.hasIncrement);

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
        {actionMessage ? (
          <div
            role="status"
            className={`rounded border px-3 py-2 cl-secondary font-semibold ${
              actionMessage.tone === "green"
                ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                : actionMessage.tone === "red"
                  ? "border-red-300 bg-red-50 text-red-900"
                  : "border-amber-300 bg-amber-50 text-amber-900"
            }`}
          >
            {actionMessage.text}
          </div>
        ) : null}
        {reason ? <Banner tone={reason.tone}>{reason.text}</Banner> : null}
        {clearance ? (
          <Banner tone={detail.financial_cover === "inpatient_credit" ? "green" : "amber"}>{clearance}</Banner>
        ) : null}

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
                  <th className="py-1 pr-2 text-right font-bold" title="Total intended to have been supplied after the next validation">Intended cumulative</th>
                  <th className="py-1 pr-2 text-right font-bold" title="Delivered (billing record)">Delivered</th>
                  <th className="py-1 pr-2 text-right font-bold" title="Consumed from stock">Consumed</th>
                  <th className="py-1 pr-2 text-right font-bold" title="Prescribed minus supplied">Remaining</th>
                  <th className="w-[22%] py-1 font-bold">Configuration</th>
                </tr>
              </thead>
              <tbody>
                {detail.lines.map((line) => (
                  <LineRows
                    key={line.id}
                    line={line}
                    draftValue={draft ? (draft[line.id] ?? "") : null}
                    issue={editable ? issueFor(line.id) : null}
                    editable={editable}
                    busy={busy}
                    onDraftChange={onDraftChange}
                  />
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

      <footer className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-slate-50 px-3 py-1.5">
        <span className="cl-meta text-slate-500">
          {detail.can_prepare || detail.can_validate
            ? "Quantities are cumulative. Each action asks for confirmation."
            : "No dispensing action is available for this record."}
        </span>
        <span className="flex items-center gap-2">
          {detail.can_prepare ? (
            <button
              type="button"
              onClick={onRequestPrepare}
              disabled={busy || !prepareReady}
              className="rounded border border-violet-600 bg-white px-2.5 py-1 cl-secondary font-bold text-violet-800 hover:bg-violet-50 disabled:opacity-50"
            >
              Prepare…
            </button>
          ) : null}
          {detail.can_validate ? (
            <button
              type="button"
              onClick={onRequestValidate}
              disabled={busy}
              className="rounded border border-violet-700 bg-violet-700 px-2.5 py-1 cl-secondary font-bold text-white hover:bg-violet-800 disabled:opacity-50"
            >
              Validate dispense…
            </button>
          ) : null}
        </span>
      </footer>
    </section>
  );
}

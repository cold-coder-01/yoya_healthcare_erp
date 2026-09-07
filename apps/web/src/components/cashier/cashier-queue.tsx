"use client";

import type {
  CashierActiveServiceRow,
  CashierWorklistRow,
} from "@/types/cashier";
import {
  cashierLabel,
  laneLabel,
  laneTone,
  money,
  serviceCategorySummary,
  shortTime,
} from "@/lib/cashier-format";

type Props = {
  rows: CashierWorklistRow[];
  activeServiceRows: CashierActiveServiceRow[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  truncated: boolean;
  activeServiceTruncated: boolean;
  onSelect: (appointmentId: number) => void;
};

/**
 * The queue. Dense rows, one number per row, no cards -- in TWO SECTIONS.
 *
 * INITIAL CLEARANCE is the entrance handoff: triage is done and money stands
 * between the patient and the doctor.
 *
 * SERVICE PAYMENTS is the second lane: a service ordered during the
 * consultation is waiting on payment. It was named "In-consultation payments"
 * until Slice 4 made that wrong -- a visit stays in this lane after the doctor
 * signs off, because completing a consultation does not settle a bill, and a
 * patient whose laboratory work is still unpaid must not vanish from the queue
 * at the moment their doctor finishes with them.
 *
 * Rows therefore carry state=in_consultation OR state=done, and STAY that way
 * through payment: the desk shows the clinical state as a fact, never as
 * something the cashier changes.
 *
 * The two are rendered separately on purpose. Merged into one list, a patient
 * mid-consultation would read as somebody who never got past the entrance, and
 * a cashier would go looking for a patient who is already in a doctor's room.
 *
 * Both numbers shown are `patient_outstanding`, already made mode-correct by
 * the server. Nothing here computes money, a lane or a blocking reason.
 */
export default function CashierQueue({
  rows,
  activeServiceRows,
  selectedId,
  loading,
  error,
  truncated,
  activeServiceTruncated,
  onSelect,
}: Props) {
  if (loading) {
    return (
      <div className="p-3 text-sm text-slate-500" role="status">
        Loading queue...
      </div>
    );
  }

  if (error) {
    return (
      <div className="m-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
        {error}
      </div>
    );
  }

  if (!rows.length && !activeServiceRows.length) {
    return (
      <div className="p-4 text-sm text-slate-500">
        <p className="font-medium text-slate-700">Nothing awaiting payment.</p>
        <p className="mt-1 leading-5">
          Visits appear here once triage is complete and money is still owed,
          or when an ordered service has not been paid for.
        </p>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <QueueSection
          title="Initial clearance"
          hint="Triage complete, payment due before the doctor."
          count={rows.length}
          truncated={truncated}
          empty="No visits awaiting initial clearance."
        >
          {rows.map((row) => (
            <li key={`initial-${row.appointment_id}`}>
              <QueueButton
                selected={row.appointment_id === selectedId}
                onSelect={() => onSelect(row.appointment_id)}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-semibold text-slate-900">
                    {row.patient.name}
                  </span>
                  <span className="shrink-0 font-mono text-sm font-semibold tabular-nums text-slate-900">
                    {money(row.patient_outstanding)}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-[11px] text-slate-500">
                    {row.patient.identification_code ?? "-"} ·{" "}
                    {row.appointment_code ?? "-"} ·{" "}
                    {shortTime(row.appointment_date)}
                  </span>
                  <span
                    className={`shrink-0 rounded border px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${laneTone(
                      row.lane,
                    )}`}
                  >
                    {laneLabel(row.lane)}
                  </span>
                </div>
                {row.patient_paid > 0 ? (
                  <p className="mt-0.5 font-mono text-[11px] text-amber-700">
                    Paid {money(row.patient_paid)} so far
                  </p>
                ) : null}
              </QueueButton>
            </li>
          ))}
        </QueueSection>

        <QueueSection
          title="Service payments"
          hint="An ordered service is waiting on payment."
          count={activeServiceRows.length}
          truncated={activeServiceTruncated}
          empty="No ordered services awaiting payment."
        >
          {activeServiceRows.map((row) => {
            const categories = serviceCategorySummary(row.service_categories);
            return (
              <li key={`active-${row.appointment_id}`}>
                <QueueButton
                  selected={row.appointment_id === selectedId}
                  onSelect={() => onSelect(row.appointment_id)}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-semibold text-slate-900">
                      {row.patient.name}
                    </span>
                    <span className="shrink-0 font-mono text-sm font-semibold tabular-nums text-slate-900">
                      {money(row.patient_outstanding)}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-[11px] text-slate-500">
                      {row.patient.identification_code ?? "-"} ·{" "}
                      {row.appointment_code ?? "-"}
                    </span>
                    <span
                      className={`shrink-0 rounded border px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide ${laneTone(
                        row.lane,
                      )}`}
                    >
                      {laneLabel(row.lane)}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    {/*
                      The CLINICAL state, stated as a fact. Paying does not
                      change it and this desk never writes it.
                    */}
                    <span className="rounded border border-sky-300 bg-sky-50 px-1.5 py-px text-[10px] font-semibold uppercase tracking-wide text-sky-800">
                      {cashierLabel(row.visit_state)}
                    </span>
                    {categories ? (
                      <span className="rounded border border-slate-300 bg-slate-50 px-1.5 py-px text-[10px] font-medium text-slate-700">
                        {categories}
                      </span>
                    ) : null}
                  </div>
                  {/* The server's own wording. Never a locally invented one. */}
                  <p className="mt-0.5 text-[11px] leading-4 text-slate-600">
                    {row.blocking_reason}
                  </p>
                  {row.patient_paid > 0 ? (
                    <p className="mt-0.5 font-mono text-[11px] text-amber-700">
                      Paid {money(row.patient_paid)} so far
                    </p>
                  ) : null}
                </QueueButton>
              </li>
            );
          })}
        </QueueSection>
      </div>
    </div>
  );
}

function QueueSection({
  title,
  hint,
  count,
  truncated,
  empty,
  children,
}: {
  title: string;
  hint: string;
  count: number;
  truncated: boolean;
  empty: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <header className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50 px-2.5 py-1.5">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-[11px] font-semibold uppercase tracking-wide text-slate-700">
            {title}
          </h2>
          <span className="font-mono text-[11px] tabular-nums text-slate-500">
            {count}
          </span>
        </div>
        <p className="mt-px text-[10px] leading-3 text-slate-500">{hint}</p>
      </header>

      {count ? (
        <ul>{children}</ul>
      ) : (
        <p className="px-2.5 py-2 text-[11px] text-slate-400">{empty}</p>
      )}

      {truncated ? (
        <p className="border-y border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800">
          More visits match than are shown. Narrow the search to see the rest.
        </p>
      ) : null}
    </section>
  );
}

function QueueButton({
  selected,
  onSelect,
  children,
}: {
  selected: boolean;
  onSelect: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-current={selected ? "true" : undefined}
      className={`w-full border-b border-slate-200 px-2.5 py-2 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 ${
        selected
          ? "bg-emerald-50 ring-1 ring-inset ring-emerald-300"
          : "hover:bg-slate-50"
      }`}
    >
      {children}
    </button>
  );
}

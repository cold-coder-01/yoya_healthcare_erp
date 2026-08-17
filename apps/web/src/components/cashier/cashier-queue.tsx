"use client";

import type { CashierWorklistRow } from "@/types/cashier";
import { laneLabel, laneTone, money, shortTime } from "@/lib/cashier-format";

type Props = {
  rows: CashierWorklistRow[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  truncated: boolean;
  onSelect: (appointmentId: number) => void;
};

/**
 * The queue. Dense rows, one number per row, no cards.
 *
 * The number shown is `patient_outstanding`, which the server has already made
 * mode-correct -- the legacy gross under off/shadow, the patient residual under
 * enforce. The queue therefore shows what the cashier will actually be asked to
 * collect, in every mode, without computing anything.
 */
export default function CashierQueue({
  rows,
  selectedId,
  loading,
  error,
  truncated,
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

  if (!rows.length) {
    return (
      <div className="p-4 text-sm text-slate-500">
        <p className="font-medium text-slate-700">Nothing awaiting payment.</p>
        <p className="mt-1 leading-5">
          Visits appear here once triage is complete and money is still owed.
        </p>
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ul className="min-h-0 flex-1 overflow-y-auto">
        {rows.map((row) => {
          const selected = row.appointment_id === selectedId;
          return (
            <li key={row.appointment_id}>
              <button
                type="button"
                onClick={() => onSelect(row.appointment_id)}
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
              </button>
            </li>
          );
        })}
      </ul>

      {truncated ? (
        <p className="border-t border-amber-200 bg-amber-50 px-2.5 py-1.5 text-[11px] text-amber-800">
          More visits match than are shown. Narrow the search to see the rest.
        </p>
      ) : null}
    </div>
  );
}

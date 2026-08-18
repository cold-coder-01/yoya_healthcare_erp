"use client";

import { useEffect, useRef } from "react";

import { formatHospitalTime } from "@/lib/clinical-format";
import { compactGender, doctorLabel, statLabel } from "@/lib/doctor-format";
import type { DoctorQueueRow } from "@/types/doctor";

/**
 * ONE grid template for the header and every row, so the two cannot drift.
 *
 * The column order follows the vendor OPD worklist a doctor already reads
 * left-to-right -- Chart No, Name, Age, Sex, Stat, Remark -- because that
 * scanning order is muscle memory and re-ordering it would cost more than any
 * layout improvement could return. Time is added ahead of the chart number
 * (the vendor screen carries it inside Remark) since arrival order is how a
 * queue is actually worked, and the two flag columns at the end replace the
 * vendor's nine single-letter ones with the two a doctor acts on.
 */
const GRID =
  "grid grid-cols-[46px_minmax(0,1fr)_46px_74px] gap-x-2 " +
  "lg:grid-cols-[46px_72px_minmax(0,1fr)_62px_46px_minmax(0,1.1fr)_74px]";

/**
 * Stat tones. Emerald is reserved for Rev -- the ONE cell that means the
 * patient may be called through -- so a doctor scanning the column can find
 * their workable set without reading a word. Cash is amber (money is owed),
 * Wait neutral, Cons indigo, Done spent.
 */
const STAT_TONE: Record<string, string> = {
  Wait: "bg-slate-100 text-slate-700 border-slate-300",
  Cash: "bg-amber-100 text-amber-900 border-amber-300",
  Rev: "bg-emerald-100 text-emerald-900 border-emerald-400",
  Cons: "bg-indigo-100 text-indigo-900 border-indigo-300",
  Done: "bg-slate-100 text-slate-600 border-slate-300",
};

function QueueRow({
  row,
  selected,
  onSelect,
}: {
  row: DoctorQueueRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const stat = statLabel(row);

  // Keyboard selection moves the row off-screen otherwise: arrow-key paging
  // through a 40-patient queue is unusable if the viewport does not follow.
  useEffect(() => {
    if (selected) {
      ref.current?.scrollIntoView({ block: "nearest" });
    }
  }, [selected]);

  return (
    <li>
      <button
        ref={ref}
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        // tabIndex -1 on unselected rows: the whole list is one tab stop and
        // arrow keys move within it, which is the roving-tabindex pattern a
        // dense worklist needs. Tabbing through 40 buttons is not navigation.
        tabIndex={selected ? 0 : -1}
        className={`${GRID} min-h-[46px] w-full items-center border-b border-slate-200 px-2 py-1 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-600 ${
          selected
            ? "bg-slate-100 shadow-[inset_3px_0_0_#047857] hover:bg-slate-100"
            : "bg-white hover:bg-slate-50"
        }`}
      >
        <span className="text-[11px] font-bold leading-tight tabular-nums text-slate-700">
          {formatHospitalTime(row.appointment_date)}
        </span>

        {/* Chart number: the identifier the vendor screen leads with. */}
        <span className="hidden truncate font-mono text-[11px] font-bold leading-tight text-slate-800 lg:block">
          {row.patient.mrn ?? "—"}
        </span>

        <span className="min-w-0 self-start">
          <span className="flex min-w-0 items-center gap-1.5">
            {row.urgent ? (
              <span
                className="h-2 w-2 shrink-0 rounded-full bg-red-600"
                title={`Priority: ${doctorLabel(row.triage_priority)}`}
              />
            ) : null}
            <span className="truncate text-[13px] font-bold leading-tight text-slate-950">
              {row.patient.name}
            </span>
          </span>
          {/* Folded-in columns for narrow screens. Nothing is dropped. */}
          <span className="mt-0.5 block truncate font-mono text-[10px] leading-tight text-slate-500 lg:hidden">
            {row.patient.mrn ?? "—"}
            <span className="text-slate-300"> · </span>
            {row.patient.age ?? "-"}/{compactGender(row.patient.gender)}
          </span>
        </span>

        <span className="hidden text-[11px] leading-tight tabular-nums text-slate-700 lg:block">
          {row.patient.age ?? "-"}
        </span>

        <span className="text-[11px] font-semibold leading-tight text-slate-700">
          {compactGender(row.patient.gender)}
        </span>

        {/* Remark: the operational sentence. Chief complaint when the payload
            carries one, otherwise the department the visit is booked into. */}
        <span className="hidden min-w-0 truncate text-[11px] leading-tight text-slate-600 lg:block">
          {row.chief_complaint ?? row.department?.name ?? "—"}
        </span>

        <span className="flex items-center justify-end gap-1">
          {row.clearance.blocked ? (
            <span
              className="h-4 w-4 shrink-0 rounded-sm border border-amber-400 bg-amber-100 text-center text-[9px] font-bold leading-4 text-amber-900"
              // Odoo's allowlisted sentence. A fixed string with no figure and
              // no payer name -- see DOCTOR_CLEARANCE_REASONS.
              title={row.clearance.reason ?? "Financial clearance required"}
            >
              $
            </span>
          ) : null}
          <span
            className={`inline-flex h-5 w-[38px] items-center justify-center rounded border text-[10px] font-bold ${
              STAT_TONE[stat] ?? STAT_TONE.Wait
            }`}
          >
            {stat}
          </span>
        </span>
      </button>
    </li>
  );
}

export default function DoctorQueue({
  rows,
  selectedId,
  loading,
  error,
  truncated,
  onSelect,
}: {
  rows: DoctorQueueRow[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  truncated: boolean;
  onSelect: (appointmentId: number) => void;
}) {
  const listRef = useRef<HTMLUListElement>(null);

  /**
   * Arrow keys walk the queue; Home/End jump to its ends. Handled on the list
   * container rather than per row so the behaviour survives re-sorting, and so
   * a doctor can hold the key down without focus thrashing.
   */
  function handleKeyDown(event: React.KeyboardEvent<HTMLUListElement>) {
    const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
    if (!keys.includes(event.key) || rows.length === 0) return;
    event.preventDefault();

    const current = rows.findIndex((row) => row.appointment_id === selectedId);
    let next = current;

    if (event.key === "ArrowDown") next = current < 0 ? 0 : Math.min(current + 1, rows.length - 1);
    if (event.key === "ArrowUp") next = current < 0 ? 0 : Math.max(current - 1, 0);
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = rows.length - 1;

    const target = rows[next];
    if (target) {
      onSelect(target.appointment_id);
      // Focus follows selection so the next keystroke lands here, not on body.
      requestAnimationFrame(() => {
        listRef.current
          ?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]')
          ?.focus();
      });
    }
  }

  return (
    <section className="flex min-h-[340px] min-w-0 flex-col overflow-hidden rounded border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      <header className="flex h-8 shrink-0 items-center justify-between border-b border-slate-200 bg-slate-50 px-2.5">
        <h2 className="text-[11px] font-bold uppercase tracking-wide text-slate-700">
          Patient Queue ({rows.length})
        </h2>
        <span className="flex items-center gap-2">
          {loading ? (
            <span className="text-[10px] tabular-nums text-slate-500">Updating…</span>
          ) : null}
        </span>
      </header>

      <div
        className={`${GRID} shrink-0 border-b border-slate-200 bg-slate-50 px-2 py-1 text-[9px] font-bold uppercase tracking-wide text-slate-500`}
      >
        <span>Time</span>
        <span className="hidden lg:block">Chart No</span>
        <span>Name</span>
        <span className="hidden lg:block">Age</span>
        <span>Sex</span>
        <span className="hidden lg:block">Remark</span>
        <span className="text-right">Stat</span>
      </div>

      {error ? (
        <div className="border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          {error}
        </div>
      ) : null}
      {truncated ? (
        <div className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-900">
          Queue limit reached. Narrow the filters to see every patient.
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading && rows.length === 0 ? (
          <div aria-label="Loading patient queue">
            {Array.from({ length: 10 }, (_, index) => (
              <div
                key={index}
                className="h-[46px] animate-pulse border-b border-slate-100 bg-slate-50"
              />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="flex h-full min-h-32 items-center justify-center px-6 text-center">
            <p className="text-xs text-slate-500">
              No patients match the current filters.
            </p>
          </div>
        ) : (
          <ul
            ref={listRef}
            onKeyDown={handleKeyDown}
            aria-label="Patient queue. Use arrow keys to move between patients."
          >
            {rows.map((row) => (
              <QueueRow
                key={row.appointment_id}
                row={row}
                selected={selectedId === row.appointment_id}
                onSelect={() => onSelect(row.appointment_id)}
              />
            ))}
          </ul>
        )}
      </div>

      <footer className="flex h-6 shrink-0 items-center gap-2 border-t border-slate-200 bg-slate-50 px-2.5 text-[10px] text-slate-500">
        <span>
          {rows.length} {rows.length === 1 ? "patient" : "patients"}
        </span>
        <span className="text-slate-300">·</span>
        <span>↑↓ to move</span>
      </footer>
    </section>
  );
}

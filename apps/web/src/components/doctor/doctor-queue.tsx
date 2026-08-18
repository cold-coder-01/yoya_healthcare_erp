"use client";

import { useEffect, useRef } from "react";

import { formatHospitalTime } from "@/lib/clinical-format";
import { compactGender, doctorLabel, statLabel } from "@/lib/doctor-format";
import type { DoctorQueueRow } from "@/types/doctor";

import { stageTone } from "./doctor-badges";

/**
 * ONE grid template for the header and every row, so the two cannot drift.
 *
 * The column order follows the vendor OPD worklist a doctor already reads
 * left-to-right, but the WIDTHS were rebalanced around what is actually
 * scanned: the patient's name now owns the free space, and the chart number
 * sits beneath it as a secondary line rather than competing for a column of
 * its own. Age/Sex collapsed into one narrow cell for the same reason -- they
 * were two columns carrying about six characters between them.
 *
 * The result is four columns instead of seven, which is what lets each row
 * breathe at 52px without the panel needing more width.
 */
const GRID = "grid grid-cols-[52px_minmax(0,1fr)_46px_58px] items-center gap-x-2";

/**
 * Stat tones. Emerald is reserved for Rev -- the ONE cell that means the
 * patient may be called through -- so a doctor scanning the column can find
 * their workable set without reading a word.
 */
const STAT_TONE: Record<string, string> = {
  Wait: "border-slate-300 bg-slate-100 text-slate-600",
  Cash: "border-amber-400 bg-amber-100 text-amber-900",
  Rev: "border-emerald-500 bg-emerald-100 text-emerald-900",
  Cons: "border-indigo-300 bg-indigo-100 text-indigo-900",
  Done: "border-slate-200 bg-white text-slate-500",
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
  const tone = stageTone(row.queue_stage);
  // The second line: complaint when triage recorded one, department otherwise.
  const remark = row.chief_complaint ?? row.department?.name ?? null;

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
        className={`${GRID} relative min-h-[52px] w-full border-b border-slate-100 py-1.5 pl-3 pr-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-600 ${
          selected ? "bg-emerald-50/60" : "bg-white hover:bg-slate-50"
        }`}
      >
        {/*
          Selection is a solid emerald rail plus a tint. It is deliberately the
          only place emerald appears outside a status, and it reads as
          "you are here" rather than as a state the patient is in.
        */}
        <span
          aria-hidden
          className={`absolute inset-y-0 left-0 w-[3px] ${
            selected ? "bg-emerald-600" : "bg-transparent"
          }`}
        />

        <span className="text-[11px] font-bold leading-tight tabular-nums text-slate-500">
          {formatHospitalTime(row.appointment_date)}
        </span>

        {/* Identity: name on top at full weight, chart number and remark
            beneath in a quieter register. This is the scan target. */}
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="flex min-w-0 items-center gap-1.5">
            {row.urgent ? (
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-600"
                title={`Priority: ${doctorLabel(row.triage_priority)}`}
              />
            ) : null}
            <span className="truncate text-[13px] font-bold leading-tight text-slate-900">
              {row.patient.name}
            </span>
          </span>
          <span className="flex min-w-0 items-center gap-1 text-[10px] leading-tight text-slate-500">
            <span className="shrink-0 font-mono font-semibold text-slate-600">
              {row.patient.mrn ?? "—"}
            </span>
            {remark ? (
              <>
                <span aria-hidden className="shrink-0 text-slate-300">
                  ·
                </span>
                <span className="truncate">{remark}</span>
              </>
            ) : null}
          </span>
        </span>

        <span className="text-[11px] leading-tight tabular-nums text-slate-600">
          {row.patient.age ?? "-"}
          <span className="text-slate-300">/</span>
          {compactGender(row.patient.gender)}
        </span>

        {/* Status: the stage dot plus the short stat code. Two encodings of the
            same fact -- colour for peripheral vision, text for certainty. */}
        <span className="flex items-center justify-end gap-1.5">
          <span
            aria-hidden
            className={`h-2 w-2 shrink-0 rounded-full ${tone.dot}`}
          />
          <span
            title={tone.label}
            className={`inline-flex h-[19px] w-[40px] items-center justify-center rounded border text-[10px] font-bold ${
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
    <section className="flex min-h-[340px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      <header className="flex h-9 shrink-0 items-center justify-between border-b border-slate-200 bg-slate-50 px-3">
        <h2 className="flex items-baseline gap-1.5 text-[11px] font-bold uppercase tracking-[0.08em] text-slate-700">
          Patient Queue
          <span className="rounded bg-slate-200 px-1.5 py-px text-[10px] tabular-nums text-slate-700">
            {rows.length}
          </span>
        </h2>
        {loading ? (
          <span className="text-[10px] tabular-nums text-slate-500">Updating…</span>
        ) : null}
      </header>

      <div
        className={`${GRID} shrink-0 border-b border-slate-200 bg-slate-50 py-1 pl-3 pr-2 text-[9px] font-bold uppercase tracking-[0.06em] text-slate-400`}
      >
        <span>Time</span>
        <span>Patient · Chart no.</span>
        <span>Age/Sex</span>
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
                className="flex min-h-[52px] items-center gap-2 border-b border-slate-100 px-3"
              >
                <div className="h-2.5 w-9 animate-pulse rounded bg-slate-100" />
                <div className="flex flex-1 flex-col gap-1.5">
                  <div className="h-2.5 w-2/5 animate-pulse rounded bg-slate-200" />
                  <div className="h-2 w-3/5 animate-pulse rounded bg-slate-100" />
                </div>
                <div className="h-4 w-10 animate-pulse rounded bg-slate-100" />
              </div>
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="flex h-full min-h-32 flex-col items-center justify-center gap-1 px-6 text-center">
            <p className="text-xs font-semibold text-slate-600">No patients here</p>
            <p className="text-[11px] text-slate-500">
              Nothing matches the current filters.
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

      <footer className="flex h-7 shrink-0 items-center gap-2 border-t border-slate-200 bg-slate-50 px-3 text-[10px] text-slate-500">
        <span className="tabular-nums">
          {rows.length} {rows.length === 1 ? "patient" : "patients"}
        </span>
        <span aria-hidden className="text-slate-300">
          ·
        </span>
        <span className="flex items-center gap-1">
          <kbd className="rounded border border-slate-200 bg-white px-1 font-mono text-[9px] text-slate-500">
            ↑↓
          </kbd>
          to move
        </span>
      </footer>
    </section>
  );
}

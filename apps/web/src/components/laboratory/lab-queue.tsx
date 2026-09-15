"use client";

import { useEffect, useRef } from "react";

import { formatHospitalDate } from "@/lib/clinical-format";
import {
  ageSexLabel,
  isUrgentPriority,
  orDash,
  testCountLabel,
} from "@/lib/lab-desk-format";
import type { LabQueueRow } from "@/types/lab-desk";

import { LabPriorityPill, LabStatusPill } from "./lab-status-pill";

/**
 * ONE grid template for the header and every row, so the two cannot drift.
 *
 * Columns follow what a technician actually scans, left to right: which
 * request, whose sample, how old the order is, how urgent, and what stage it
 * has reached. The patient owns the free space because it is the scan target;
 * the request code sits beneath it, the way the Doctor Desk puts the chart
 * number under the name.
 */
const GRID =
  "grid grid-cols-[minmax(0,1fr)_54px_44px_52px] items-center gap-x-2";

function QueueRow({
  row,
  selected,
  onSelect,
}: {
  row: LabQueueRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const urgent = isUrgentPriority(row.priority);

  // Keyboard selection moves the row off-screen otherwise: arrow-key paging
  // through a long bench queue is unusable if the viewport does not follow.
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
        // arrow keys move within it -- the roving-tabindex pattern a dense
        // worklist needs. Tabbing through forty buttons is not navigation.
        tabIndex={selected ? 0 : -1}
        className={`${GRID} relative min-h-[54px] w-full border-b border-slate-100 py-1.5 pl-3 pr-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-indigo-600 ${
          selected ? "bg-indigo-50/60" : "bg-white hover:bg-slate-50"
        }`}
      >
        {/* Selection is a solid indigo rail plus a tint. Deliberately the only
            place the accent appears outside a status, so it reads as "you are
            here" rather than as a state the request is in. */}
        <span
          aria-hidden
          className={`absolute inset-y-0 left-0 w-[3px] ${
            selected ? "bg-indigo-600" : "bg-transparent"
          }`}
        />

        {/* Identity: patient on top at full weight, request code, chart number
            and the ordered tests beneath in a quieter register. */}
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="flex min-w-0 items-center gap-1.5">
            {urgent ? (
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-600"
                title={`Priority: ${orDash(row.priority_label)}`}
              />
            ) : null}
            <span className="truncate cl-strong font-bold leading-tight text-slate-900">
              {row.patient?.name ?? "—"}
            </span>
          </span>
          <span className="flex min-w-0 items-center gap-1 cl-meta leading-tight text-slate-500">
            <span className="shrink-0 font-mono font-semibold text-slate-600">
              {row.request_code}
            </span>
            <span aria-hidden className="shrink-0 text-slate-300">
              ·
            </span>
            <span className="shrink-0 font-mono">{row.patient?.mrn ?? "—"}</span>
            {row.tests_summary ? (
              <>
                <span aria-hidden className="shrink-0 text-slate-300">
                  ·
                </span>
                <span className="truncate">{row.tests_summary}</span>
              </>
            ) : null}
          </span>
        </span>

        <span className="flex flex-col items-start gap-0.5 cl-meta leading-tight text-slate-600">
          <span className="tabular-nums">
            {formatHospitalDate(row.request_date, "—")}
          </span>
          <span className="tabular-nums text-slate-400">
            {ageSexLabel(row.patient)}
          </span>
        </span>

        <span className="flex justify-start">
          <LabPriorityPill
            priority={row.priority}
            label={row.priority_label}
            compact
          />
        </span>

        <span className="flex justify-end">
          <LabStatusPill status={row.status} label={row.status_label} compact />
        </span>
      </button>
    </li>
  );
}

export default function LabQueue({
  rows,
  selectedId,
  loading,
  error,
  truncated,
  onSelect,
}: {
  rows: LabQueueRow[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  truncated: boolean;
  onSelect: (requestId: number) => void;
}) {
  const listRef = useRef<HTMLUListElement>(null);

  /**
   * Arrow keys walk the queue; Home/End jump to its ends. Handled on the list
   * container rather than per row so the behaviour survives re-sorting, and so
   * a technician can hold the key down without focus thrashing.
   */
  function handleKeyDown(event: React.KeyboardEvent<HTMLUListElement>) {
    const keys = ["ArrowDown", "ArrowUp", "Home", "End"];
    if (!keys.includes(event.key) || rows.length === 0) return;
    event.preventDefault();

    const current = rows.findIndex((row) => row.id === selectedId);
    let next = current;

    if (event.key === "ArrowDown")
      next = current < 0 ? 0 : Math.min(current + 1, rows.length - 1);
    if (event.key === "ArrowUp") next = current < 0 ? 0 : Math.max(current - 1, 0);
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = rows.length - 1;

    const target = rows[next];
    if (target) {
      onSelect(target.id);
      // Focus follows selection so the next keystroke lands here, not on body.
      requestAnimationFrame(() => {
        listRef.current
          ?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]')
          ?.focus();
      });
    }
  }

  const pendingTests = rows.reduce((total, row) => total + row.test_count, 0);

  return (
    <section className="flex min-h-[340px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      <header className="flex h-9 shrink-0 items-center justify-between border-b border-slate-200 bg-slate-50 px-3">
        <h2 className="flex items-baseline gap-1.5 cl-secondary font-bold uppercase tracking-[0.08em] text-slate-700">
          Bench Queue
          <span className="rounded bg-slate-200 px-1.5 py-px cl-meta tabular-nums text-slate-700">
            {rows.length}
          </span>
        </h2>
        {loading ? (
          <span className="cl-meta tabular-nums text-slate-500">Updating…</span>
        ) : null}
      </header>

      <div
        className={`${GRID} shrink-0 border-b border-slate-200 bg-slate-50 py-1 pl-3 pr-2 cl-micro font-bold uppercase tracking-[0.06em] text-slate-400`}
      >
        <span>Patient · Request · Tests</span>
        <span>Ordered</span>
        <span>Pri</span>
        <span className="text-right">Stage</span>
      </div>

      {error ? (
        <div className="border-b border-red-200 bg-red-50 px-3 py-2 cl-body text-red-800">
          {error}
        </div>
      ) : null}
      {truncated ? (
        <div className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 cl-secondary text-amber-900">
          Queue limit reached. More matching requests may exist. Narrow the date
          or search by patient, chart number or request code.
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading && rows.length === 0 ? (
          <div aria-label="Loading bench queue">
            {Array.from({ length: 10 }, (_, index) => (
              <div
                key={index}
                className="flex min-h-[54px] items-center gap-2 border-b border-slate-100 px-3"
              >
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
            <p className="cl-body font-semibold text-slate-600">
              No requests here
            </p>
            <p className="cl-secondary text-slate-500">
              {truncated
                ? "No matches in the requests checked. Narrow the date or search."
                : "Nothing matches the current lane and filters."}
            </p>
          </div>
        ) : (
          <ul
            ref={listRef}
            onKeyDown={handleKeyDown}
            aria-label="Laboratory bench queue. Use arrow keys to move between requests."
          >
            {rows.map((row) => (
              <QueueRow
                key={row.id}
                row={row}
                selected={selectedId === row.id}
                onSelect={() => onSelect(row.id)}
              />
            ))}
          </ul>
        )}
      </div>

      <footer className="flex h-7 shrink-0 items-center gap-2 border-t border-slate-200 bg-slate-50 px-3 cl-meta text-slate-500">
        <span className="tabular-nums">
          {rows.length} {rows.length === 1 ? "request" : "requests"}
        </span>
        <span aria-hidden className="text-slate-300">
          ·
        </span>
        <span className="tabular-nums">{testCountLabel(pendingTests)}</span>
        <span aria-hidden className="text-slate-300">
          ·
        </span>
        <span className="flex items-center gap-1">
          <kbd className="rounded border border-slate-200 bg-white px-1 font-mono cl-micro text-slate-500">
            ↑↓
          </kbd>
          to move
        </span>
      </footer>
    </section>
  );
}

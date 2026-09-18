"use client";

import { useEffect, useRef } from "react";

import { formatHospitalDate } from "@/lib/clinical-format";
import {
  ageSexLabel,
  examCellLabel,
  isUrgentPriority,
  orDash,
  studyCountLabel,
} from "@/lib/rad-desk-format";
import type { RadQueueRow } from "@/types/rad-desk";

import { RadLanePill, RadPriorityPill } from "./rad-status-pill";

/**
 * ONE grid template for the header and every row, so the two cannot drift.
 *
 * Left to right, what a radiographer scans: whose study and which one, the
 * modality and body part (which room and which protocol), when it was ordered,
 * how urgent, and where it stands. The patient owns the free space because it
 * is the scan target; request code, chart number, exam and ordering doctor sit
 * beneath it in a quieter register.
 */
const GRID =
  "grid grid-cols-[minmax(0,1fr)_78px_58px_44px_52px] items-center gap-x-2";

function QueueRow({
  row,
  selected,
  onSelect,
}: {
  row: RadQueueRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  const urgent = isUrgentPriority(row.priority);

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
        // Roving tabindex: the list is one tab stop, arrow keys move within it.
        tabIndex={selected ? 0 : -1}
        className={`${GRID} relative min-h-[60px] w-full border-b border-slate-100 py-1.5 pl-3 pr-2 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-teal-600 ${
          selected ? "bg-teal-50/60" : "bg-white hover:bg-slate-50"
        }`}
      >
        <span
          aria-hidden
          className={`absolute inset-y-0 left-0 w-[3px] ${
            selected ? "bg-teal-600" : "bg-transparent"
          }`}
        />

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
            {row.result_conflict ? (
              <span className="shrink-0 rounded border border-red-300 bg-red-50 px-1 cl-micro font-bold text-red-800">
                2+ REPORTS
              </span>
            ) : null}
          </span>
          <span className="flex min-w-0 items-center gap-1 cl-meta leading-tight text-slate-500">
            <span className="shrink-0 font-mono font-semibold text-slate-600">
              {row.request_code}
            </span>
            <span aria-hidden className="shrink-0 text-slate-300">
              ·
            </span>
            <span className="shrink-0 font-mono">{row.patient?.mrn ?? "—"}</span>
            <span aria-hidden className="shrink-0 text-slate-300">
              ·
            </span>
            <span className="truncate">{examCellLabel(row)}</span>
          </span>
          <span className="truncate cl-meta leading-tight text-slate-400">
            {row.ordering_physician?.name ?? "No ordering doctor"}
          </span>
        </span>

        <span className="flex min-w-0 flex-col items-start gap-0.5 cl-meta leading-tight">
          <span className="truncate font-semibold text-slate-700">
            {orDash(row.modality_label)}
          </span>
          <span className="truncate text-slate-400">{orDash(row.body_part)}</span>
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
          <RadPriorityPill priority={row.priority} label={row.priority_label} compact />
        </span>

        <span className="flex justify-end">
          <RadLanePill lane={row.lane} label={row.lane_label} compact />
        </span>
      </button>
    </li>
  );
}

export default function RadQueue({
  rows,
  selectedId,
  loading,
  error,
  truncated,
  onSelect,
}: {
  rows: RadQueueRow[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  truncated: boolean;
  onSelect: (requestId: number) => void;
}) {
  const listRef = useRef<HTMLUListElement>(null);

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
      requestAnimationFrame(() => {
        listRef.current
          ?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]')
          ?.focus();
      });
    }
  }

  const studies = rows.reduce((total, row) => total + row.exam_count, 0);

  return (
    <section className="flex min-h-[340px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      <header className="flex h-9 shrink-0 items-center justify-between border-b border-slate-200 bg-slate-50 px-3">
        <h2 className="flex items-baseline gap-1.5 cl-secondary font-bold uppercase tracking-[0.08em] text-slate-700">
          Imaging Queue
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
        <span>Patient · Request · Exam · Doctor</span>
        <span>Modality</span>
        <span>Ordered</span>
        <span>Pri</span>
        <span className="text-right">Lane</span>
      </div>

      {error ? (
        <div role="alert" className="border-b border-red-200 bg-red-50 px-3 py-2 cl-body text-red-800">
          {error}
        </div>
      ) : null}
      {truncated ? (
        <div className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 cl-secondary text-amber-900">
          Queue limit reached. More matching requests may exist. Narrow the date,
          modality or search.
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading && rows.length === 0 ? (
          <div aria-label="Loading imaging queue">
            {Array.from({ length: 10 }, (_, index) => (
              <div
                key={index}
                className="flex min-h-[60px] items-center gap-2 border-b border-slate-100 px-3"
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
            <p className="cl-body font-semibold text-slate-600">No requests here</p>
            <p className="cl-secondary text-slate-500">
              {error
                ? "The queue could not be loaded."
                : truncated
                  ? "No matches in the requests checked. Narrow the filters."
                  : "Nothing matches the current lane and filters."}
            </p>
          </div>
        ) : (
          <ul
            ref={listRef}
            onKeyDown={handleKeyDown}
            aria-label="Radiology imaging queue. Use arrow keys to move between requests."
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
        <span className="tabular-nums">{studyCountLabel(studies)}</span>
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

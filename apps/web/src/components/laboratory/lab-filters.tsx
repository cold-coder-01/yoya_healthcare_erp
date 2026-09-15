"use client";

import {
  LAB_DESK_ACTIVE_STATUS_ORDER,
  LAB_DESK_STATUS_ORDER,
  labStatusCode,
  labStatusLabel,
} from "@/lib/lab-desk-format";

/**
 * The bench filter strip: status lane, date, search, refresh.
 *
 * THE STATUS LANE IS THE PRIMARY CONTROL, so it sits first and reads as tabs
 * rather than as a dropdown -- a technician switches between "what can I draw"
 * and "what am I running" constantly, and a select box makes that two clicks
 * and a read.
 *
 * "Active" is the default lane and means exactly LAB_DESK_ACTIVE_STATUS_ORDER,
 * which the server also sends as `meta.default_statuses`. Draft, Completed and
 * Cancelled are reachable individually but never crowd the working queue.
 *
 * THE DATE IS OPTIONAL AND EMPTY BY DEFAULT, unlike the Doctor and Cashier
 * desks. Bench work does not expire at midnight: a specimen ordered yesterday
 * is still uncollected this morning, and defaulting to today would hide it.
 */
const LANES: ReadonlyArray<{ key: string; label: string; statuses: readonly string[] }> =
  [
    {
      key: "active",
      label: "Active bench",
      statuses: LAB_DESK_ACTIVE_STATUS_ORDER,
    },
    ...LAB_DESK_STATUS_ORDER.map((status) => ({
      key: status,
      label: labStatusLabel(status),
      statuses: [status] as readonly string[],
    })),
  ];

export function laneStatuses(laneKey: string): readonly string[] {
  return (
    LANES.find((lane) => lane.key === laneKey)?.statuses ??
    LAB_DESK_ACTIVE_STATUS_ORDER
  );
}

export default function LabFilters({
  lane,
  date,
  search,
  loading,
  counts,
  onLaneChange,
  onDateChange,
  onSearchChange,
  onRefresh,
}: {
  lane: string;
  date: string;
  search: string;
  loading: boolean;
  /** Counts over the rows the server returned, for the single-status lanes. */
  counts: Record<string, number>;
  onLaneChange: (lane: string) => void;
  onDateChange: (date: string) => void;
  onSearchChange: (search: string) => void;
  onRefresh: () => void;
}) {
  return (
    <div className="flex shrink-0 flex-col gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-sm">
      {/* Status lane. Scrolls horizontally on a narrow screen rather than
          wrapping into a second row that pushes the queue down. */}
      <div
        role="tablist"
        aria-label="Laboratory queue lane"
        className="flex min-w-0 items-center gap-1 overflow-x-auto"
      >
        {LANES.map((entry) => {
          const active = entry.key === lane;
          // Only a single-status lane has a meaningful count in `counts`; the
          // "Active bench" lane is the sum of four and is left uncounted rather
          // than shown as a number that means something different.
          const count = entry.statuses.length === 1 ? counts[entry.key] : undefined;
          return (
            <button
              key={entry.key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onLaneChange(entry.key)}
              className={`flex shrink-0 items-center gap-1.5 rounded border px-2 py-1 cl-secondary font-bold transition-colors ${
                active
                  ? "border-indigo-600 bg-indigo-50 text-indigo-900"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              <span className="font-mono cl-micro text-slate-400">
                {entry.key === "active" ? "ALL" : labStatusCode(entry.key)}
              </span>
              {entry.label}
              {typeof count === "number" ? (
                <span className="rounded bg-slate-100 px-1 cl-micro tabular-nums text-slate-600">
                  {count}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 cl-meta font-bold uppercase tracking-[0.06em] text-slate-500">
          Ordered
          <input
            type="date"
            value={date}
            onChange={(event) => onDateChange(event.target.value)}
            className="rounded border border-slate-200 px-2 py-1 cl-secondary font-normal normal-case tracking-normal text-slate-800 outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
          />
        </label>
        {date ? (
          <button
            type="button"
            onClick={() => onDateChange("")}
            className="rounded border border-slate-200 px-2 py-1 cl-secondary font-semibold text-slate-600 hover:bg-slate-50"
          >
            Any day
          </button>
        ) : (
          <span className="cl-meta text-slate-400">All outstanding days</span>
        )}

        <label className="flex min-w-0 flex-1 items-center gap-1.5">
          <span className="sr-only">
            Search by patient, chart number or request code
          </span>
          <input
            type="search"
            value={search}
            placeholder="Patient, chart no. or request code…"
            onChange={(event) => onSearchChange(event.target.value)}
            className="min-w-[180px] flex-1 rounded border border-slate-200 px-2 py-1 cl-secondary text-slate-800 outline-none placeholder:text-slate-400 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
          />
        </label>

        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="rounded border border-slate-300 bg-white px-2.5 py-1 cl-secondary font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
    </div>
  );
}

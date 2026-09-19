"use client";

import {
  ACTIVE_LANE_KEY,
  PHARMACY_LANE_ORDER,
  laneCountLabel,
  pharmacyLaneCode,
  pharmacyLaneLabel,
} from "@/lib/pharmacy-desk-format";
import type { PharmacyWorklistSummary } from "@/types/pharmacy-desk";

/**
 * The Pharmacy Desk filter strip: lane, date, search, refresh.
 *
 * THE LANE IS THE PRIMARY CONTROL: "All active" first, then each lane in
 * workflow order, then Completed and Cancelled. Every badge is the SERVER's
 * count over the date + search scope; nothing is recounted here, and an
 * unknown count is a dash, never a zero.
 *
 * THE STRIP WRAPS rather than scrolling, so no lane can be left scrolled out of
 * view -- the Radiology Desk's cut-off-tab defect cannot happen here.
 */
const LANES: readonly string[] = [ACTIVE_LANE_KEY, ...PHARMACY_LANE_ORDER];

export default function PharmacyFilters({
  lane,
  date,
  search,
  loading,
  summary,
  roleLabel,
  onLaneChange,
  onDateChange,
  onSearchChange,
  onRefresh,
}: {
  lane: string;
  date: string;
  search: string;
  loading: boolean;
  summary: PharmacyWorklistSummary | null;
  /** The signed-in user's Pharmacy Desk role, from the session. */
  roleLabel: string | null;
  onLaneChange: (lane: string) => void;
  onDateChange: (date: string) => void;
  onSearchChange: (search: string) => void;
  onRefresh: () => void;
}) {
  return (
    <div className="flex shrink-0 flex-col gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-sm">
      <div role="tablist" aria-label="Pharmacy queue lane" className="flex flex-wrap items-center gap-1">
        {LANES.map((key) => {
          const active = key === lane;
          const alarm = key === "anomaly" || key === "blocked";
          return (
            <button
              key={key}
              data-lane={key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onLaneChange(key)}
              className={`flex shrink-0 items-center gap-1.5 rounded border px-2 py-1 cl-secondary font-bold transition-colors ${
                active
                  ? alarm
                    ? "border-red-600 bg-red-50 text-red-900"
                    : "border-violet-600 bg-violet-50 text-violet-900"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              <span className="font-mono cl-micro text-slate-400">{pharmacyLaneCode(key)}</span>
              {pharmacyLaneLabel(key)}
              <span
                className={`rounded px-1 cl-micro tabular-nums ${
                  active ? "bg-violet-100 text-violet-900" : "bg-slate-100 text-slate-600"
                }`}
              >
                {laneCountLabel(summary, key)}
              </span>
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 cl-meta font-bold uppercase tracking-[0.06em] text-slate-500">
          Dispense date
          <input
            type="date"
            value={date}
            onChange={(event) => onDateChange(event.target.value)}
            className="rounded border border-slate-200 px-2 py-1 cl-secondary font-normal normal-case tracking-normal text-slate-800 outline-none focus:border-violet-600 focus:ring-1 focus:ring-violet-600"
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
          <span className="cl-meta text-slate-400">All days</span>
        )}

        <label className="flex min-w-0 flex-1 items-center gap-1.5">
          <span className="sr-only">
            Search by patient, chart number, dispense, prescription, doctor or medicine
          </span>
          <input
            type="search"
            value={search}
            placeholder="Patient, chart no., dispense, Rx, doctor or medicine…"
            onChange={(event) => onSearchChange(event.target.value)}
            className="min-w-[180px] flex-1 rounded border border-slate-200 px-2 py-1 cl-secondary text-slate-800 outline-none placeholder:text-slate-400 focus:border-violet-600 focus:ring-1 focus:ring-violet-600"
          />
        </label>

        {roleLabel ? (
          <span className="rounded bg-violet-50 px-1.5 py-0.5 cl-meta font-semibold text-violet-900">
            {roleLabel}
          </span>
        ) : null}

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

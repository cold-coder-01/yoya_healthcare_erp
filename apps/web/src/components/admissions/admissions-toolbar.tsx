"use client";

import {
  ACTIVE_LANE_KEY,
  ADMISSION_LANE_ORDER,
  ALL_LANE_KEY,
  laneCode,
  laneLabel,
} from "@/lib/admissions-desk-format";
import type { AdmissionLaneSummary } from "@/types/admissions-desk";

const TABS: readonly string[] = [ACTIVE_LANE_KEY, ...ADMISSION_LANE_ORDER, ALL_LANE_KEY];

function tabCount(summary: AdmissionLaneSummary | null, lane: string): string {
  if (!summary) return "–";
  const key = lane === ALL_LANE_KEY ? "total" : lane;
  const value = summary[key as keyof AdmissionLaneSummary];
  // null means the server declined to count (scan cap): a dash, never a guess.
  return value === null || value === undefined ? "–" : String(value);
}

/**
 * Lane tabs with the SERVER's counts, search and refresh. The counts describe
 * the whole ward + search scope and do not change when a lane is selected.
 */
export default function AdmissionsToolbar({
  lane,
  search,
  summary,
  loading,
  roleLabel,
  scopeText,
  onLaneChange,
  onSearchChange,
  onRefresh,
}: {
  lane: string;
  search: string;
  summary: AdmissionLaneSummary | null;
  loading: boolean;
  roleLabel: string | null;
  scopeText: string;
  onLaneChange: (lane: string) => void;
  onSearchChange: (value: string) => void;
  onRefresh: () => void;
}) {
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white px-2 py-1.5">
      <div role="tablist" aria-label="Admission lanes" className="flex flex-wrap items-center gap-1">
        {TABS.map((tab) => {
          const selected = lane === tab;
          return (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => onLaneChange(tab)}
              title={laneLabel(tab)}
              className={`flex items-center gap-1.5 rounded-md border px-2 py-1 cl-meta font-bold ${
                selected
                  ? "border-sky-700 bg-sky-700 text-white"
                  : tab === "needs_review" && summary && (summary.needs_review ?? 0) > 0
                    ? "border-red-300 bg-red-50 text-red-800 hover:bg-red-100"
                    : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
              }`}
            >
              <span className="font-mono">{laneCode(tab)}</span>
              <span className="hidden min-[1400px]:inline">{laneLabel(tab)}</span>
              <span
                className={`min-w-[1.5rem] rounded px-1 text-center font-mono tabular-nums ${
                  selected ? "bg-white/20" : "bg-slate-100 text-slate-700"
                }`}
              >
                {tabCount(summary, tab)}
              </span>
            </button>
          );
        })}
      </div>

      <div className="ml-auto flex items-center gap-2">
        <input
          type="search"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Patient, MRN, ADM ref, doctor, bed, room, ward"
          aria-label="Search admissions"
          className="h-8 w-[300px] rounded-md border border-slate-300 bg-white px-2 cl-body outline-none focus:border-sky-600 focus:ring-2 focus:ring-sky-600/20"
        />
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="h-8 rounded-md border border-slate-300 bg-white px-3 cl-meta font-bold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
        <span className="hidden flex-col items-end leading-tight min-[1200px]:flex">
          {roleLabel ? <span className="cl-micro font-bold uppercase tracking-wide text-slate-600">{roleLabel}</span> : null}
          <span className="cl-micro text-slate-500">{scopeText}</span>
        </span>
      </div>
    </div>
  );
}

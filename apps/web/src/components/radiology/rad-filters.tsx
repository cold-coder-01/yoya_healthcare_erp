"use client";

import { useEffect, useRef } from "react";

import {
  ACTIVE_LANE_KEY,
  RAD_DESK_LANE_ORDER,
  laneStripScrollFor,
  radLaneCode,
  radLaneLabel,
  laneCountLabel,
} from "@/lib/rad-desk-format";
import type { RadModalityOption, RadWorklistSummary } from "@/types/rad-desk";

/**
 * The Radiology Desk filter strip: lane, date, modality, search, refresh.
 *
 * THE LANE IS THE PRIMARY CONTROL and reads as tabs: "All active" first, then
 * each lane in workflow order, then Completed and Cancelled at the end. Every
 * badge is the SERVER's count over the date + search + modality scope; nothing
 * is recounted here, and an unknown count is a dash, never a zero.
 *
 * THE DATE IS OPTIONAL AND EMPTY BY DEFAULT: imaging work ordered yesterday is
 * still today's work. It filters the ORDERING day -- the model has no scheduled
 * date, so none is offered.
 *
 * THE MODALITY LIST COMES FROM THE SERVER (`meta.modalities`), which reads it
 * from hospital.radiology.exam's own selection. No list is maintained here.
 */
const LANES: readonly string[] = [ACTIVE_LANE_KEY, ...RAD_DESK_LANE_ORDER];

export default function RadFilters({
  lane,
  date,
  modality,
  search,
  modalities,
  loading,
  summary,
  roleLabel,
  onLaneChange,
  onDateChange,
  onModalityChange,
  onSearchChange,
  onRefresh,
}: {
  lane: string;
  date: string;
  modality: string;
  search: string;
  modalities: RadModalityOption[];
  loading: boolean;
  summary: RadWorklistSummary | null;
  /** The signed-in user's Radiology Desk role, from the session. */
  roleLabel: string | null;
  onLaneChange: (lane: string) => void;
  onDateChange: (date: string) => void;
  onModalityChange: (modality: string) => void;
  onSearchChange: (search: string) => void;
  onRefresh: () => void;
}) {
  const stripRef = useRef<HTMLDivElement>(null);

  /*
    KEEP THE SELECTED LANE -- AND THEREFORE THE FIRST LANE -- IN VIEW.

    The strip scrolls horizontally when its ten tabs do not fit. Its offset
    used to be left entirely to the browser, which is how the desk came to open
    with "All active" hidden and "Awaiting clearance" cut to "…ng clearance":
    after reaching Completed or Cancelled the strip stayed scrolled, and Back
    restored it.

    Runs on mount, on every lane change, and when the page is shown again from
    the back/forward cache. It writes ONLY this element's scrollLeft -- never
    scrollIntoView, which would also scroll the page and the panels -- and it
    sets no React state. Scrolling by hand still works exactly as before.
  */
  useEffect(() => {
    function revealSelectedLane() {
      const strip = stripRef.current;
      if (!strip) return;
      const tab = strip.querySelector<HTMLElement>(`[data-lane="${lane}"]`);
      if (!tab) {
        strip.scrollLeft = 0;
        return;
      }
      const stripBox = strip.getBoundingClientRect();
      const tabBox = tab.getBoundingClientRect();
      strip.scrollLeft = laneStripScrollFor({
        scrollLeft: strip.scrollLeft,
        clientWidth: strip.clientWidth,
        scrollWidth: strip.scrollWidth,
        tabLeft: tabBox.left - stripBox.left + strip.scrollLeft,
        tabWidth: tabBox.width,
        isFirst: lane === LANES[0],
      });
    }

    revealSelectedLane();
    window.addEventListener("pageshow", revealSelectedLane);
    return () => window.removeEventListener("pageshow", revealSelectedLane);
  }, [lane]);

  return (
    <div className="flex shrink-0 flex-col gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-sm">
      <div
        ref={stripRef}
        role="tablist"
        aria-label="Radiology queue lane"
        className="flex min-w-0 items-center gap-1 overflow-x-auto"
      >
        {LANES.map((key) => {
          const active = key === lane;
          const anomaly = key === "anomaly";
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
                  ? anomaly
                    ? "border-red-600 bg-red-50 text-red-900"
                    : "border-teal-600 bg-teal-50 text-teal-900"
                  : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
              }`}
            >
              <span className="font-mono cl-micro text-slate-400">
                {radLaneCode(key)}
              </span>
              {radLaneLabel(key)}
              <span
                className={`rounded px-1 cl-micro tabular-nums ${
                  active ? "bg-teal-100 text-teal-900" : "bg-slate-100 text-slate-600"
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
          Ordered
          <input
            type="date"
            value={date}
            onChange={(event) => onDateChange(event.target.value)}
            className="rounded border border-slate-200 px-2 py-1 cl-secondary font-normal normal-case tracking-normal text-slate-800 outline-none focus:border-teal-600 focus:ring-1 focus:ring-teal-600"
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

        <label className="flex items-center gap-1.5 cl-meta font-bold uppercase tracking-[0.06em] text-slate-500">
          Modality
          <select
            value={modality}
            onChange={(event) => onModalityChange(event.target.value)}
            className="rounded border border-slate-200 bg-white px-2 py-1 cl-secondary font-normal normal-case tracking-normal text-slate-800 outline-none focus:border-teal-600 focus:ring-1 focus:ring-teal-600"
          >
            <option value="">All modalities</option>
            {modalities.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex min-w-0 flex-1 items-center gap-1.5">
          <span className="sr-only">
            Search by patient, chart number, request code, exam or doctor
          </span>
          <input
            type="search"
            value={search}
            placeholder="Patient, chart no., request, exam or doctor…"
            onChange={(event) => onSearchChange(event.target.value)}
            className="min-w-[180px] flex-1 rounded border border-slate-200 px-2 py-1 cl-secondary text-slate-800 outline-none placeholder:text-slate-400 focus:border-teal-600 focus:ring-1 focus:ring-teal-600"
          />
        </label>

        {roleLabel ? (
          <span className="rounded bg-teal-50 px-1.5 py-0.5 cl-meta font-semibold text-teal-900">
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

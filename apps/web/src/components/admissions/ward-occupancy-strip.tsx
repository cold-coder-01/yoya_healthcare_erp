"use client";

import type { WardSummary } from "@/types/admissions-desk";

/**
 * One compact segment per ward: occupied / available / out of service, plus a
 * review marker. Clicking a ward filters the census AND the bed board; clicking
 * it again (or "All wards") clears the filter.
 *
 * The numbers are the server's rollups, which are summed from the same bed
 * rows the bed board shows -- never recounted here.
 */
export default function WardOccupancyStrip({
  wards,
  selectedWardId,
  loading,
  error,
  onSelect,
}: {
  wards: WardSummary[];
  selectedWardId: number | null;
  loading: boolean;
  error: string | null;
  onSelect: (wardId: number | null) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Ward occupancy"
      className="flex shrink-0 items-stretch gap-1.5 overflow-x-auto rounded-lg border border-slate-200 bg-white p-1.5"
    >
      <button
        type="button"
        onClick={() => onSelect(null)}
        aria-pressed={selectedWardId === null}
        className={`shrink-0 rounded-md border px-2.5 py-1 text-left cl-meta font-bold ${
          selectedWardId === null
            ? "border-sky-600 bg-sky-50 text-sky-900"
            : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
        }`}
      >
        All wards
      </button>

      {error ? (
        <span className="self-center px-2 cl-meta text-red-700">{error}</span>
      ) : null}
      {!error && loading && wards.length === 0 ? (
        <span className="self-center px-2 cl-meta text-slate-400">Loading wards…</span>
      ) : null}
      {!error && !loading && wards.length === 0 ? (
        <span className="self-center px-2 cl-meta text-slate-500">No wards are configured.</span>
      ) : null}

      {wards.map((ward) => {
        const selected = selectedWardId === ward.id;
        const outOfService = ward.cleaning_count + ward.maintenance_count + ward.blocked_count;
        return (
          <button
            key={ward.id}
            type="button"
            onClick={() => onSelect(selected ? null : ward.id)}
            aria-pressed={selected}
            title={ward.department ? `${ward.name} — ${ward.department.name}` : (ward.name ?? "")}
            className={`flex min-w-[190px] shrink-0 flex-col gap-0.5 rounded-md border px-2.5 py-1 text-left ${
              selected ? "border-sky-600 bg-sky-50" : "border-slate-200 bg-white hover:bg-slate-50"
            } ${ward.active ? "" : "opacity-60"}`}
          >
            <span className="flex items-center gap-1.5">
              <span className="truncate cl-body font-bold text-slate-900">{ward.name}</span>
              {ward.code ? (
                <span className="shrink-0 font-mono cl-micro text-slate-500">{ward.code}</span>
              ) : null}
              {ward.needs_review_count > 0 ? (
                <span className="ml-auto shrink-0 rounded border border-red-300 bg-red-50 px-1 cl-micro font-bold text-red-800">
                  REVIEW {ward.needs_review_count}
                </span>
              ) : null}
            </span>
            <span className="flex items-center gap-2 font-mono cl-meta tabular-nums text-slate-700">
              <span>
                <span className="font-bold text-sky-900">{ward.occupied_count}</span> occ
              </span>
              <span>
                <span className="font-bold text-emerald-800">{ward.available_count}</span> avail
              </span>
              <span>
                <span className="font-bold text-orange-800">{outOfService}</span> oos
              </span>
              <span className="text-slate-400">/ {ward.bed_count}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

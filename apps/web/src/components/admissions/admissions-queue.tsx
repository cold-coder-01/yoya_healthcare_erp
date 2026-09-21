"use client";

import { useEffect, useRef } from "react";

import {
  ageSexLabel,
  billingMarker,
  formatLengthOfStay,
  locationLabel,
  orDash,
} from "@/lib/admissions-desk-format";
import type { AdmissionWorklistRow } from "@/types/admissions-desk";

import AdmissionLanePill from "./admission-lane-pill";

/**
 * ONE grid template for the header and every row, so the two cannot drift.
 *
 * Left to right, what an admissions clerk scans: who, where, how long, which
 * lane. The patient owns the free space; the reference, MRN and doctor sit
 * beneath in a quieter register. Review and clearance markers are TEXT.
 */
const GRID = "grid grid-cols-[minmax(0,1fr)_minmax(0,150px)_52px_52px] items-center gap-x-2";

function QueueRow({
  row,
  selected,
  onSelect,
}: {
  row: AdmissionWorklistRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (selected) ref.current?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  const marker = billingMarker(row.billing_blocked);

  return (
    <li>
      <button
        ref={ref}
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className={`${GRID} relative min-h-[54px] w-full border-b border-slate-100 py-1.5 pl-3 pr-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-600 ${
          selected ? "bg-sky-50/70" : "bg-white hover:bg-slate-50"
        }`}
      >
        <span
          aria-hidden
          className={`absolute inset-y-0 left-0 w-[3px] ${selected ? "bg-sky-700" : row.lane === "needs_review" ? "bg-red-500" : "bg-transparent"}`}
        />
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate cl-strong font-bold leading-tight text-slate-900">
              {row.patient?.name ?? "—"}
            </span>
            {row.lane === "needs_review" ? (
              <span className="shrink-0 rounded border border-red-300 bg-red-50 px-1 cl-micro font-bold text-red-800">
                REVIEW
              </span>
            ) : null}
            {marker === "CLEARANCE" ? (
              <span className="shrink-0 rounded border border-amber-300 bg-amber-50 px-1 cl-micro font-bold text-amber-900">
                CLEARANCE
              </span>
            ) : null}
          </span>
          <span className="flex min-w-0 items-center gap-1.5 truncate font-mono cl-meta text-slate-500">
            <span>{row.reference}</span>
            <span aria-hidden>·</span>
            <span>{orDash(row.patient?.mrn)}</span>
            <span aria-hidden>·</span>
            <span>{ageSexLabel(row.patient)}</span>
            {row.physician ? (
              <>
                <span aria-hidden>·</span>
                <span className="truncate font-sans">{row.physician.name}</span>
              </>
            ) : null}
          </span>
        </span>
        <span className="truncate font-mono cl-meta text-slate-700" title={locationLabel(row.location)}>
          {locationLabel(row.location)}
        </span>
        <span className="text-right font-mono cl-meta tabular-nums text-slate-700">
          {formatLengthOfStay(row.length_of_stay)}
        </span>
        <span className="flex justify-end">
          <AdmissionLanePill lane={row.lane} compact />
        </span>
      </button>
    </li>
  );
}

export default function AdmissionsQueue({
  rows,
  selectedId,
  loading,
  error,
  truncated,
  emptyMessage,
  onSelect,
}: {
  rows: AdmissionWorklistRow[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  truncated: boolean;
  emptyMessage: string;
  onSelect: (admissionId: number) => void;
}) {
  return (
    <section
      aria-label="Admissions census"
      className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white"
    >
      <div className={`${GRID} shrink-0 border-b border-slate-200 bg-slate-50 py-1 pl-3 pr-2 cl-micro font-bold uppercase tracking-wide text-slate-500`}>
        <span>Patient</span>
        <span>Location</span>
        <span className="text-right">LOS</span>
        <span className="text-right">Lane</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <p className="px-3 py-4 cl-body text-red-700">{error}</p>
        ) : loading && rows.length === 0 ? (
          <p className="px-3 py-4 cl-body text-slate-400">Loading census…</p>
        ) : rows.length === 0 ? (
          <p className="px-3 py-6 text-center cl-body text-slate-500">{emptyMessage}</p>
        ) : (
          <ul>
            {rows.map((row) => (
              <QueueRow
                key={row.id}
                row={row}
                selected={row.id === selectedId}
                onSelect={() => onSelect(row.id)}
              />
            ))}
          </ul>
        )}
      </div>
      {truncated ? (
        <p className="shrink-0 border-t border-amber-200 bg-amber-50 px-3 py-1 cl-meta text-amber-900">
          More admissions match than are shown. Narrow by ward or search.
        </p>
      ) : null}
    </section>
  );
}

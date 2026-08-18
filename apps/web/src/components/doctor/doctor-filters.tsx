"use client";

import { useEffect, useRef } from "react";

const CONTROL =
  "h-7 rounded border border-slate-300 bg-white px-2 text-[12px] text-slate-900 outline-none focus:border-emerald-600 focus:ring-1 focus:ring-emerald-600";

/**
 * Date, search and refresh. Deliberately four controls, not ten.
 *
 * There is no doctor picker here, unlike the Front Desk filter bar: this queue
 * is already restricted to the signed-in doctor's own patients by the Odoo
 * scope domain, so a doctor selector would be a control that either does
 * nothing or implies it can show someone else's list.
 */
export default function DoctorFilters({
  date,
  search,
  loading,
  onDateChange,
  onSearchChange,
  onRefresh,
}: {
  date: string;
  search: string;
  loading: boolean;
  onDateChange: (value: string) => void;
  onSearchChange: (value: string) => void;
  onRefresh: () => void;
}) {
  const searchRef = useRef<HTMLInputElement>(null);

  // "/" focuses search, the convention every dense worklist tool shares.
  // Ignored while typing so it can still be entered into a field.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      event.preventDefault();
      searchRef.current?.focus();
      searchRef.current?.select();
    }

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <section className="flex shrink-0 flex-wrap items-center gap-2 rounded border border-slate-200 bg-white px-2 py-1.5 shadow-sm">
      <label className="flex items-center gap-1.5">
        <span className="text-[10px] font-bold uppercase tracking-wide text-slate-500">
          Date
        </span>
        <input
          type="date"
          value={date}
          onChange={(event) => onDateChange(event.target.value)}
          className={`${CONTROL} tabular-nums`}
        />
      </label>

      <label className="flex min-w-[180px] flex-1 items-center gap-1.5">
        <span className="sr-only">Search this queue by name or chart number</span>
        <input
          ref={searchRef}
          type="search"
          value={search}
          placeholder="Filter by name or chart no.   ( / )"
          onChange={(event) => onSearchChange(event.target.value)}
          className={`${CONTROL} w-full`}
        />
      </label>

      <button
        type="button"
        onClick={onRefresh}
        disabled={loading}
        className="h-7 rounded border border-slate-300 bg-white px-2.5 text-[11px] font-bold uppercase tracking-wide text-slate-700 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:opacity-50"
      >
        {loading ? "Refreshing…" : "Refresh"}
      </button>
    </section>
  );
}

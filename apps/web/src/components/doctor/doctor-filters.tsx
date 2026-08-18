"use client";

import { useEffect, useRef } from "react";

/**
 * ONE control height (32px) and one focus treatment across the row, so the
 * date field, the search box and the refresh button line up on both edges
 * instead of each sitting at its own height.
 */
const CONTROL =
  "h-8 rounded-md border border-slate-300 bg-white text-[12px] text-slate-900 outline-none transition-colors focus:border-emerald-600 focus:ring-2 focus:ring-emerald-600/20";

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
    <section className="flex shrink-0 flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white px-2 py-1.5 shadow-sm">
      <label className="flex items-center gap-1.5">
        <span className="text-[10px] font-bold uppercase tracking-[0.08em] text-slate-500">
          Date
        </span>
        <input
          type="date"
          value={date}
          onChange={(event) => onDateChange(event.target.value)}
          className={`${CONTROL} px-2 tabular-nums`}
        />
      </label>

      {/* The search box owns the free space on the row, which is what marks it
          as the control a doctor is meant to reach for. The icon sits inside
          the field rather than beside it so the row keeps two clean edges. */}
      <div className="relative flex min-w-[200px] flex-1 items-center">
        <label htmlFor="doctor-queue-search" className="sr-only">
          Search this queue by patient name or chart number
        </label>
        <svg
          aria-hidden
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          className="pointer-events-none absolute left-2.5 h-3.5 w-3.5 text-slate-400"
        >
          <circle cx="9" cy="9" r="6" />
          <path d="m17 17-3.5-3.5" strokeLinecap="round" />
        </svg>
        <input
          id="doctor-queue-search"
          ref={searchRef}
          type="search"
          value={search}
          placeholder="Search name or chart no."
          onChange={(event) => onSearchChange(event.target.value)}
          className={`${CONTROL} w-full pl-8 pr-9`}
        />
        {/* The "/" hint, shown as the key it actually is. Hidden once the field
            has content, where it would only crowd the text. */}
        {search ? null : (
          <kbd
            aria-hidden
            className="pointer-events-none absolute right-2 rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 font-mono text-[10px] font-semibold leading-none text-slate-400"
          >
            /
          </kbd>
        )}
      </div>

      <button
        type="button"
        onClick={onRefresh}
        disabled={loading}
        className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-300 bg-white px-3 text-[11px] font-bold uppercase tracking-wide text-slate-700 outline-none transition-colors hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <svg
          aria-hidden
          viewBox="0 0 20 20"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
        >
          <path d="M17 10a7 7 0 1 1-2.05-4.95" />
          <path d="M17 3v4h-4" />
        </svg>
        {loading ? "Refreshing…" : "Refresh"}
      </button>
    </section>
  );
}

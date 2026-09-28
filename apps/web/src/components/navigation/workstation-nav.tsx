import Link from "next/link";

import type { WorkstationNavItem } from "@/lib/reception-roles";

/**
 * Compact header tabs between sidebarless workstations ("Front Desk |
 * Admissions"). A server component: the items are computed from the session
 * in the layout, so the right tabs render on first paint with no flash.
 *
 * Renders NOTHING for an empty list, so a role offered no tabs keeps exactly
 * the header it had. Navigation only -- the server gates every route.
 */
const ACCENT = {
  emerald: "bg-emerald-50 text-emerald-800 ring-emerald-200 focus-visible:ring-emerald-600",
  sky: "bg-sky-50 text-sky-800 ring-sky-200 focus-visible:ring-sky-600",
} as const;

export default function WorkstationNav({
  items,
  accent,
}: {
  items: WorkstationNavItem[];
  accent: keyof typeof ACCENT;
}) {
  if (items.length === 0) {
    return null;
  }
  return (
    <nav aria-label="Workstations" className="flex shrink-0 items-center gap-1">
      {items.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          aria-current={item.current ? "page" : undefined}
          className={`rounded px-2 py-1 text-xs font-semibold outline-none focus-visible:ring-2 ${
            item.current
              ? `ring-1 ${ACCENT[accent]}`
              : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
          }`}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}

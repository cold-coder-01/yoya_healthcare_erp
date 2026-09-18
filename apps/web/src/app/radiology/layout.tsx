import type { ReactNode } from "react";

import { loadReceptionSession } from "@/lib/reception-session.server";

/**
 * The Radiology Desk shell: sidebarless, one brand line, matching the
 * Laboratory and Doctor desks.
 *
 * PRESENTATION ONLY: no route guard and no authorization. The Odoo role gate
 * (services/reception_scope.may_rad_desk) and the record rules behind it are the
 * only access control -- a user who reaches this URL without a Radiology Desk
 * role gets a 403 from every data endpoint the page calls, and the workstation
 * says so in words rather than rendering an empty queue.
 *
 * The TEAL keyline distinguishes this workstation from the Laboratory Desk's
 * indigo and the Doctor Desk's emerald at a glance, which matters where several
 * desks are left logged in side by side.
 */
export default async function RadiologyLayout({
  children,
}: {
  children: ReactNode;
}) {
  // Never throws; the shell cannot take the workstation down.
  const session = await loadReceptionSession();
  const brand = session?.companyName ?? "YOYA General Hospital";

  const userName = session?.userName ?? "";
  const initial = userName.trim().charAt(0).toUpperCase() || "·";

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-100 text-slate-950">
      {/* Literal hex (teal-700): Tailwind v4 does not resolve theme() inside an
          arbitrary value, and a wrong one fails silently. */}
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 shadow-[inset_0_-2px_0_#0f766e]">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="truncate text-[13px] font-bold uppercase tracking-[0.08em] text-teal-800">
            {brand}
          </span>
          <span aria-hidden className="h-4 w-px shrink-0 bg-slate-200" />
          <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em] text-slate-600">
            Radiology Desk
          </span>
        </div>

        {userName ? (
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-[12px] font-semibold text-slate-700">
              {userName}
            </span>
            <span
              aria-hidden
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-teal-700 text-[11px] font-bold text-white"
            >
              {initial}
            </span>
          </div>
        ) : null}
      </header>

      <main className="min-h-0 flex-1 overflow-hidden p-3">{children}</main>
    </div>
  );
}

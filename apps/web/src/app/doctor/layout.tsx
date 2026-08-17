import type { ReactNode } from "react";

import { loadReceptionSession } from "@/lib/reception-session.server";

/**
 * The Doctor Desk shell: sidebarless, one brand line, for the same reason the
 * Front Desk shell is. A doctor working a queue needs horizontal space more
 * than they need a navigation column whose only live link is the page they are
 * already on.
 *
 * PRESENTATION ONLY: no route guard and no authorization. Odoo record rules
 * and the clinical scope domain in yoya_emr_api remain the only access
 * control -- a user who reaches this URL without the Doctor group still gets
 * an empty queue and a 403 from every endpoint the page calls.
 *
 * This adds a route layout; it changes no existing one. ReceptionShell,
 * /front-desk and /triage are untouched.
 */
export default async function DoctorLayout({ children }: { children: ReactNode }) {
  // Never throws; the shell cannot take the workstation down.
  const session = await loadReceptionSession();
  const brand = session?.companyName ?? "YOYA General Hospital";

  return (
    <div className="flex min-h-screen flex-col bg-slate-100 text-slate-950">
      <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-3">
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="truncate text-sm font-bold uppercase tracking-wide text-emerald-800">
            {brand}
          </span>
          <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Doctor Desk
          </span>
        </span>
        <span className="truncate text-[12px] font-semibold text-slate-700">
          {session?.userName ?? ""}
        </span>
      </header>

      <main className="min-h-0 flex-1 p-3">{children}</main>
    </div>
  );
}

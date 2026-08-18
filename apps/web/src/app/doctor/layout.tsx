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

  const userName = session?.userName ?? "";
  // First letter of the signed-in user, for the identity chip. Falls back to a
  // neutral glyph rather than an empty circle when the session is unavailable.
  const initial = userName.trim().charAt(0).toUpperCase() || "·";

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-100 text-slate-950">
      {/*
        One compact strip, three zones: who the hospital is, which workstation
        this is, and who is signed in. The emerald keyline under the header ties
        the desk to the accent used for "ready" and for the primary action, so
        the screen reads as one system rather than three panels that happen to
        sit together.
      */}
      {/* Literal hex (emerald-700), matching front-desk-queue.tsx: Tailwind v4
          does not resolve theme() inside an arbitrary value, and a wrong one
          fails silently rather than at build time. */}
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 shadow-[inset_0_-2px_0_#047857]">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="truncate text-[13px] font-bold uppercase tracking-[0.08em] text-emerald-800">
            {brand}
          </span>
          <span aria-hidden className="h-4 w-px shrink-0 bg-slate-200" />
          <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em] text-slate-600">
            Doctor Desk
          </span>
        </div>

        {userName ? (
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-[12px] font-semibold text-slate-700">
              {userName}
            </span>
            <span
              aria-hidden
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-700 text-[11px] font-bold text-white"
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

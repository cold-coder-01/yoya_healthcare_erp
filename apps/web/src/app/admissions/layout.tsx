import type { ReactNode } from "react";

import AdmissionsUserMenu from "@/components/admissions/admissions-user-menu";
import FullscreenToggle from "@/components/workstation/fullscreen-toggle";
import WorkstationNav from "@/components/navigation/workstation-nav";
import { ADMISSIONS_ROUTE, frontOfHouseNavItems } from "@/lib/reception-roles";
import { hospitalBrand } from "@/lib/branding";
import { loadReceptionSession } from "@/lib/reception-session.server";

/**
 * The Admissions Desk shell: sidebarless, one brand line, matching the
 * Laboratory, Radiology, Pharmacy and Doctor desks.
 *
 * PRESENTATION ONLY: no route guard and no authorization. The Odoo role gate
 * (services/reception_scope.may_admissions_desk) and the record rules behind it
 * are the only access control -- a user who reaches this URL without an
 * Admissions Desk role gets a 403 from every data endpoint, and the workstation
 * says so in words rather than rendering an empty census.
 *
 * The SKY keyline distinguishes this workstation from the Laboratory Desk's
 * indigo, the Radiology Desk's teal, the Pharmacy Desk's violet and the Doctor
 * Desk's emerald.
 */
export default async function AdmissionsLayout({ children }: { children: ReactNode }) {
  // Never throws; the shell cannot take the workstation down.
  const session = await loadReceptionSession();
  const brand = hospitalBrand(session?.companyName);

  const userName = session?.userName ?? "";
  const initial = userName.trim().charAt(0).toUpperCase() || "·";
  // The way back to Front Desk for the front-of-house roles. A ward-only nurse
  // or a doctor gets no tabs: they hold no front-desk authority to return to.
  const navItems = frontOfHouseNavItems(session?.roles ?? null, ADMISSIONS_ROUTE);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-100 text-slate-950">
      {/* Literal hex (sky-700): Tailwind v4 does not resolve theme() inside an
          arbitrary value, and a wrong one fails silently. */}
      <header className="flex h-12 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-4 shadow-[inset_0_-2px_0_#0369a1]">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="truncate text-[13px] font-bold uppercase tracking-[0.08em] text-sky-800">
            {brand}
          </span>
          <span aria-hidden className="h-4 w-px shrink-0 bg-slate-200" />
          <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.1em] text-slate-600">
            Admissions Desk
          </span>
          <WorkstationNav items={navItems} accent="sky" />
        </div>

        <div className="flex shrink-0 items-center gap-1">
          <FullscreenToggle />
          {/* Always rendered: a ward-only nurse has no other shell to sign out from. */}
          <AdmissionsUserMenu userName={userName} initial={initial} />
        </div>
      </header>

      <main className="min-h-0 flex-1 overflow-auto p-3">{children}</main>
    </div>
  );
}

import type { ReactNode } from "react";

import FrontDeskUserMenu from "@/components/front-desk/front-desk-user-menu";
import FullscreenToggle from "@/components/workstation/fullscreen-toggle";
import WorkstationNav from "@/components/navigation/workstation-nav";
import { FRONT_DESK_ROUTE, frontOfHouseNavItems } from "@/lib/reception-roles";
import { loadReceptionSession } from "@/lib/reception-session.server";

/**
 * The Front Desk operational shell: SIDEBARLESS, by design.
 *
 * WHY THIS EXISTS AS ITS OWN LAYOUT
 * /front-desk used to render inside ReceptionShell, which is a two-column app
 * shell: a 256px dark-green navigation column plus a three-line page header.
 * That cost roughly 256px of horizontal space and ~90px of vertical space to
 * show a nurse four things they do not need:
 *
 *     YOYA EMR              (sidebar brand)
 *     Front Desk Workspace  (sidebar subtitle)
 *     YOYA General Hospital (header eyebrow)
 *     Front Desk Workstation(header H1)
 *
 * B2.2 folded registration AND triage into this one page, so the sidebar's
 * remaining link was "Front Desk Queue" -- a link to the page already on
 * screen. An entire navigation column for a self-link is dead space at a busy
 * entrance, so the whole workspace is now one screen with one brand line.
 *
 * A ROUTE LAYOUT, NOT A GLOBAL CHANGE. ReceptionShell is untouched and still
 * serves /reception, /reception/new and /reception/visits/[id]; /triage keeps
 * its own shell. A Cashier, Doctor or Admin workspace can add its own layout
 * the same way. Nothing here removes navigation for any other role.
 *
 * PRESENTATION ONLY: no route guard, no authorization. Odoo record rules and
 * the group checks in yoya_emr_api remain the only access control -- a user who
 * reaches this URL without the right groups still gets a 403 from every
 * endpoint the page calls.
 */
export default async function FrontDeskLayout({ children }: { children: ReactNode }) {
  const session = await loadReceptionSession();

  // Falls back to static text when the lookup fails; loadReceptionSession never
  // throws, so the shell cannot take the workstation down.
  const brand = session?.companyName ?? "YOYA General Hospital";
  const roleLabel = session?.roles?.front_desk_nurse
    ? "Front Desk Nurse"
    : "Front Desk";
  // Front Desk | Admissions for the front-of-house roles; nothing for anyone else.
  const navItems = frontOfHouseNavItems(session?.roles ?? null, FRONT_DESK_ROUTE);

  return (
    <div className="flex min-h-screen flex-col bg-slate-100 text-slate-950">
      {/* ~45px: brand and workstation tabs left, signed-in user right. */}
      <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-3">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="truncate text-sm font-bold uppercase tracking-wide text-emerald-800">
            {brand}
          </span>
          <WorkstationNav items={navItems} accent="emerald" />
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <FullscreenToggle />
          <FrontDeskUserMenu userName={session?.userName ?? null} roleLabel={roleLabel} />
        </div>
      </header>

      {/* 12px gutters, no max-width: every pixel is operational workspace. */}
      <main className="min-h-0 flex-1 p-3">{children}</main>
    </div>
  );
}

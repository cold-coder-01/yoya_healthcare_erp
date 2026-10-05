import type { ReactNode } from "react";

import FrontDeskUserMenu from "@/components/front-desk/front-desk-user-menu";
import FullscreenToggle from "@/components/workstation/fullscreen-toggle";
import { hospitalBrand } from "@/lib/branding";
import { loadReceptionSession } from "@/lib/reception-session.server";

/**
 * The Accountant Desk shell: the same sidebarless workstation shape as
 * /cashier, with the hospital brand and the desk's own label.
 *
 * PRESENTATION ONLY: no route guard. The group checks in yoya_emr_api are the
 * access control -- anyone without ACCOUNTANT_DESK_GROUPS gets 403
 * accountant_desk_not_authorized from every call the page makes, and the
 * workstation renders that refusal instead of a queue.
 */
export default async function AccountantLayout({ children }: { children: ReactNode }) {
  const session = await loadReceptionSession();

  const brand = hospitalBrand(session?.companyName);
  const roleLabel = session?.roles?.accountant ? "Accountant" : "Accountant Desk";

  return (
    <div className="flex min-h-screen flex-col bg-slate-100 text-slate-950">
      <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="truncate text-sm font-bold uppercase tracking-wide text-emerald-800">{brand}</span>
          <span className="shrink-0 text-[11px] font-bold uppercase tracking-wide text-slate-500">
            Accountant Desk
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <FullscreenToggle />
          <FrontDeskUserMenu userName={session?.userName ?? null} roleLabel={roleLabel} />
        </div>
      </header>

      <main className="min-h-0 flex-1 p-3">{children}</main>
    </div>
  );
}

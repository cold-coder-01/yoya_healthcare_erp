import type { ReactNode } from "react";

import FrontDeskUserMenu from "@/components/front-desk/front-desk-user-menu";
import FullscreenToggle from "@/components/workstation/fullscreen-toggle";
import { hospitalBrand } from "@/lib/branding";
import { loadReceptionSession } from "@/lib/reception-session.server";

/**
 * The Revenue Analytics shell: the same sidebarless workstation shape as
 * /accountant, with the hospital brand and the view's own label.
 *
 * PRESENTATION ONLY: no route guard. The group checks in
 * hospital_revenue_analytics are the access control -- a user without the
 * Finance Analytics or Executive role gets 403 revenue_analytics_not_authorized
 * from every data call, and the workstation renders that refusal.
 */
export default async function FinanceAnalyticsLayout({ children }: { children: ReactNode }) {
  const session = await loadReceptionSession();
  const brand = hospitalBrand(session?.companyName);

  return (
    <div className="flex min-h-screen flex-col bg-slate-100 text-slate-950">
      <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-3">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="truncate text-sm font-bold uppercase tracking-wide text-emerald-800">{brand}</span>
          <span className="shrink-0 text-[11px] font-bold uppercase tracking-wide text-slate-500">
            Revenue Analytics
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <FullscreenToggle />
          <FrontDeskUserMenu userName={session?.userName ?? null} roleLabel="Revenue Analytics" />
        </div>
      </header>

      <main className="min-h-0 flex-1 p-3">{children}</main>
    </div>
  );
}

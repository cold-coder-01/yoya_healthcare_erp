import type { ReactNode } from "react";

import FrontDeskUserMenu from "@/components/front-desk/front-desk-user-menu";
import { loadReceptionSession } from "@/lib/reception-session.server";

/**
 * The Cashier operational shell.
 *
 * Deliberately the SAME sidebarless shape as /front-desk, and for the same
 * reason: this is a single-purpose workstation at a window, and a 256px
 * navigation column pointing at the page already on screen is dead space. The
 * user menu is reused rather than reimplemented -- it is role-agnostic and its
 * only job is showing who is signed in and offering sign-out.
 *
 * PRESENTATION ONLY: no route guard, no authorization. Odoo record rules and
 * the group checks in yoya_emr_api are the only access control. A user who
 * types this URL without CASHIER_DESK_GROUPS still gets 403
 * cashier_desk_not_authorized from every endpoint the page calls, and the
 * workstation renders that refusal instead of a queue.
 */
export default async function CashierLayout({ children }: { children: ReactNode }) {
  const session = await loadReceptionSession();

  const brand = session?.companyName ?? "YOYA General Hospital";
  const roleLabel = session?.roles?.cashier ? "Cashier" : "Cashier Desk";

  return (
    <div className="flex min-h-screen flex-col bg-slate-100 text-slate-950">
      <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-slate-200 bg-white px-3">
        <span className="truncate text-sm font-bold uppercase tracking-wide text-emerald-800">
          {brand}
        </span>
        <FrontDeskUserMenu userName={session?.userName ?? null} roleLabel={roleLabel} />
      </header>

      <main className="min-h-0 flex-1 p-3">{children}</main>
    </div>
  );
}

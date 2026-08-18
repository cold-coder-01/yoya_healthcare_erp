import type { ReactNode } from "react";

import FrontDeskUserMenu from "@/components/front-desk/front-desk-user-menu";
import { loadReceptionSession } from "@/lib/reception-session.server";

/**
 * The Insurance/Credit operational shell.
 *
 * The same sidebarless shape as /front-desk and /cashier: a single-purpose
 * workstation where a navigation column pointing at the page already on screen
 * would be dead space.
 *
 * PRESENTATION ONLY. Odoo's group checks are the access control; a user who
 * types this URL without INSURANCE_CREDIT_GROUPS gets 403 from every endpoint
 * the page calls, and the workstation renders that refusal instead of a queue.
 */
export default async function InsuranceCreditLayout({
  children,
}: {
  children: ReactNode;
}) {
  const session = await loadReceptionSession();
  const brand = session?.companyName ?? "YOYA General Hospital";
  const roleLabel = session?.roles?.insurance_officer
    ? "Insurance / Credit Officer"
    : "Insurance / Credit";

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

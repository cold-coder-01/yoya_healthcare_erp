import type { ReactNode } from "react";

import TopHeader from "@/components/clinical/top-header";
import { loadReceptionRoles } from "@/lib/reception-session.server";

import ReceptionSidebar from "./reception-sidebar";

/**
 * Server component: resolves roles before render so the sidebar is correct on
 * first paint, with no client round-trip and no flash of a link the user may
 * not be allowed to use. loadReceptionRoles never throws.
 */
export default async function ReceptionShell({
  children,
  title,
  subtitle,
}: {
  children: ReactNode;
  title: string;
  subtitle?: string;
}) {
  const roles = await loadReceptionRoles();

  return (
    <div className="min-h-screen bg-slate-100 text-slate-950">
      <div className="flex min-h-screen">
        <ReceptionSidebar roles={roles} />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopHeader title={title} subtitle={subtitle} />
          <main className="flex-1 px-4 py-4 sm:px-6 lg:px-8">{children}</main>
        </div>
      </div>
    </div>
  );
}

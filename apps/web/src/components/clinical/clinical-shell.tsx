import type { ReactNode } from "react";
import ClinicalSidebar from "./clinical-sidebar";
import TopHeader from "./top-header";

export default function ClinicalShell({
  children,
  title,
  subtitle,
}: {
  children: ReactNode;
  title: string;
  subtitle?: string;
}) {
  return (
    <div className="min-h-screen bg-slate-100 text-slate-950">
      <div className="flex min-h-screen">
        <ClinicalSidebar />
        <div className="flex min-w-0 flex-1 flex-col">
          <TopHeader title={title} subtitle={subtitle} />
          <main className="flex-1 px-4 py-4 sm:px-6 lg:px-8">{children}</main>
        </div>
      </div>
    </div>
  );
}

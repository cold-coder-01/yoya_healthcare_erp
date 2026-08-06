import ReceptionShell from "@/components/reception/reception-shell";
import Link from "next/link";

export default function ReceptionNewVisitPage() {
  return (
    <ReceptionShell title="New Visit" subtitle="Guided registration workspace coming soon">
      <div className="rounded-3xl border border-slate-200 bg-white p-8 shadow-sm">
        <h2 className="text-2xl font-semibold text-slate-950">New Visit workspace</h2>
        <p className="mt-4 text-slate-600">
          This workspace will guide the receptionist through patient lookup, visit selection, and preview.
        </p>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <Link
            href="/reception"
            className="inline-flex h-11 items-center justify-center rounded-md bg-emerald-700 px-5 text-sm font-semibold text-white transition hover:bg-emerald-800"
          >
            Back to reception queue
          </Link>
        </div>
      </div>
    </ReceptionShell>
  );
}

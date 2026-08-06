import Link from "next/link";

import ReceptionShell from "@/components/reception/reception-shell";

/**
 * Slice 1 placeholder.
 *
 * `params` is a Promise in this Next.js version -- destructuring it
 * synchronously (as the first draft did) does not yield the route values.
 * The sibling clinical route at app/triage/[appointmentId]/page.tsx uses the
 * same awaited form.
 */
export default async function ReceptionVisitDetailPage({
  params,
}: {
  params: Promise<{ appointmentId: string }>;
}) {
  const { appointmentId } = await params;

  return (
    <ReceptionShell title="Visit Detail" subtitle={`Visit ${appointmentId}`}>
      <div className="rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <span className="inline-flex items-center rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide text-slate-600">
          Coming in Slice 2
        </span>
        <h2 className="mt-4 text-2xl font-semibold text-slate-950">
          Visit detail
        </h2>
        <p className="mt-3 max-w-2xl text-slate-600">
          Full visit review — patient header, charge lines, reception clearance,
          send-to-triage and emergency bypass — arrives in the next slice. The
          reception queue already shows the clearance totals for this visit.
        </p>
        <div className="mt-6">
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

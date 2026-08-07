import ReceptionShell from "@/components/reception/reception-shell";

import VisitDetailClient from "./visit-detail-client";

/** `params` is a Promise in this Next.js version and must be awaited. */
export default async function ReceptionVisitDetailPage({
  params,
}: {
  params: Promise<{ appointmentId: string }>;
}) {
  const { appointmentId } = await params;

  return (
    <ReceptionShell
      title="Visit Detail"
      subtitle="Clearance, charges and triage handoff"
    >
      <VisitDetailClient appointmentId={appointmentId} />
    </ReceptionShell>
  );
}

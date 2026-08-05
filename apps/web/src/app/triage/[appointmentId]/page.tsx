import ClinicalShell from "@/components/clinical/clinical-shell";
import EvaluationClient from "./evaluation-client";

export default async function TriageDetailPage({
  params,
}: {
  params: Promise<{ appointmentId: string }>;
}) {
  const { appointmentId } = await params;

  return (
    <ClinicalShell title="Clinical Evaluation" subtitle="Vitals, complaint, triage, and readiness for consultation">
      <EvaluationClient appointmentId={appointmentId} />
    </ClinicalShell>
  );
}

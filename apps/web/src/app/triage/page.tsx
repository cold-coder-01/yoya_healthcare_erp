import ClinicalShell from "@/components/clinical/clinical-shell";
import TriageQueueClient from "./triage-queue-client";

export default function TriagePage() {
  return (
    <ClinicalShell
      title="Evaluation Queue"
      subtitle="Triage and nursing evaluation worklist"
    >
      <TriageQueueClient />
    </ClinicalShell>
  );
}

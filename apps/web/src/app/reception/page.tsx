import ReceptionShell from "@/components/reception/reception-shell";
import ReceptionQueueClient from "./reception-queue-client";

export default function ReceptionPage() {
  return (
    <ReceptionShell
      title="Reception Queue"
      subtitle="Manage guided visit registrations and triage handoff"
    >
      <ReceptionQueueClient />
    </ReceptionShell>
  );
}

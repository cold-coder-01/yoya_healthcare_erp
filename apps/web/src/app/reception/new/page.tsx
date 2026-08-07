import ReceptionShell from "@/components/reception/reception-shell";

import NewVisitClient from "./new-visit-client";

export default function ReceptionNewVisitPage() {
  return (
    <ReceptionShell
      title="New Visit"
      subtitle="Register a patient, open the visit and quote the fees in one step"
    >
      <NewVisitClient />
    </ReceptionShell>
  );
}

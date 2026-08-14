import { formatBloodGroup, formatHospitalDateTime } from "@/lib/clinical-format";
import { displayValue, frontDeskLabel } from "@/lib/front-desk-format";
import { formatEtb, formatVisitType } from "@/lib/reception-format";
import type { FrontDeskVisit } from "@/types/front-desk";

import FrontDeskPayerControl from "./front-desk-payer-control";
import { FrontDeskStageBadge } from "./front-desk-queue";
import FrontDeskTriagePanel from "./front-desk-triage-panel";

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[9px] font-bold uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 truncate text-[12px] font-semibold text-slate-900">{value || "-"}</dd>
    </div>
  );
}

/**
 * The canonical outcome, stated once and taken straight from the backend stage.
 *
 * Deliberately NOT derived from "we just pressed Complete": the same completion
 * lands on Awaiting Cashier or Ready for Doctor depending on encounter-wide
 * clearance, and only the server knows which.
 */
function StatusStrip({ visit }: { visit: FrontDeskVisit }) {
  const stage = visit.row.queue_stage;
  const done = visit.evaluation?.state === "done";
  const outstanding = visit.clearance.outstanding;

  const strips: Record<string, { tone: string; title: string; detail: string }> = {
    awaiting_cashier: {
      tone: "border-amber-500 bg-amber-50 text-amber-950",
      title: done ? "Triage complete — Send patient to Cashier" : "Send patient to Cashier",
      detail:
        visit.permitted_actions.blocked_reason ??
        (outstanding > 0
          ? `Prepayment of ${formatEtb(outstanding)} is required before service.`
          : "Payment clearance is pending."),
    },
    ready_doctor: {
      tone: "border-emerald-600 bg-emerald-50 text-emerald-950",
      title: "Ready for Doctor",
      detail: "Triage complete and financially cleared.",
    },
    in_consultation: {
      tone: "border-sky-600 bg-sky-50 text-sky-950",
      title: "In consultation",
      detail: "The doctor has started this consultation.",
    },
    completed: {
      tone: "border-slate-400 bg-slate-100 text-slate-800",
      title: "Visit completed",
      detail: "This visit is closed.",
    },
    cancelled: {
      tone: "border-slate-400 bg-slate-100 text-slate-800",
      title: "Visit cancelled",
      detail: "This visit was cancelled.",
    },
  };

  const strip = strips[stage];
  if (!strip) return null;

  return (
    /*
      ONE handoff strip. The stage label that used to sit on the right was the
      third copy of the same word -- the header badge already carries it, and
      the footer states the outcome -- so it is gone. Title and money remain,
      because "how much, and where does the patient go next" is the only thing
      this strip exists to answer.
    */
    <div
      className={`flex shrink-0 items-center gap-2 border-b border-l-4 border-b-slate-200 px-3 py-1 ${strip.tone}`}
    >
      <div className="min-w-0">
        <div className="truncate text-xs font-bold leading-tight">{strip.title}</div>
        <div className="truncate text-[11px] leading-tight opacity-90">{strip.detail}</div>
      </div>
    </div>
  );
}

/** Read-only. The front desk never takes money; the cashier has its own screen. */
function FinancialSummary({ visit }: { visit: FrontDeskVisit }) {
  const clearance = visit.clearance;
  return (
    <section className="border-b border-slate-200 px-3 py-1.5">
      <h3 className="mb-1 text-[10px] font-bold uppercase tracking-wide text-slate-600">
        Financial (read-only)
      </h3>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Field label="Required" value={formatEtb(clearance.required)} />
        <Field label="Received" value={formatEtb(clearance.received)} />
        <Field
          label="Outstanding"
          value={
            <span className={clearance.outstanding > 0 ? "text-amber-800" : "text-emerald-700"}>
              {formatEtb(clearance.outstanding)}
            </span>
          }
        />
        <Field label="Clearance" value={frontDeskLabel(clearance.state)} />
        <Field label="Funding" value={frontDeskLabel(visit.row.operational_funding_state)} />
      </dl>
    </section>
  );
}

export default function FrontDeskPatientPanel({
  visit,
  loading,
  error,
  capabilities,
  onMutated,
}: {
  visit: FrontDeskVisit | null;
  loading: boolean;
  error: string | null;
  capabilities: Record<string, boolean>;
  onMutated: () => void;
}) {
  return (
    <section className="flex min-h-[420px] min-w-0 flex-col overflow-hidden rounded border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      {!visit && loading ? (
        <div className="flex h-full flex-col gap-3 p-4" aria-label="Loading selected visit">
          <div className="h-12 animate-pulse bg-slate-100" />
          <div className="h-24 animate-pulse bg-slate-100" />
          <div className="h-40 animate-pulse bg-slate-100" />
        </div>
      ) : !visit ? (
        <div className="flex h-full min-h-72 items-center justify-center px-6 text-center">
          <div>
            <p className="text-sm font-semibold text-slate-700">
              {error ?? "Select a patient from the queue."}
            </p>
            {error ? (
              <p className="mt-1 text-xs text-slate-500">The queue remains available.</p>
            ) : null}
          </div>
        </div>
      ) : (
        <>
          {/*
            Patient name is the strongest text in the panel; identity metadata
            is one quiet line beneath it, separated by middots rather than pipes
            so the name keeps the eye. The stage badge is the panel's single
            authoritative stage indicator.
          */}
          <header className="flex min-h-10 shrink-0 items-center justify-between gap-3 border-b border-slate-200 px-3 py-1">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="truncate text-[15px] font-bold leading-tight tracking-tight text-slate-950">
                  {visit.patient.name}
                </h2>
                {visit.row.urgent ? (
                  <span className="shrink-0 rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-bold uppercase leading-none text-white">
                    Urgent
                  </span>
                ) : null}
              </div>
              <p className="truncate text-[11px] leading-tight text-slate-500">
                <span className="font-mono font-semibold text-slate-700">
                  {visit.patient.mrn ?? "No MRN"}
                </span>
                <span className="text-slate-300"> · </span>
                {visit.visit.appointment_code ?? `#${visit.visit.appointment_id}`}
                <span className="text-slate-300"> · </span>
                {displayValue(visit.patient.age, "y")}
                <span className="text-slate-300"> · </span>
                {frontDeskLabel(visit.patient.gender)}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {loading ? <span className="text-[10px] text-slate-500">Updating…</span> : null}
              <FrontDeskStageBadge stage={visit.row.queue_stage} />
            </div>
          </header>

          {error ? (
            <div className="shrink-0 border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
              {error}
            </div>
          ) : null}

          <StatusStrip visit={visit} />

          {/*
            The triage panel owns the scrolling body, the history tabs and the
            pinned action footer. Identity and money are secondary reference
            data, so they are passed as children and scroll BELOW the triage
            form rather than above it -- the nurse must not have to scroll past
            reference data to reach the only controls on the screen.
          */}
          <FrontDeskTriagePanel
            visit={visit}
            capabilities={capabilities}
            onMutated={onMutated}
          >
            <section className="border-b border-slate-200 px-3 py-1.5">
              <h3 className="mb-1 text-[10px] font-bold uppercase tracking-wide text-slate-600">
                Patient &amp; visit
              </h3>
              <dl className="grid grid-cols-3 gap-x-3 gap-y-2 sm:grid-cols-6">
                <Field label="Blood group" value={formatBloodGroup(visit.patient.blood_group)} />
                <Field label="Mobile" value={visit.patient.mobile ?? visit.patient.phone ?? "-"} />
                <Field label="Arrival" value={formatHospitalDateTime(visit.visit.arrived_at)} />
                <Field label="Visit type" value={formatVisitType(visit.visit.visit_type)} />
                <Field label="Visit state" value={frontDeskLabel(visit.visit.state)} />
                <Field label="Reason (registration)" value={visit.visit.reason ?? "-"} />
              </dl>
            </section>

            {/*
              Payer identity sits BETWEEN the visit reference data and the money.
              That is the reading order the desk actually uses -- who is
              responsible, then what is outstanding -- and it keeps the two
              visually separate, because in this phase they are unrelated: an
              eligibility never reduces the amount below.
            */}
            <FrontDeskPayerControl visit={visit} onMutated={onMutated} />

            <FinancialSummary visit={visit} />
          </FrontDeskTriagePanel>
        </>
      )}
    </section>
  );
}

"use client";

import { useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  formatBloodGroup,
  formatHospitalDate,
  formatHospitalTime,
} from "@/lib/clinical-format";
import {
  compactGender,
  displayText,
  doctorLabel,
  visitReadiness,
} from "@/lib/doctor-format";
import type { ApiEnvelope, DoctorVisitDetail } from "@/types/doctor";

import {
  ClearanceBadge,
  PriorityBadge,
  StageBadge,
  TriageBadge,
  VisitStateBadge,
} from "./doctor-badges";
import DoctorVitalsGrid from "./doctor-vitals-grid";

const SEVERITY_TONE: Record<string, string> = {
  critical: "border-red-400 bg-red-50 text-red-900",
  high: "border-red-400 bg-red-50 text-red-900",
  severe: "border-red-400 bg-red-50 text-red-900",
  medium: "border-amber-400 bg-amber-50 text-amber-900",
  moderate: "border-amber-400 bg-amber-50 text-amber-900",
  low: "border-slate-300 bg-slate-100 text-slate-700",
};

function Section({
  title,
  children,
  aside,
}: {
  title: string;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[10px] font-bold uppercase tracking-wide text-slate-500">
          {title}
        </h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col">
      <span className="text-[9px] font-bold uppercase tracking-wide text-slate-500">
        {label}
      </span>
      <span className="truncate text-[12px] font-semibold text-slate-900">{value}</span>
    </div>
  );
}

/**
 * The operational panel. One screen, no navigation.
 *
 * The brief's requirement is that selecting a patient opens ONE panel rather
 * than walking several Odoo screens, so identity, visit, triage, vitals,
 * alerts and the clearance verdict are all resolved from a single read and
 * laid out in scanning order: who, then why, then what triage found, then
 * whether the doctor may proceed.
 *
 * WHAT IS NOT HERE IS DELIBERATE. No amount, balance, receipt, sponsor share
 * or agreement appears anywhere, and no payer is named. The financial gate is
 * represented by a verdict and its reason, which is all a clinician acts on.
 */
export default function DoctorPatientPanel({
  detail,
  loading,
  error,
  onStarted,
}: {
  detail: DoctorVisitDetail | null;
  loading: boolean;
  error: string | null;
  onStarted: () => void;
}) {
  const [starting, setStarting] = useState(false);
  /**
   * The refusal is stored WITH the visit it belongs to, and rendered only when
   * that visit is still the one on screen.
   *
   * A stale refusal must never sit on the next patient's panel -- "Financial
   * clearance required" under a different name would be read as that patient's
   * problem. Tagging the error is what makes the wrong pairing impossible;
   * clearing it from an effect keyed on the id would both lag by a frame and
   * trip react-hooks/set-state-in-effect.
   */
  const [startError, setStartError] = useState<{
    appointmentId: number;
    message: string;
  } | null>(null);

  const appointmentId = detail?.visit.appointment_id ?? null;
  const visibleStartError =
    startError && startError.appointmentId === appointmentId ? startError.message : null;

  if (!detail) {
    return (
      <section className="flex min-h-[320px] min-w-0 items-center justify-center rounded border border-slate-200 bg-white p-6 text-center shadow-sm min-[1100px]:min-h-0">
        <p className="text-xs text-slate-500">
          {error ?? (loading ? "Loading patient…" : "Select a patient from the queue.")}
        </p>
      </section>
    );
  }

  const { visit, patient, triage, medical_alerts: alerts, encounter, clearance } = detail;

  // Readiness is the AUTHORITATIVE stage plus the server's own assignment
  // verdict. Nothing here re-derives it from triage state and a billing flag.
  const readiness = visitReadiness({
    state: visit.state,
    queueStage: visit.queue_stage,
    canStart: detail.can_start_consultation,
    clearanceReason: clearance.reason,
  });

  async function startConsultation() {
    if (!appointmentId) return;
    setStarting(true);
    setStartError(null);
    const target = appointmentId;
    try {
      const response = await fetch(
        `/api/doctor/visits/${appointmentId}/start-consultation`,
        { method: "POST", cache: "no-store" },
      );
      const payload = (await response.json()) as ApiEnvelope<unknown>;

      if (!response.ok || !payload.success) {
        // Odoo's own sentence, verbatim. It names which of the four gates
        // refused, which no message invented here could do.
        setStartError({
          appointmentId: target,
          message: messageFromPayload(payload, "Unable to start the consultation."),
        });
        return;
      }
      onStarted();
    } catch {
      setStartError({
        appointmentId: target,
        message: "Unable to reach the consultation service.",
      });
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="flex min-h-[320px] min-w-0 flex-col overflow-hidden rounded border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      {/* Identity header: everything needed to confirm the right patient. */}
      <header className="shrink-0 border-b border-slate-200 bg-slate-50 px-3 py-2">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <h2 className="truncate text-[15px] font-bold leading-tight text-slate-950">
            {patient.name}
          </h2>
          <span className="font-mono text-[12px] font-bold text-slate-600">
            {patient.mrn ?? "No chart no."}
          </span>
          <span className="text-[12px] font-semibold text-slate-700">
            {displayText(patient.age)} / {compactGender(patient.gender)}
          </span>
          {loading ? (
            <span className="text-[10px] text-slate-500">Updating…</span>
          ) : null}
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {/* The authoritative stage leads: it is the one badge that decides
              whether this patient may be seen. */}
          <StageBadge stage={visit.queue_stage} />
          <VisitStateBadge state={visit.state} />
          <PriorityBadge priority={triage.priority} />
          <TriageBadge status={triage.status} />
          <ClearanceBadge blocked={clearance.blocked} />
          {visit.visit_type ? (
            <span className="inline-flex h-5 items-center rounded border border-slate-300 bg-white px-1.5 text-[10px] font-bold uppercase tracking-wide text-slate-700">
              {doctorLabel(visit.visit_type)}
            </span>
          ) : null}
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {/* ---- Visit ---- */}
        <div className="grid grid-cols-2 gap-x-3 gap-y-2 sm:grid-cols-4">
          <Field label="Visit no." value={displayText(visit.appointment_code)} />
          <Field
            label="Date"
            value={`${formatHospitalDate(visit.appointment_date)} ${formatHospitalTime(
              visit.appointment_date,
              "",
            )}`}
          />
          <Field label="Department" value={displayText(visit.department?.name)} />
          <Field label="Doctor" value={displayText(visit.doctor?.name, "Unassigned")} />
          <Field label="Encounter" value={displayText(encounter?.name)} />
          <Field label="Blood group" value={formatBloodGroup(patient.blood_group, "—")} />
          <Field label="Date of birth" value={formatHospitalDate(patient.date_of_birth, "—")} />
          {/*
            Sponsorship CATEGORY only. hospital.payer, hospital.payer.agreement
            and hospital.patient.payer carry no Doctor ACL and no named payer,
            agreement or membership is ever requested or rendered.
          */}
          <Field label="Payer" value={doctorLabel(detail.payer_type, "—")} />
        </div>

        {/* ---- Medical Alerts ----
            Named exactly as the schema names them. hospital.patient has no
            authoritative allergy field, so this is never presented as an
            Allergies list even though the vendor screen has one here. */}
        <Section title="Medical Alerts">
          {alerts.length === 0 ? (
            <p className="rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-[11px] text-slate-500">
              No medical alerts recorded.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {alerts.map((alert) => (
                <li
                  key={alert.id}
                  className={`inline-flex items-center gap-1.5 rounded border px-1.5 py-1 text-[11px] font-semibold ${
                    SEVERITY_TONE[alert.severity?.toLowerCase() ?? ""] ??
                    "border-slate-300 bg-slate-100 text-slate-800"
                  }`}
                >
                  {alert.name}
                  {alert.severity ? (
                    <span className="text-[9px] font-bold uppercase tracking-wide opacity-75">
                      {doctorLabel(alert.severity)}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </Section>

        {/* ---- Presenting problem ---- */}
        <Section title="Chief complaint">
          <p className="rounded border border-slate-200 bg-slate-50 px-2 py-1.5 text-[12px] leading-snug text-slate-900">
            {triage.chief_complaint ?? visit.reason ?? "Not recorded at triage."}
          </p>
          {triage.notes ? (
            <p className="rounded border border-slate-200 bg-white px-2 py-1.5 text-[11px] leading-snug text-slate-600">
              <span className="font-bold uppercase tracking-wide text-slate-500">
                Triage notes:{" "}
              </span>
              {triage.notes}
            </p>
          ) : null}
        </Section>

        {/* ---- Vitals ---- */}
        <Section
          title="Triage vitals"
          aside={
            triage.completed_at ? (
              <span className="text-[10px] text-slate-500">
                Completed {formatHospitalTime(triage.completed_at)}
              </span>
            ) : null
          }
        >
          <DoctorVitalsGrid vitals={triage.vitals} previous={detail.previous_vitals} />
        </Section>

        {/* ---- History ---- */}
        {patient.past_medical_history || patient.disease_history ? (
          <Section title="History">
            <div className="grid gap-1.5 sm:grid-cols-2">
              {patient.past_medical_history ? (
                <p className="rounded border border-slate-200 bg-white px-2 py-1.5 text-[11px] leading-snug text-slate-700">
                  <span className="font-bold uppercase tracking-wide text-slate-500">
                    Past medical:{" "}
                  </span>
                  {patient.past_medical_history}
                </p>
              ) : null}
              {patient.disease_history ? (
                <p className="rounded border border-slate-200 bg-white px-2 py-1.5 text-[11px] leading-snug text-slate-700">
                  <span className="font-bold uppercase tracking-wide text-slate-500">
                    Disease history:{" "}
                  </span>
                  {patient.disease_history}
                </p>
              ) : null}
            </div>
          </Section>
        ) : null}
      </div>

      {/* ---- The gate ---- */}
      <footer className="shrink-0 border-t border-slate-200 bg-slate-50 px-3 py-2">
        {visibleStartError ? (
          <p
            role="alert"
            className="mb-2 rounded border border-red-300 bg-red-50 px-2 py-1.5 text-[11px] leading-snug text-red-900"
          >
            {visibleStartError}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="min-w-0 flex-1 text-[11px] leading-snug text-slate-600">
            {readiness.ready ? (
              <span className="font-semibold text-emerald-800">
                Ready for doctor. Triage complete and cleared at the desk.
              </span>
            ) : (
              <>
                <span className="font-semibold text-amber-800">Cannot start: </span>
                {readiness.reason}
              </>
            )}
          </p>

          <button
            type="button"
            onClick={startConsultation}
            // Disabled only as an affordance. The four model-layer gates in
            // action_start_consultation decide the real answer, and a doctor
            // whose visit looks ready here can still be refused there.
            disabled={!readiness.ready || starting}
            className="h-8 shrink-0 rounded bg-emerald-700 px-3 text-[12px] font-bold uppercase tracking-wide text-white outline-none transition-colors hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:bg-slate-300 disabled:text-slate-600"
          >
            {starting ? "Starting…" : "Start Consultation"}
          </button>
        </div>
      </footer>
    </section>
  );
}

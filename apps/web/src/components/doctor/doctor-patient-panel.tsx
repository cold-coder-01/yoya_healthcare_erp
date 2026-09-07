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
import { hasActiveCareRelationship } from "@/lib/history-format";
import type { ApiEnvelope, DoctorVisitDetail } from "@/types/doctor";

import HistoryWorkspace from "./consultation/history-workspace";
import { PriorityBadge, StageBadge, VisitTypeBadge } from "./doctor-badges";
import DoctorVitalsGrid from "./doctor-vitals-grid";

/**
 * The two views this PRE-CONSULTATION panel can show.
 *
 * DELIBERATELY TWO, AND DELIBERATELY BOTH READ-ONLY. Note, Diagnosis, Orders
 * and Results are not offered here and must not be: they are the consultation
 * workspace, they carry writes, and they stay behind Start Consultation and
 * the four model-layer gates it runs. Only History is added, because Slice 9A
 * already authorizes reading it from `confirmed` onward and the desk was the
 * only thing making that unreachable.
 */
type PanelView = "overview" | "history";

const SEVERITY_TONE: Record<string, string> = {
  critical: "border-red-400 bg-red-50 text-red-900",
  high: "border-red-400 bg-red-50 text-red-900",
  severe: "border-red-400 bg-red-50 text-red-900",
  medium: "border-amber-400 bg-amber-50 text-amber-900",
  moderate: "border-amber-400 bg-amber-50 text-amber-900",
  low: "border-slate-300 bg-slate-100 text-slate-700",
};

/**
 * A titled block with a hairline rule running to the panel edge.
 *
 * The rule is what gives the scroll column its rhythm: previously every
 * section was just a label above some content, so the panel read as one
 * undifferentiated stack and the eye had nothing to anchor on.
 */
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
    <section className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h3 className="shrink-0 cl-meta font-bold uppercase tracking-[0.08em] text-slate-500">
          {title}
        </h3>
        <span aria-hidden className="h-px flex-1 bg-slate-200" />
        {aside}
      </div>
      {children}
    </section>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="cl-micro font-semibold uppercase tracking-[0.06em] text-slate-400">
        {label}
      </span>
      <span className="truncate cl-body font-semibold text-slate-900">{value}</span>
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

  /*
    WHICH VIEW IS OPEN, tagged with the visit it belongs to.

    Storing the id alongside the view is what resets it to Overview when the
    doctor selects a different patient, WITHOUT an effect that writes state
    during render -- the same pattern doctor-workstation uses for its section
    and this component already uses for its start error. A History tab left
    open must never carry one patient's past onto the next patient's panel.
  */
  const [viewState, setViewState] = useState<{
    appointmentId: number | null;
    view: PanelView;
  }>({ appointmentId: null, view: "overview" });

  const appointmentId = detail?.visit.appointment_id ?? null;
  const visibleStartError =
    startError && startError.appointmentId === appointmentId ? startError.message : null;

  const view: PanelView =
    viewState.appointmentId === appointmentId ? viewState.view : "overview";

  if (!detail) {
    return (
      <section className="flex min-h-[320px] min-w-0 items-center justify-center rounded-lg border border-slate-200 bg-white p-6 text-center shadow-sm min-[1100px]:min-h-0">
        <p className="max-w-[24rem] cl-body text-slate-500">
          {error ?? (loading ? "Loading patient…" : "Select a patient from the queue.")}
        </p>
      </section>
    );
  }

  const { visit, patient, triage, medical_alerts: alerts, encounter, clearance } = detail;

  /*
    Whether to OFFER History at all. See hasActiveCareRelationship: it mirrors
    the server's own active-care states, and it decides what to draw, never
    what may be read.
  */
  const showHistoryTab = hasActiveCareRelationship(visit.state);

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
    <section className="flex min-h-[320px] min-w-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      {/*
        Identity header: everything needed to confirm the right patient, and
        the one badge that says whether they may be seen.

        The name is the largest text on the screen by a clear margin -- picking
        the wrong patient is the most expensive mistake this UI can cause, so
        identity outranks every other element in the hierarchy.
      */}
      <header className="shrink-0 border-b border-slate-200 bg-gradient-to-b from-white to-slate-50 px-3.5 py-2.5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2.5">
            <span
              aria-hidden
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-200 cl-strong font-bold text-slate-600"
            >
              {patient.name.trim().charAt(0).toUpperCase() || "?"}
            </span>
            <div className="min-w-0">
              <h2 className="truncate cl-head-lg font-bold leading-tight tracking-tight text-slate-950">
                {patient.name}
              </h2>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 cl-secondary leading-tight text-slate-500">
                <span className="font-mono font-bold text-slate-700">
                  {patient.mrn ?? "No chart no."}
                </span>
                <span aria-hidden className="text-slate-300">
                  ·
                </span>
                <span className="font-semibold text-slate-700">
                  {displayText(patient.age)} / {compactGender(patient.gender)}
                </span>
                {visit.appointment_code ? (
                  <>
                    <span aria-hidden className="text-slate-300">
                      ·
                    </span>
                    <span className="truncate">{visit.appointment_code}</span>
                  </>
                ) : null}
              </div>
            </div>
          </div>

          {loading ? (
            <span className="shrink-0 cl-meta text-slate-500">Updating…</span>
          ) : null}
        </div>

        {/*
          THE badge row, deliberately short. StageBadge already composes
          appointment state, triage progress and financial clearance, so the
          old VisitState / Triage / Clearance badges beside it repeated the
          same fact in three more chips. Priority and visit type appear only
          when they are not the routine default.
        */}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <StageBadge stage={visit.queue_stage} />
          <PriorityBadge priority={triage.priority} />
          <VisitTypeBadge visitType={visit.visit_type} />
        </div>
      </header>

      {/*
        ---- View strip ----

        SHOWN ONLY WHEN HISTORY IS ACTUALLY REACHABLE. Slice 9A opens
        longitudinal reading on an ACTIVE care relationship (confirmed or
        in_consultation), so a draft or cancelled visit gets no strip at all
        rather than a tab that could only ever answer "not available".

        It is an affordance, not a gate: the server refuses on every request
        regardless of what this decides to draw.

        The strip sits BELOW the identity header and ABOVE the scrolling body,
        so the patient's name, chart number and stage stay on screen whichever
        view is open -- picking the wrong patient is the most expensive mistake
        this UI can cause, and identity must not scroll away behind a tab.
      */}
      {showHistoryTab ? (
        <div className="flex shrink-0 items-center gap-1 border-b border-slate-200 bg-white px-3">
          {(
            [
              ["overview", "Overview"],
              ["history", "History"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-current={view === key ? "page" : undefined}
              onClick={() => setViewState({ appointmentId, view: key })}
              className={`-mb-px border-b-2 px-1.5 py-1.5 cl-meta font-bold uppercase tracking-[0.07em] outline-none transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 ${
                view === key
                  ? "border-emerald-600 text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-800"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      ) : null}

      {view === "history" && showHistoryTab && appointmentId !== null ? (
        /*
          THE ONLY CLINICAL SURFACE THIS PANEL ADDS, and it is read-only end to
          end: HistoryWorkspace has no write path, and every record it shows is
          served read-only by Slice 9A's record rules.

          Keyed on the visit for the same reason the consultation workspace is:
          one patient's history must never survive a selection change onto the
          next patient's screen.
        */
        <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50/40 p-3.5">
          <HistoryWorkspace key={appointmentId} appointmentId={appointmentId} />
        </div>
      ) : (
      <div className="min-h-0 flex-1 space-y-3.5 overflow-y-auto p-3.5">
        {/* ---- Visit metadata ----
            One recessed slab rather than eight fields floating on the page:
            this is reference detail a doctor consults, not the thing they read
            first, and grouping it keeps it from competing with the complaint
            and the vitals below. */}
        <div className="grid grid-cols-2 gap-x-3 gap-y-2.5 rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2.5 sm:grid-cols-4">
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
          <Field label="Date of birth" value={formatHospitalDate(patient.date_of_birth, "—")} />
          <Field label="Blood group" value={formatBloodGroup(patient.blood_group, "—")} />
          {/*
            Sponsorship CATEGORY only. hospital.payer, hospital.payer.agreement
            and hospital.patient.payer carry no Doctor ACL and no named payer,
            agreement or membership is ever requested or rendered.
          */}
          <Field label="Payer" value={doctorLabel(detail.payer_type, "—")} />
          <Field label="Visit type" value={doctorLabel(visit.visit_type, "—")} />
        </div>

        {/* ---- Medical Alerts ----
            Named exactly as the schema names them. hospital.patient has no
            authoritative allergy field, so this is never presented as an
            Allergies list even though the vendor screen has one here.
            Rendered ONLY when there are alerts: an empty "no alerts recorded"
            box on every patient is a row of nothing that pushes the complaint
            and the vitals further down the scroll. */}
        {alerts.length > 0 ? (
          <Section title="Medical Alerts">
            <ul className="flex flex-wrap gap-1.5">
              {alerts.map((alert) => (
                <li
                  key={alert.id}
                  className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 cl-secondary font-semibold ${
                    SEVERITY_TONE[alert.severity?.toLowerCase() ?? ""] ??
                    "border-slate-300 bg-slate-100 text-slate-800"
                  }`}
                >
                  {alert.name}
                  {alert.severity ? (
                    <span className="cl-micro font-bold uppercase tracking-wide opacity-75">
                      {doctorLabel(alert.severity)}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          </Section>
        ) : null}

        {/* ---- Presenting problem ----
            The clinical headline. Given an emerald keyline and larger, darker
            type than anything else in the scroll column, because it is the one
            sentence a doctor reads before looking at the patient. */}
        <Section title="Chief complaint">
          <p className="rounded-md border border-slate-200 border-l-[3px] border-l-emerald-600 bg-white px-3 py-2 cl-strong font-medium leading-relaxed text-slate-900 shadow-sm">
            {triage.chief_complaint ?? visit.reason ?? (
              <span className="font-normal italic text-slate-400">
                Not recorded at triage.
              </span>
            )}
          </p>
          {triage.notes ? (
            <p className="rounded-md border border-slate-200 bg-slate-50/70 px-3 py-2 cl-secondary leading-relaxed text-slate-600">
              <span className="font-bold uppercase tracking-[0.06em] text-slate-400">
                Triage notes ·{" "}
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
              <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 cl-micro font-semibold uppercase tracking-wide text-slate-500">
                Taken {formatHospitalTime(triage.completed_at)}
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
                <p className="rounded-md border border-slate-200 bg-white px-3 py-2 cl-secondary leading-relaxed text-slate-700">
                  <span className="font-bold uppercase tracking-[0.06em] text-slate-400">
                    Past medical:{" "}
                  </span>
                  {patient.past_medical_history}
                </p>
              ) : null}
              {patient.disease_history ? (
                <p className="rounded-md border border-slate-200 bg-white px-3 py-2 cl-secondary leading-relaxed text-slate-700">
                  <span className="font-bold uppercase tracking-[0.06em] text-slate-400">
                    Disease history:{" "}
                  </span>
                  {patient.disease_history}
                </p>
              ) : null}
            </div>
          </Section>
        ) : null}
      </div>
      )}

      {/* ---- The gate ----
          Tinted by readiness so the whole strip answers "may I proceed" before
          any of it is read: emerald when workable, amber when something is
          still owed or outstanding, neutral once the visit has moved on. */}
      <footer
        className={`shrink-0 border-t px-3.5 py-2.5 ${
          readiness.ready
            ? "border-emerald-200 bg-emerald-50"
            : readiness.gate === "state"
              ? "border-slate-200 bg-slate-50"
              : "border-amber-200 bg-amber-50/70"
        }`}
      >
        {visibleStartError ? (
          <p
            role="alert"
            className="mb-2 rounded-md border border-red-300 bg-red-50 px-2.5 py-1.5 cl-secondary leading-snug text-red-900"
          >
            {visibleStartError}
          </p>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 flex-1 items-start gap-2">
            <span
              aria-hidden
              className={`mt-[3px] h-2 w-2 shrink-0 rounded-full ${
                readiness.ready
                  ? "bg-emerald-600"
                  : readiness.gate === "state"
                    ? "bg-slate-400"
                    : "bg-amber-500"
              }`}
            />
            <p className="min-w-0 cl-secondary leading-snug">
              {readiness.ready ? (
                <span className="font-semibold text-emerald-900">
                  Ready for doctor. Triage complete and cleared at the desk.
                </span>
              ) : (
                <>
                  <span
                    className={`font-semibold ${
                      readiness.gate === "state"
                        ? "text-slate-700"
                        : "text-amber-900"
                    }`}
                  >
                    Cannot start:{" "}
                  </span>
                  <span className="text-slate-700">{readiness.reason}</span>
                </>
              )}
            </p>
          </div>

          <button
            type="button"
            onClick={startConsultation}
            // Disabled only as an affordance. The four model-layer gates in
            // action_start_consultation decide the real answer, and a doctor
            // whose visit looks ready here can still be refused there.
            disabled={!readiness.ready || starting}
            // The ring offset picks up the footer tint rather than white, so
            // the focus ring stays clean against whichever state is showing.
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md bg-emerald-700 px-4 cl-body font-bold uppercase tracking-[0.06em] text-white shadow-sm outline-none transition-colors hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-700 focus-visible:ring-offset-2 focus-visible:ring-offset-transparent disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-500 disabled:shadow-none"
          >
            {starting ? (
              <>
                <svg
                  aria-hidden
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  className="h-3.5 w-3.5 animate-spin"
                >
                  <path d="M14 8a6 6 0 1 1-1.76-4.24" />
                </svg>
                Starting…
              </>
            ) : (
              "Start Consultation"
            )}
          </button>
        </div>
      </footer>
    </section>
  );
}

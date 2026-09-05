"use client";

import type { ReactNode } from "react";

import { formatHospitalDate, formatHospitalDateTime } from "@/lib/clinical-format";
import {
  EPISODE_UNAVAILABLE_TEXT,
  NOTE_FIELDS,
  NOTE_LABELS,
  hasNoteContent,
  hasTriageContent,
  historyImageContentPath,
  vitalsLine,
} from "@/lib/history-format";
import { diagnosisLabel, sortDiagnoses } from "@/lib/diagnosis-format";
import { clinicalValue, isDocumented } from "@/lib/results-format";
import type {
  DoctorHistoryDetailResponse,
  HistoryVisit,
} from "@/types/doctor-history";

import { LaboratoryResultView, RadiologyResultView } from "./result-views";

/**
 * One prior episode, rendered read-only inside the history viewer.
 *
 * A DOCUMENT, NOT A WORKSPACE. Every control this file renders either closes
 * the dialog or opens an image; there is no field, no form and no action that
 * could change a historical record. Slice 9A serves these records read-only at
 * the record-rule layer, so an edit control here could not do anything but
 * fail -- and would imply to a clinician that the past is editable.
 *
 * EMPTY SECTIONS ARE NOT DRAWN. A prior episode that ordered no imaging shows
 * no imaging heading at all, rather than a heading over an empty box. The
 * decision lives in history-format so it is testable without a DOM.
 *
 * THE RESULT BODIES ARE THE RESULTS TAB'S OWN. LaboratoryResultView and
 * RadiologyResultView are imported unchanged, so a released panel reads
 * identically whether a doctor opens it from RESULTS today or from HISTORY in
 * six months. Two renderers would eventually disagree about what a value, a
 * flag or an empty report means, and a doctor would have no way to tell which
 * screen was right.
 *
 * IMAGERY RESOLVES THROUGH THE HISTORY ROUTE. The builder handed to
 * RadiologyResultView names BOTH appointments; the current-visit results route
 * deliberately refuses a historical episode, so this is not an alternative path
 * to the same bytes -- it is the only one that works, and the only one that is
 * checked for this episode.
 */
export default function HistoryVisitView({
  appointmentId,
  visit,
  detail,
  loading,
  error,
}: {
  /** The CURRENT visit. It proves the care relationship in every image URL. */
  appointmentId: number;
  /** The row that was clicked. Used for identity while the detail loads. */
  visit: HistoryVisit;
  detail: DoctorHistoryDetailResponse | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="flex flex-col gap-2" aria-busy="true">
        {[0, 1, 2].map((row) => (
          <div
            key={row}
            className="h-14 animate-pulse rounded border border-slate-200 bg-slate-50 motion-reduce:animate-none"
          />
        ))}
        <p className="cl-secondary text-slate-500">Loading visit…</p>
      </div>
    );
  }

  if (error || !detail) {
    return (
      <p
        role="alert"
        className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 cl-secondary leading-snug text-amber-900"
      >
        {error ?? EPISODE_UNAVAILABLE_TEXT}
      </p>
    );
  }

  const header = detail.visit;
  const historicalId = header.appointment_id ?? visit.appointment_id;

  return (
    <div className="flex flex-col gap-3">
      {/* ---------------- Visit summary ---------------- */}
      <Section title="Visit summary">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
          <Fact label="Encounter" value={header.encounter_code} />
          <Fact label="Date" value={formatHospitalDate(header.date)} />
          <Fact label="Type" value={header.encounter_type_label} />
          <Fact label="Clinician" value={header.doctor} />
          <Fact label="Department" value={header.department} />
          <Fact label="Status" value={header.status_label} />
          {header.completed_at ? (
            <Fact
              label="Completed"
              value={formatHospitalDateTime(header.completed_at)}
            />
          ) : null}
        </dl>
      </Section>

      {/* ---------------- Triage ---------------- */}
      {hasTriageContent(detail.triage) && detail.triage ? (
        <Section title="Triage">
          {detail.triage.chief_complaint ? (
            <p className="cl-secondary leading-snug text-slate-800">
              {detail.triage.chief_complaint}
            </p>
          ) : null}
          {/*
            RECORDED VALUES ONLY. history-format drops a null rather than
            printing 0, because an unrecorded SpO2 and a measured 0% are not
            the same clinical fact and the column cannot tell them apart.
          */}
          {vitalsLine(detail.triage) ? (
            <p className="cl-meta font-medium text-slate-700">
              {vitalsLine(detail.triage)}
            </p>
          ) : null}
          <p className="flex flex-wrap gap-x-3 cl-micro text-slate-500">
            {detail.triage.triage_priority_label ? (
              <span>Priority: {detail.triage.triage_priority_label}</span>
            ) : null}
            {detail.triage.pain_level ? (
              <span>Pain: {detail.triage.pain_level}</span>
            ) : null}
            {detail.triage.recorded_at ? (
              <span>{formatHospitalDateTime(detail.triage.recorded_at)}</span>
            ) : null}
          </p>
        </Section>
      ) : null}

      {/* ---------------- Diagnoses ---------------- */}
      {detail.diagnoses.length ? (
        <Section title="Diagnoses">
          <ul className="flex flex-col gap-1">
            {/* Primary first, then supporting, then what was being ruled out:
                the SAME order the Diagnosis tab uses, reused rather than
                restated so the two cannot disagree. */}
            {sortDiagnoses(detail.diagnoses).map((row) => (
              <li
                key={row.id}
                className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 border-b border-slate-100 pb-1 last:border-0"
              >
                <span className="cl-micro font-bold uppercase tracking-wide text-slate-500">
                  {diagnosisLabel(row.diagnosis_type)}
                </span>
                <span className="cl-secondary font-semibold text-slate-900">
                  {row.disease?.name ?? "—"}
                </span>
                {row.disease?.code ? (
                  <span className="cl-micro text-slate-500">
                    ({row.disease.code})
                  </span>
                ) : null}
                {row.certainty ? (
                  <span className="cl-micro text-slate-500">
                    {diagnosisLabel(row.certainty)}
                  </span>
                ) : null}
                {row.severity ? (
                  <span className="cl-micro text-slate-500">
                    {diagnosisLabel(row.severity)}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {/* ---------------- Clinical note ---------------- */}
      {hasNoteContent(detail.note) && detail.note ? (
        <Section title="Clinical note">
          <div className="flex flex-col gap-2">
            {NOTE_FIELDS.map((field) => {
              const value = detail.note?.[field];
              if (!isDocumented(value)) return null;
              return (
                <div key={field}>
                  <p className="cl-micro font-bold uppercase tracking-wide text-slate-500">
                    {NOTE_LABELS[field]}
                  </p>
                  {/* whitespace-pre-line: a clinician's paragraph breaks are
                      clinical content, not formatting to normalise away. */}
                  <p className="whitespace-pre-line cl-secondary leading-snug text-slate-800">
                    {value}
                  </p>
                </div>
              );
            })}
          </div>
        </Section>
      ) : null}

      {/* ---------------- Medications ---------------- */}
      {detail.medications.length ? (
        <Section title="Medications prescribed">
          <ul className="flex flex-col gap-1.5">
            {detail.medications.map((prescription) => (
              <li key={prescription.id} className="flex flex-col gap-0.5">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <span className="cl-micro font-semibold uppercase tracking-wide text-slate-500">
                    {prescription.prescription_code}
                  </span>
                  {/*
                    A FULFILMENT FACT, NOT AN ADHERENCE CLAIM. `status_label`
                    says what the pharmacy did; no model records whether the
                    patient took anything, so nothing here says they did.
                  */}
                  <span className="rounded border border-slate-300 bg-slate-50 px-1.5 py-px cl-micro font-bold uppercase tracking-wide text-slate-600">
                    {prescription.status_label}
                  </span>
                  {prescription.ordered_at ? (
                    <span className="cl-micro text-slate-500">
                      Prescribed {formatHospitalDate(prescription.ordered_at)}
                    </span>
                  ) : null}
                </span>
                <ul className="flex flex-col gap-0.5 pl-3">
                  {prescription.medicines.map((medicine) => (
                    <li key={medicine.id} className="cl-meta text-slate-800">
                      <span className="font-semibold">{medicine.name}</span>
                      {[
                        medicine.strength,
                        medicine.dosage,
                        medicine.route,
                        medicine.frequency,
                        medicine.duration,
                      ]
                        .filter(Boolean)
                        .map((part) => ` · ${part}`)
                        .join("")}
                      {medicine.instructions ? (
                        <span className="block cl-micro text-slate-500">
                          {medicine.instructions}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {/* ---------------- Laboratory ---------------- */}
      {detail.laboratory.length ? (
        <Section title="Laboratory">
          <div className="flex flex-col gap-3">
            {detail.laboratory.map((row) => (
              <div key={row.request_id} className="flex flex-col gap-1">
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="cl-micro font-semibold uppercase tracking-wide text-slate-500">
                    {row.request_code}
                  </span>
                  <span className="cl-micro font-bold uppercase tracking-wide text-slate-600">
                    {row.status_label}
                  </span>
                </p>
                {row.result ? (
                  <LaboratoryResultView row={row} />
                ) : (
                  <p className="cl-meta text-slate-500">
                    No released laboratory result.
                  </p>
                )}
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {/* ---------------- Radiology ---------------- */}
      {detail.radiology.length ? (
        <Section title="Radiology">
          <div className="flex flex-col gap-3">
            {detail.radiology.map((row) => (
              <div key={row.request_id} className="flex flex-col gap-1">
                <p className="flex flex-wrap items-baseline gap-x-2">
                  <span className="cl-micro font-semibold uppercase tracking-wide text-slate-500">
                    {row.request_code}
                  </span>
                  <span className="cl-micro font-bold uppercase tracking-wide text-slate-600">
                    {row.status_label}
                  </span>
                </p>
                {row.result && historicalId !== null ? (
                  <RadiologyResultView
                    row={row}
                    appointmentId={appointmentId}
                    /* BOTH appointments travel in every image URL. */
                    imageSrc={(imageId, disposition) =>
                      historyImageContentPath(
                        appointmentId,
                        historicalId,
                        imageId,
                        disposition,
                      )
                    }
                  />
                ) : (
                  <p className="cl-meta text-slate-500">
                    No released radiology report.
                  </p>
                )}
              </div>
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  );
}

/** One titled block. Section chrome lives here once. */
function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h4 className="cl-micro font-bold uppercase tracking-[0.07em] text-slate-500">
        {title}
      </h4>
      {children}
    </section>
  );
}

/** One label/value pair. An absent value reads as a dash, never as blank. */
function Fact({ label, value }: { label: string; value: string | null }) {
  return (
    <div className="min-w-0">
      <dt className="cl-micro uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="truncate cl-meta font-semibold text-slate-900">
        {clinicalValue(value)}
      </dd>
    </div>
  );
}

"use client";

import type { ReactNode } from "react";

import { formatHospitalDateTime } from "@/lib/clinical-format";
import {
  ageSexLabel,
  clearanceLabel,
  encounterLabel,
  financialLabel,
  formatLengthOfStay,
  orDash,
} from "@/lib/admissions-desk-format";
import type { AdmissionDetail, CountAndLatest, NamedRef } from "@/types/admissions-desk";

import AdmissionLanePill from "./admission-lane-pill";

/**
 * One admission. Its actions are offered only when the SERVER says so twice
 * over -- the role's capability AND this record's own affordance:
 *
 *   Admit to bed…     (Slice 2)  `admit`          + `can_admit`
 *   Transfer patient… (Slice 3)  `transfer`       + `can_transfer`
 *   Cancel request    (Slice 3)  `cancel_request` + `can_cancel_request`
 *
 * The browser derives none of them. There is no discharge control (Slice 4).
 * The financial section shows the server's STATE only, never a figure.
 *
 * Needs-review reasons come first and in red: a broken admission must be seen
 * before anything that looks normal about it.
 */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="cl-micro font-bold uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="min-w-0 truncate cl-body text-slate-900">{children}</dd>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-slate-200 px-3 py-2">
      <h3 className="mb-1.5 cl-micro font-bold uppercase tracking-wide text-slate-600">{title}</h3>
      {children}
    </section>
  );
}

function place(ref: NamedRef | null) {
  return ref ? (ref.code ?? ref.name ?? "—") : "—";
}

function countLabel(value: CountAndLatest | number | null): string {
  if (value === null) return "Not visible to your role";
  if (typeof value === "number") return String(value);
  if (value.count === 0) return "0";
  return `${value.count} · last ${formatHospitalDateTime(value.latest_at, "—")}`;
}

type ActionMessage = { tone: "red" | "amber" | "green"; text: string };

export default function AdmissionDetailPanel({
  detail,
  loading,
  error,
  empty,
  stale,
  mayAdmit = false,
  mayTransfer = false,
  mayCancelRequest = false,
  actionMessage = null,
  onRequestAdmit,
  onRequestTransfer,
  onRequestCancel,
}: {
  detail: AdmissionDetail | null;
  loading: boolean;
  error: string | null;
  empty: boolean;
  stale: boolean;
  /** The role's `admit` capability, from the server session. */
  mayAdmit?: boolean;
  /** The role's `transfer` capability, from the server session. */
  mayTransfer?: boolean;
  /** The role's `cancel_request` capability, from the server session. */
  mayCancelRequest?: boolean;
  actionMessage?: ActionMessage | null;
  onRequestAdmit?: () => void;
  onRequestTransfer?: () => void;
  onRequestCancel?: () => void;
}) {
  if (error) {
    return (
      <section aria-label="Admission detail" className="rounded-lg border border-slate-200 bg-white p-4 cl-body text-red-700">
        {error}
      </section>
    );
  }
  if (!detail) {
    return (
      <section aria-label="Admission detail" className="flex items-center justify-center rounded-lg border border-slate-200 bg-white p-4 cl-body text-slate-500">
        {loading ? "Loading admission…" : empty ? "Nothing to show in this lane." : "Select an admission."}
      </section>
    );
  }

  const clearance = clearanceLabel(detail.clearance);
  const financial = financialLabel(detail.financial);
  const diagnosis = detail.diagnosis;

  return (
    <section
      aria-label="Admission detail"
      className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white"
    >
      <header className="shrink-0 px-3 py-2">
        <div className="flex items-start gap-2">
          <div className="min-w-0 flex-1">
            <h2 className="truncate cl-head-lg font-bold text-slate-950">{detail.patient?.name ?? "—"}</h2>
            <p className="flex flex-wrap items-center gap-x-2 font-mono cl-meta text-slate-600">
              <span>{orDash(detail.patient?.mrn)}</span>
              <span>{ageSexLabel(detail.patient)}</span>
              <span className="font-bold text-slate-800">{detail.reference}</span>
              <span>LOS {formatLengthOfStay(detail.length_of_stay)}</span>
            </p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-1">
            <AdmissionLanePill lane={detail.lane} />
            <span className="cl-micro text-slate-500">State: {detail.state_label}</span>
          </div>
        </div>
        {stale ? (
          <p className="mt-1 rounded border border-amber-200 bg-amber-50 px-2 py-0.5 cl-meta text-amber-900">
            Showing the last loaded version; the refresh did not complete.
          </p>
        ) : null}
        {mayAdmit && detail.can_admit && onRequestAdmit ? (
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={onRequestAdmit}
              className="h-8 rounded-md bg-sky-700 px-3 cl-meta font-bold text-white hover:bg-sky-800"
            >
              Admit to bed…
            </button>
            <span className="cl-micro text-slate-500">Assigns the bed and admits in one step.</span>
          </div>
        ) : null}
        {mayTransfer && detail.can_transfer && onRequestTransfer ? (
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={onRequestTransfer}
              className="h-8 rounded-md bg-sky-700 px-3 cl-meta font-bold text-white hover:bg-sky-800"
            >
              Transfer patient…
            </button>
            <span className="cl-micro text-slate-500">Moves the patient to another available bed.</span>
          </div>
        ) : null}
        {mayCancelRequest && detail.can_cancel_request && onRequestCancel ? (
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={onRequestCancel}
              className="h-8 rounded-md border border-red-300 bg-white px-3 cl-meta font-bold text-red-800 hover:bg-red-50"
            >
              Cancel request…
            </button>
            <span className="cl-micro text-slate-500">The patient has not been admitted yet.</span>
          </div>
        ) : null}
        {actionMessage ? (
          <p
            role="status"
            className={`mt-2 rounded border px-2 py-1 cl-meta ${
              actionMessage.tone === "red"
                ? "border-red-300 bg-red-50 text-red-900"
                : actionMessage.tone === "amber"
                  ? "border-amber-300 bg-amber-50 text-amber-900"
                  : "border-emerald-300 bg-emerald-50 text-emerald-900"
            }`}
          >
            {actionMessage.text}
          </p>
        ) : null}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {detail.review_reasons.length ? (
          <div role="alert" className="mx-3 mb-2 rounded-md border border-red-300 bg-red-50 px-3 py-2">
            <p className="cl-meta font-bold uppercase tracking-wide text-red-800">Needs review</p>
            <ul className="mt-1 list-disc pl-5 cl-body text-red-900">
              {detail.review_reasons.map((reason) => (
                <li key={reason.code}>{reason.message}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <Section title="Location">
          <dl className="grid grid-cols-4 gap-2">
            <Field label="Ward">{place(detail.location.ward)}</Field>
            <Field label="Room">{place(detail.location.room)}</Field>
            <Field label="Bed">{place(detail.location.bed)}</Field>
            <Field label="Bed state">
              {detail.bed_ownership
                ? `${detail.bed_ownership.bed_state}${
                    detail.bed_ownership.consistent === false ? " — does not match" : ""
                  }`
                : "—"}
            </Field>
          </dl>
        </Section>

        <Section title="Visit">
          <p className={`cl-body ${detail.encounter.legacy || detail.encounter.restricted ? "text-slate-500" : "text-slate-900"}`}>
            {encounterLabel(detail.encounter)}
          </p>
        </Section>

        <Section title="Clinical">
          <dl className="grid grid-cols-2 gap-2">
            <Field label="Physician">{detail.physician?.name ?? "—"}</Field>
            <Field label="Diagnosis">
              {diagnosis === null
                ? "—"
                : diagnosis.restricted
                  ? "Not visible to your role"
                  : [diagnosis.code, diagnosis.name].filter(Boolean).join(" · ") || "—"}
            </Field>
          </dl>
          <div className="mt-2">
            <dt className="cl-micro font-bold uppercase tracking-wide text-slate-500">Admission reason</dt>
            <dd className="whitespace-pre-wrap cl-secondary text-slate-800">{detail.admission_reason ?? "—"}</dd>
          </div>
        </Section>

        <Section title="Operational">
          <dl className="grid grid-cols-3 gap-2">
            <Field label="Admitted">{formatHospitalDateTime(detail.admitted_at, "—")}</Field>
            <Field label="Expected discharge">{formatHospitalDateTime(detail.expected_discharge_at, "—")}</Field>
            <Field label="Discharged">{formatHospitalDateTime(detail.discharged_at, "—")}</Field>
            <Field label="Transfers">{detail.transfer_count}</Field>
            <Field label="Nursing rounds">{countLabel(detail.nursing.rounds)}</Field>
            <Field label="Nursing notes">{countLabel(detail.nursing.notes)}</Field>
            <Field label="Care plans">{countLabel(detail.nursing.care_plans)}</Field>
            <Field label="Medication admin.">{countLabel(detail.nursing.medication_administrations)}</Field>
            <Field label="Procedures">{countLabel(detail.linkages.procedure_requests)}</Field>
            <Field label="Pending lab">{countLabel(detail.linkages.pending_laboratory_requests)}</Field>
            <Field label="Pending imaging">{countLabel(detail.linkages.pending_radiology_requests)}</Field>
          </dl>
        </Section>

        <Section title="Financial clearance">
          <p
            className={`cl-body ${
              clearance.tone === "warn" ? "text-amber-900" : clearance.tone === "ok" ? "text-emerald-800" : "text-slate-600"
            }`}
          >
            {clearance.text}
          </p>
        </Section>

        <Section title="Inpatient financial state">
          <p
            className={`cl-body ${
              financial.tone === "warn" ? "text-amber-900" : financial.tone === "ok" ? "text-emerald-800" : "text-slate-600"
            }`}
          >
            {financial.text}
          </p>
          {detail.financial?.review_reasons.length ? (
            <ul className="mt-1 list-disc pl-5 cl-meta text-amber-900">
              {detail.financial.review_reasons.map((reason) => (
                <li key={reason.code}>{reason.message}</li>
              ))}
            </ul>
          ) : null}
        </Section>

        <Section title="Transfer history">
          {detail.transfers.length === 0 ? (
            <p className="cl-body text-slate-500">No transfers.</p>
          ) : (
            <table className="w-full cl-meta">
              <thead>
                <tr className="text-left cl-micro uppercase tracking-wide text-slate-500">
                  <th className="py-0.5 pr-2 font-bold">When</th>
                  <th className="py-0.5 pr-2 font-bold">From</th>
                  <th className="py-0.5 pr-2 font-bold">To</th>
                  <th className="py-0.5 pr-2 font-bold">By</th>
                  <th className="py-0.5 font-bold">Reason</th>
                </tr>
              </thead>
              <tbody className="font-mono text-slate-800">
                {detail.transfers.map((transfer) => (
                  <tr key={transfer.id} className="border-t border-slate-100 align-top">
                    <td className="py-1 pr-2">{formatHospitalDateTime(transfer.transferred_at, "—")}</td>
                    <td className="py-1 pr-2">{[place(transfer.from.ward), place(transfer.from.room), place(transfer.from.bed)].join(" / ")}</td>
                    <td className="py-1 pr-2">{[place(transfer.to.ward), place(transfer.to.room), place(transfer.to.bed)].join(" / ")}</td>
                    <td className="py-1 pr-2 font-sans">{transfer.transferred_by ?? "—"}</td>
                    <td className="py-1 font-sans">{transfer.reason ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>
    </section>
  );
}

"use client";

/**
 * Guided visit registration.
 *
 * Creation goes through exactly one call: POST /api/reception/visits, which
 * forwards to hospital.reception.workflow.create_visit(). This screen never
 * creates a patient, appointment, encounter, card issuance or charge itself,
 * and never sends both patient_id and patient_values.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import ErrorBanner from "@/components/clinical/error-banner";
import ChargePreview from "@/components/reception/charge-preview";
import NewPatientForm from "@/components/reception/new-patient-form";
import PatientSearch from "@/components/reception/patient-search";
import VisitDetailsForm, {
  type VisitFormValues,
} from "@/components/reception/visit-details-form";
import { messageFromPayload } from "@/lib/api-error";
import {
  formatGender,
  hospitalLocalToOdooUtc,
  hospitalNowLocalInput,
} from "@/lib/reception-format";
import { PATIENT_VALUE_KEYS } from "@/types/reception";
import type {
  ApiEnvelope,
  CreateVisitPayload,
  NewPatientValues,
  ReceptionPatientSearchResult,
  ReceptionVisitDetail,
  ReceptionVisitPreview,
} from "@/types/reception";

type PatientMode = "existing" | "new";

const EMPTY_PATIENT: NewPatientValues = { name: "" };

function Section({
  step,
  title,
  description,
  children,
}: {
  step: number;
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-baseline gap-3">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-emerald-700 text-xs font-bold text-white">
          {step}
        </span>
        <div>
          <h2 className="text-base font-semibold text-slate-950">{title}</h2>
          {description ? (
            <p className="mt-0.5 text-sm text-slate-600">{description}</p>
          ) : null}
        </div>
      </div>
      {children}
    </section>
  );
}

export default function NewVisitClient() {
  const router = useRouter();

  const [mode, setMode] = useState<PatientMode>("existing");
  const [selectedPatient, setSelectedPatient] =
    useState<ReceptionPatientSearchResult | null>(null);
  const [patientValues, setPatientValues] =
    useState<NewPatientValues>(EMPTY_PATIENT);

  const [visit, setVisit] = useState<VisitFormValues>({
    visit_type: "routine",
    department_id: "",
    doctor_id: "",
    appointment_date: hospitalNowLocalInput(),
    reason: "",
    triage_destination_id: "",
  });

  const [preview, setPreview] = useState<ReceptionVisitPreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Preview key: only the inputs that change the quote.
  const previewKey = useMemo(
    () =>
      JSON.stringify({
        patientId: mode === "existing" ? (selectedPatient?.id ?? null) : null,
        visitType: visit.visit_type,
        departmentId: visit.department_id,
        doctorId: visit.doctor_id,
      }),
    [mode, selectedPatient, visit.visit_type, visit.department_id, visit.doctor_id],
  );

  useEffect(() => {
    const { patientId, visitType, departmentId, doctorId } = JSON.parse(
      previewKey,
    ) as {
      patientId: number | null;
      visitType: string;
      departmentId: string;
      doctorId: string;
    };

    // Without a department there is nothing to quote. The render path derives
    // a null preview (see `activePreview`); clearing state from the effect
    // body instead would trigger a cascading render.
    if (!departmentId) {
      return;
    }

    const controller = new AbortController();

    async function run() {
      setPreviewLoading(true);
      const params = new URLSearchParams();
      // Omitted entirely for a new patient -- the API then quotes a first card.
      if (patientId) params.set("patient_id", String(patientId));
      params.set("visit_type", visitType);
      params.set("department_id", departmentId);
      if (doctorId) params.set("doctor_id", doctorId);

      try {
        const response = await fetch(
          `/api/reception/visit-preview?${params.toString()}`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload =
          (await response.json()) as ApiEnvelope<ReceptionVisitPreview>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setPreviewError(
            messageFromPayload(payload, "Unable to calculate the fees."),
          );
          setPreview(null);
          return;
        }
        setPreviewError(null);
        setPreview(payload.data);
      } catch {
        if (controller.signal.aborted) return;
        setPreviewError("Unable to reach the reception service.");
        setPreview(null);
      } finally {
        if (!controller.signal.aborted) setPreviewLoading(false);
      }
    }

    void run();
    return () => controller.abort();
  }, [previewKey]);

  // Derived so a quote from a previous department never lingers.
  const hasDepartment = Boolean(visit.department_id);
  const activePreview = hasDepartment ? preview : null;
  const activePreviewError = hasDepartment ? previewError : null;
  const activePreviewLoading = hasDepartment && previewLoading;

  const patientReady =
    mode === "existing"
      ? Boolean(selectedPatient)
      : patientValues.name.trim().length > 0;
  const canSubmit =
    patientReady && Boolean(visit.department_id) && !submitting;

  const handleSelectExisting = useCallback(
    (patient: ReceptionPatientSearchResult) => {
      setSelectedPatient(patient);
      setSubmitError(null);
    },
    [],
  );

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;

    setSubmitting(true);
    setSubmitError(null);

    // Exactly one of patient_id / patient_values.
    const payload: CreateVisitPayload = {
      visit_type: visit.visit_type,
      department_id: Number(visit.department_id),
    };
    if (mode === "existing" && selectedPatient) {
      payload.patient_id = selectedPatient.id;
    } else {
      // Built by iterating the ALLOWLIST, not the form state. Any key the API
      // does not accept is structurally unable to reach the request, so a
      // stray field can never fail the whole atomic registration again.
      // Empty values are omitted rather than sent as "".
      const trimmed: NewPatientValues = { name: patientValues.name.trim() };
      PATIENT_VALUE_KEYS.forEach((key) => {
        if (key === "name") return;
        const value = patientValues[key];
        if (typeof value === "string" && value.trim()) {
          trimmed[key] = value.trim();
        }
      });
      payload.patient_values = trimmed;
    }
    if (visit.doctor_id) payload.doctor_id = Number(visit.doctor_id);
    if (visit.triage_destination_id) {
      payload.triage_destination_id = Number(visit.triage_destination_id);
    }
    if (visit.reason.trim()) payload.reason = visit.reason.trim();
    const appointmentDate = hospitalLocalToOdooUtc(visit.appointment_date);
    if (appointmentDate) payload.appointment_date = appointmentDate;

    try {
      const response = await fetch("/api/reception/visits", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = (await response.json()) as ApiEnvelope<ReceptionVisitDetail>;

      if (!response.ok || !result.success) {
        // Entered values are intentionally left untouched so nothing is retyped.
        setSubmitError(
          messageFromPayload(result, "Unable to register the visit."),
        );
        return;
      }

      router.push(`/reception/visits/${result.data.appointment.id}`);
      router.refresh();
    } catch {
      setSubmitError("Unable to reach the reception service.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      <Section
        step={1}
        title="Patient"
        description="Search for an existing record before registering a new one."
      >
        <div
          role="group"
          aria-label="Patient type"
          className="mb-4 inline-flex rounded-md border border-slate-300 p-0.5"
        >
          {(
            [
              ["existing", "Existing patient"],
              ["new", "Register new patient"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={mode === value}
              onClick={() => {
                setMode(value);
                setSubmitError(null);
              }}
              className={`rounded px-4 py-1.5 text-sm font-semibold transition ${
                mode === value
                  ? "bg-emerald-700 text-white"
                  : "text-slate-700 hover:bg-slate-100"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {mode === "existing" ? (
          <>
            <PatientSearch
              selected={selectedPatient}
              onSelect={handleSelectExisting}
            />
            {selectedPatient ? (
              <div className="mt-4 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm">
                <span className="font-semibold text-emerald-900">
                  Selected: {selectedPatient.name}
                </span>
                <span className="mt-0.5 block text-emerald-800">
                  {selectedPatient.identification_code ?? "No MRN"} ·{" "}
                  {selectedPatient.age ?? "—"} yrs ·{" "}
                  {formatGender(selectedPatient.gender)}
                </span>
              </div>
            ) : null}
          </>
        ) : (
          <NewPatientForm
            values={patientValues}
            onChange={setPatientValues}
          />
        )}
      </Section>

      <Section step={2} title="Visit details">
        <VisitDetailsForm values={visit} onChange={setVisit} />
      </Section>

      <Section
        step={3}
        title="Fees"
        description="Quoted before anything is created."
      >
        <ChargePreview
          preview={activePreview}
          loading={activePreviewLoading}
          error={activePreviewError}
        />
      </Section>

      <ErrorBanner message={submitError} title="Unable to register the visit" />

      <div className="flex flex-wrap items-center justify-end gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <Link
          href="/reception"
          className="inline-flex h-11 items-center justify-center rounded-md border border-slate-300 bg-white px-5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
        >
          Cancel
        </Link>
        <button
          type="submit"
          disabled={!canSubmit}
          className="h-11 rounded-md bg-emerald-700 px-6 text-sm font-semibold text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-slate-400"
        >
          {submitting ? "Registering…" : "Register visit"}
        </button>
      </div>
    </form>
  );
}

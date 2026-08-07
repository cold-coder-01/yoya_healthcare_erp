"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import BillingWarning from "@/components/clinical/billing-warning";
import DoctorSelect from "@/components/clinical/doctor-select";
import ErrorBanner from "@/components/clinical/error-banner";
import StatusBadge from "@/components/clinical/status-badge";
import {
  formatBloodGroup,
  formatHospitalDateTime,
} from "@/lib/clinical-format";
import type {
  ApiEnvelope,
  ApiErrorShape,
  ClinicalEvaluation,
  EvaluationDetail,
  EvaluationSavePayload,
} from "@/types/clinical";

type FormState = {
  weight: string;
  height: string;
  temperature: string;
  heart_rate: string;
  respiratory_rate: string;
  systolic_bp: string;
  diastolic_bp: string;
  spo2: string;
  rbs: string;
  head_circumference: string;
  pain_level: string;
  pain_note: string;
  triage_priority: string;
  chief_complaint: string;
  triage_notes: string;
  physician_id: string;
  // assigned_nurse_id is deliberately NOT part of the form: the server derives
  // it from env.user. See buildPayload().
};

const EMPTY_FORM: FormState = {
  weight: "",
  height: "",
  temperature: "",
  heart_rate: "",
  respiratory_rate: "",
  systolic_bp: "",
  diastolic_bp: "",
  spo2: "",
  rbs: "",
  head_circumference: "",
  pain_level: "",
  pain_note: "",
  triage_priority: "",
  chief_complaint: "",
  triage_notes: "",
  physician_id: "",
};

const NUMERIC_FIELDS = [
  "weight",
  "height",
  "temperature",
  "heart_rate",
  "respiratory_rate",
  "systolic_bp",
  "diastolic_bp",
  "spo2",
  "rbs",
  "head_circumference",
] as const;

function valueToString(value: string | number | null | undefined) {
  return value === null || value === undefined ? "" : String(value);
}

/**
 * `fallbackPhysicianId` is the appointment's own doctor. It seeds the picker
 * for an evaluation that has not recorded a physician yet -- typically a brand
 * new one -- so the clinician who is actually booked for the visit is
 * pre-selected instead of the field opening empty.
 */
function formFromEvaluation(
  evaluation: ClinicalEvaluation,
  fallbackPhysicianId?: number | null,
): FormState {
  if (!evaluation) {
    return {
      ...EMPTY_FORM,
      physician_id: valueToString(fallbackPhysicianId ?? undefined),
    };
  }

  return {
    weight: valueToString(evaluation.weight),
    height: valueToString(evaluation.height),
    temperature: valueToString(evaluation.temperature),
    heart_rate: valueToString(evaluation.heart_rate),
    respiratory_rate: valueToString(evaluation.respiratory_rate),
    systolic_bp: valueToString(evaluation.systolic_bp),
    diastolic_bp: valueToString(evaluation.diastolic_bp),
    spo2: valueToString(evaluation.spo2),
    rbs: valueToString(evaluation.rbs),
    head_circumference: valueToString(evaluation.head_circumference),
    pain_level: valueToString(evaluation.pain_level),
    pain_note: valueToString(evaluation.pain_note),
    triage_priority: valueToString(evaluation.triage_priority),
    chief_complaint: valueToString(evaluation.chief_complaint),
    triage_notes: valueToString(evaluation.triage_notes),
    physician_id: valueToString(
      evaluation.physician_id?.id ?? fallbackPhysicianId ?? undefined,
    ),
  };
}

function safeMessage(payload: unknown, fallback: string) {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof payload.error === "object" &&
    payload.error !== null &&
    "message" in payload.error &&
    typeof payload.error.message === "string"
  ) {
    return payload.error.message;
  }
  return fallback;
}

function getError(payload: unknown): ApiErrorShape | null {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof payload.error === "object" &&
    payload.error !== null
  ) {
    return payload.error as ApiErrorShape;
  }
  return null;
}

/** True when Odoo signalled a billing-clearance block rather than a plain failure. */
function isBillingClearanceError(status: number, apiError: ApiErrorShape | null) {
  if (status !== 409 || !apiError) {
    return false;
  }
  return (
    apiError.code === "billing_clearance_required" ||
    typeof apiError.billing_clearance_message === "string"
  );
}

function buildPayload(form: FormState): EvaluationSavePayload {
  const payload: EvaluationSavePayload = {
    pain_level: form.pain_level || null,
    pain_note: form.pain_note || null,

    chief_complaint: form.chief_complaint || null,
    triage_notes: form.triage_notes || null,
    physician_id: form.physician_id ? Number(form.physician_id) : null,
    // assigned_nurse_id is intentionally OMITTED, not sent as null.
    //
    // hospital.patient.evaluation.assigned_nurse_id carries
    // `default=lambda self: self.env.user`, and Odoo applies a default only
    // when the key is ABSENT from vals. Sending an explicit null therefore
    // suppressed that default and created evaluations with no nurse assigned
    // -- which, for an appointment-less evaluation, is the only thing the
    // nurse record rule matches on. The server derives the identity from the
    // authenticated session; the browser must not supply it.
  };

  if (form.triage_priority) {
    payload.triage_priority = form.triage_priority;
  }

  for (const field of NUMERIC_FIELDS) {
    const raw = form[field].trim();
    if (raw) {
      payload[field] = Number(raw);
    }
  }

  return payload;
}

async function requestEvaluationDetail(numericAppointmentId: number) {
  const response = await fetch(
    `/api/clinical/evaluations/by-appointment/${numericAppointmentId}`,
    { cache: "no-store" },
  );
  const payload = (await response.json()) as ApiEnvelope<EvaluationDetail>;

  if (!response.ok || !payload.success) {
    throw new Error(safeMessage(payload, "Unable to load evaluation."));
  }

  return payload.data;
}

function Field({
  label,
  value,
  onChange,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
}) {
  return (
    <label className="text-sm font-medium text-slate-700">
      {label}
      <input
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm outline-none focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100"
      />
    </label>
  );
}

export default function EvaluationClient({ appointmentId }: { appointmentId: string }) {
  const [detail, setDetail] = useState<EvaluationDetail | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [billingError, setBillingError] = useState<ApiErrorShape | null>(null);

  const numericAppointmentId = useMemo(() => Number(appointmentId), [appointmentId]);

  /**
   * The single source of truth for the billing banner.
   *
   * A live 409 from Start Consultation is the most specific signal and wins;
   * otherwise the billing fields returned in the evaluation detail are used.
   * A malformed or empty error object yields null, so no empty banner renders.
   */
  const billingNotice = useMemo<
    { blocked: boolean; message: string | null; detail: string | null } | null
  >(() => {
    if (billingError) {
      const odooMessage =
        typeof billingError.message === "string" ? billingError.message.trim() : "";
      const clearanceMessage =
        typeof billingError.billing_clearance_message === "string"
          ? billingError.billing_clearance_message.trim()
          : "";

      if (!odooMessage && !clearanceMessage) {
        return null;
      }

      return {
        blocked: true,
        message: odooMessage || clearanceMessage,
        detail: odooMessage ? clearanceMessage || null : null,
      };
    }

    const appointment = detail?.appointment;
    if (!appointment) {
      return null;
    }

    const clearanceMessage =
      typeof appointment.billing_clearance_message === "string"
        ? appointment.billing_clearance_message.trim()
        : "";

    if (!appointment.billing_blocked && !clearanceMessage) {
      return null;
    }

    return {
      blocked: Boolean(appointment.billing_blocked),
      message: clearanceMessage || null,
      detail: null,
    };
  }, [billingError, detail]);

  const loadDetail = useCallback(async () => {
    if (!Number.isInteger(numericAppointmentId) || numericAppointmentId <= 0) {
      setError("Appointment ID is invalid.");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    setBillingError(null);

    try {
      const nextDetail = await requestEvaluationDetail(numericAppointmentId);
      setDetail(nextDetail);
      setForm(
        formFromEvaluation(
          nextDetail.evaluation,
          nextDetail.appointment.doctor_id?.id,
        ),
      );
      // A successful fetch clears whatever transient failure preceded it.
      setError(null);
      setBillingError(null);
    } catch (loadError) {
      setError(
        loadError instanceof Error
          ? loadError.message
          : "Unable to reach the clinical evaluation service.",
      );
    } finally {
      setLoading(false);
    }
  }, [numericAppointmentId]);

  useEffect(() => {
    let active = true;

    async function loadInitialDetail() {
      if (!Number.isInteger(numericAppointmentId) || numericAppointmentId <= 0) {
        setError("Appointment ID is invalid.");
        setLoading(false);
        return;
      }

      try {
        const nextDetail = await requestEvaluationDetail(numericAppointmentId);
        if (active) {
          setDetail(nextDetail);
          setForm(
            formFromEvaluation(
              nextDetail.evaluation,
              nextDetail.appointment.doctor_id?.id,
            ),
          );
          setError(null);
          setBillingError(null);
        }
      } catch (loadError) {
        if (active) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "Unable to reach the clinical evaluation service.",
          );
        }
      } finally {
        if (active) {
          setLoading(false);
        }
      }
    }

    void loadInitialDetail();

    return () => {
      active = false;
    };
  }, [numericAppointmentId]);

  function setField(name: keyof FormState, value: string) {
    setForm((current) => ({ ...current, [name]: value }));
  }

  async function saveDraft() {
    if (!detail || saving || completing) return;
    setSaving(true);
    setError(null);
    setBillingError(null);

    try {
      const response = await fetch(
        `/api/clinical/evaluations/${detail.appointment.id}/save`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildPayload(form)),
        },
      );
      const payload = (await response.json()) as ApiEnvelope<{ evaluation: NonNullable<ClinicalEvaluation> }>;

      if (!response.ok || !payload.success) {
        setError(safeMessage(payload, "Unable to save evaluation."));
        return;
      }

      setDetail((current) =>
        current ? { ...current, evaluation: payload.data.evaluation } : current,
      );
      setForm(
        formFromEvaluation(
          payload.data.evaluation,
          detail.appointment.doctor_id?.id,
        ),
      );
      setError(null);
      setBillingError(null);
    } catch {
      setError("Unable to reach the clinical evaluation service.");
    } finally {
      setSaving(false);
    }
  }

  async function complete() {
    const evaluationId = detail?.evaluation?.id;
    if (!evaluationId || completing || saving) return;
    setCompleting(true);
    setError(null);
    setBillingError(null);

    try {
      const response = await fetch(
        `/api/clinical/evaluations/${evaluationId}/complete`,
        { method: "POST" },
      );
      const payload = (await response.json()) as ApiEnvelope<{ evaluation: NonNullable<ClinicalEvaluation> }>;

      if (!response.ok || !payload.success) {
        setError(safeMessage(payload, "Unable to complete evaluation."));
        return;
      }

      setDetail((current) =>
        current ? { ...current, evaluation: payload.data.evaluation } : current,
      );
      setError(null);
      setBillingError(null);
    } catch {
      setError("Unable to reach the clinical evaluation service.");
    } finally {
      setCompleting(false);
    }
  }

  async function startConsultation() {
    if (!detail || detail.evaluation?.state !== "done" || starting) return;
    setStarting(true);
    setError(null);
    setBillingError(null);

    try {
      const response = await fetch(
        `/api/clinical/appointments/${detail.appointment.id}/start-consultation`,
        { method: "POST" },
      );
      const payload = await response.json();

      if (!response.ok || !payload.success) {
        const apiError = getError(payload);

        // A billing 409 is a billing signal, not a general failure. Setting
        // only one of the two states is what keeps a single banner on screen.
        if (isBillingClearanceError(response.status, apiError)) {
          setBillingError(apiError);
        } else {
          setError(safeMessage(payload, "Unable to start consultation."));
        }
        return;
      }

      await loadDetail();
    } catch {
      setError("Unable to reach the clinical appointment service.");
    } finally {
      setStarting(false);
    }
  }

  if (loading) {
    return <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-600">Loading evaluation...</div>;
  }

  if (!detail) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-6">
        <div className="text-sm text-red-700">{error ?? "Evaluation detail is unavailable."}</div>
        <Link href="/triage" className="mt-4 inline-block text-sm font-semibold text-emerald-700">Back to queue</Link>
      </div>
    );
  }

  const evaluationDone = detail.evaluation?.state === "done";
  const medicalAlerts = detail.patient.medical_alerts ?? [];

  return (
    <div className="space-y-4">
      <div className="flex justify-between gap-3">
        <Link href="/triage" className="text-sm font-semibold text-emerald-700 hover:text-emerald-900">Back to queue</Link>
        <div className="flex gap-2">
          <button type="button" onClick={saveDraft} disabled={saving || completing || evaluationDone} className="h-9 rounded-md bg-emerald-700 px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-400">
            {saving ? "Saving..." : "Save Draft"}
          </button>
          <button type="button" onClick={complete} disabled={!detail.evaluation?.id || saving || completing || evaluationDone} className="h-9 rounded-md bg-slate-900 px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-400">
            {completing ? "Completing..." : "Complete Evaluation"}
          </button>
          <button type="button" onClick={startConsultation} disabled={!evaluationDone || starting} className="h-9 rounded-md bg-teal-700 px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:bg-slate-400">
            {starting ? "Starting..." : "Start Consultation"}
          </button>
        </div>
      </div>

      <ErrorBanner message={error} />
      {billingNotice ? (
        <BillingWarning
          blocked={billingNotice.blocked}
          message={billingNotice.message}
          detail={billingNotice.detail}
        />
      ) : null}

      <section className="grid gap-4 xl:grid-cols-[1.3fr_0.7fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <div className="text-2xl font-semibold text-slate-950">{detail.patient.name}</div>
              <div className="mt-1 text-sm text-slate-500">{detail.patient.identification_code ?? "No MRN"} | {detail.patient.age ?? "-"} | {detail.patient.gender ?? "-"}</div>
              <div className="mt-2 text-sm text-slate-700">Phone: {detail.patient.mobile ?? detail.patient.phone ?? "-"} | Blood group: {formatBloodGroup(detail.patient.blood_group)}</div>
            </div>
            <div className="text-right text-sm text-slate-600">
              <div className="font-semibold text-slate-950">{detail.appointment.appointment_code ?? detail.appointment.id}</div>
              <div>{detail.appointment.doctor_id?.name ?? "No doctor"}</div>
              <div>{detail.appointment.department_id?.name ?? "No department"}</div>
              <div className="mt-2"><StatusBadge value={detail.appointment.state} /></div>
            </div>
          </div>
          {medicalAlerts.length ? (
            <div className="mt-4 flex flex-wrap gap-2">
              {medicalAlerts.map((alert) => (
                <span key={alert.id} className="rounded-full bg-red-50 px-2.5 py-1 text-xs font-semibold text-red-700 ring-1 ring-red-100">
                  {alert.name} {alert.severity ? `(${alert.severity})` : ""}
                </span>
              ))}
            </div>
          ) : null}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-4 text-sm shadow-sm">
          <div className="font-semibold text-slate-950">Appointment Summary</div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-slate-600">
            <div>Date</div><div className="text-slate-900">{formatHospitalDateTime(detail.appointment.appointment_date)}</div>
            <div>Reason</div><div className="text-slate-900">{detail.appointment.reason ?? "-"}</div>
            <div>Encounter</div><div className="text-slate-900">{detail.encounter?.name ?? "Not linked"}</div>
            <div>Evaluation</div><div><StatusBadge value={detail.evaluation?.status} /></div>
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[0.75fr_1.25fr]">
        <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div className="font-semibold text-slate-950">Previous Vitals</div>
          <div className="mt-3 grid grid-cols-2 gap-2 text-sm text-slate-600">
            {detail.previous_vitals ? (
              Object.entries(detail.previous_vitals)
                .filter(([key]) => key !== "evaluation_id")
                .map(([key, value]) => (
                  <div key={key} className="flex justify-between gap-3 border-b border-slate-100 py-1">
                    <span className="capitalize">{key.replaceAll("_", " ")}</span>
                    <span className="font-medium text-slate-900">{value ?? "-"}</span>
                  </div>
                ))
            ) : (
              <div className="col-span-2 text-slate-500">No completed previous vitals found.</div>
            )}
          </div>
        </div>

        <form className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm" onSubmit={(event) => event.preventDefault()}>
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <div className="font-semibold text-slate-950">Current Evaluation</div>
              <div className="text-sm text-slate-500">Draft saves upsert the Odoo evaluation record.</div>
            </div>
            <StatusBadge value={detail.evaluation?.state ?? "draft"} />
          </div>

          <div className="space-y-6">
            <section>
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Vitals</h2>
              <div className="mt-3 grid gap-3 md:grid-cols-3 lg:grid-cols-5">
                {NUMERIC_FIELDS.map((field) => (
                  <Field key={field} label={field.replaceAll("_", " ")} type="number" value={form[field]} onChange={(value) => setField(field, value)} />
                ))}
              </div>
            </section>

            <section className="grid gap-4 lg:grid-cols-2">
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Complaint</h2>
                <textarea value={form.chief_complaint} onChange={(event) => setField("chief_complaint", event.target.value)} className="mt-3 min-h-28 w-full rounded-md border border-slate-300 p-3 text-sm" placeholder="Chief complaint" />
              </div>
              <div>
                <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Notes</h2>
                <textarea value={form.triage_notes} onChange={(event) => setField("triage_notes", event.target.value)} className="mt-3 min-h-28 w-full rounded-md border border-slate-300 p-3 text-sm" placeholder="Triage notes" />
              </div>
            </section>

            <section className="grid gap-4 lg:grid-cols-3">
              <label className="text-sm font-medium text-slate-700">
                Pain level
                <select value={form.pain_level} onChange={(event) => setField("pain_level", event.target.value)} className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm">
                  <option value="">Not set</option>
                  {Array.from({ length: 11 }, (_, index) => String(index)).map((value) => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <Field label="Pain note" value={form.pain_note} onChange={(value) => setField("pain_note", value)} />
              <label className="text-sm font-medium text-slate-700">
                Triage priority
                <select value={form.triage_priority} onChange={(event) => setField("triage_priority", event.target.value)} className="mt-1 h-10 w-full rounded-md border border-slate-300 px-3 text-sm">
                  <option value="">Not set</option>
                  <option value="routine">Routine</option>
                  <option value="urgent">Urgent</option>
                  <option value="emergency">Emergency</option>
                </select>
              </label>
            </section>

            <section>
              <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">Triage Assessment</h2>
              <div className="mt-3 grid gap-3 md:grid-cols-3">
                <DoctorSelect
                  label="Physician"
                  value={form.physician_id}
                  onChange={(value) => setField("physician_id", value)}
                  departmentId={detail.appointment.department_id?.id ?? null}
                  currentDoctor={detail.evaluation?.physician_id ?? detail.appointment.doctor_id}
                  helpText={
                    detail.appointment.department_id
                      ? `Doctors in ${detail.appointment.department_id.name}`
                      : undefined
                  }
                />
                <label className="text-sm font-medium text-slate-700">
                  Assigned nurse
                  <input
                    readOnly
                    value={detail.evaluation?.assigned_nurse_id?.name ?? "Assigned on first save"}
                    className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-slate-50 px-3 text-sm text-slate-600"
                  />
                  <span className="mt-1 block text-xs font-normal text-slate-500">
                    Taken from your signed-in account.
                  </span>
                </label>
                <label className="text-sm font-medium text-slate-700">
                  Encounter
                  <input readOnly value={detail.encounter?.name ?? "Not linked"} className="mt-1 h-10 w-full rounded-md border border-slate-200 bg-slate-50 px-3 text-sm text-slate-600" />
                </label>
              </div>
            </section>
          </div>
        </form>
      </section>
    </div>
  );
}



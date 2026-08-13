"use client";

/**
 * Front Desk B2.1 -- search/register a patient and open today's visit, without
 * leaving the workstation.
 *
 * NOTHING here is a second registration system. The whole flow is one POST to
 * the existing BFF route /api/reception/visits, which forwards to the Odoo
 * endpoint that calls hospital.reception.workflow.create_visit() -- the single
 * authoritative transaction that creates the patient (when new), the
 * appointment, the encounter, the billing account, the consultation charge and
 * the first card, and rolls all of it back together on any failure.
 *
 * This component therefore owns NO business rule. It does not stamp
 * appointment_date, registered_at or reception_workflow_managed; the workflow
 * does. It does not derive a queue stage; front_desk_stage is computed from the
 * appointment state and the evaluation, so a new visit surfaces as 'intake' on
 * its own. It does not decide who may register; RECEPTION_GROUPS already
 * includes the Front Desk Nurse group and the API re-checks on every call.
 *
 * DEFERRED TO B2.2 (all optional in the API contract, unlike department):
 * doctor assignment, chief complaint (`reason`), triage destination, visit-fee
 * preview, vitals, priority and triage completion.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";

import PatientSearch from "@/components/reception/patient-search";
import { codeFromPayload, messageFromPayload } from "@/lib/api-error";
import type {
  ApiEnvelope,
  CreateVisitPayload,
  Department,
  NewPatientValues,
  ReceptionPatientSearchResult,
  ReceptionVisitDetail,
} from "@/types/reception";

/**
 * Mirrors VISIT_TYPES in yoya_emr_api/controllers/reception.py. The API
 * validates the value and 400s on anything else, so this list is a convenience,
 * never the control.
 */
const VISIT_TYPES = [
  { value: "routine", label: "Routine" },
  { value: "emergency", label: "Emergency" },
  { value: "follow_up", label: "Follow-up" },
  { value: "referral", label: "Referral" },
];

/**
 * The fast-registration subset.
 *
 * hospital.patient marks only `name` required (identification_code is required
 * but sequence-assigned with a "New" default, and the API rejects any attempt
 * to supply it). Everything else here is optional and included because a desk
 * needs it to tell two patients apart later -- no speculative required fields.
 * The full field set stays on the legacy guided form until B2.2 decides what
 * the desk actually wants.
 */
type NewPatientForm = {
  name: string;
  gender: string;
  date_of_birth: string;
  phone: string;
};

const EMPTY_PATIENT: NewPatientForm = {
  name: "",
  gender: "",
  date_of_birth: "",
  phone: "",
};

type FailureKind =
  | "validation"
  | "authorization"
  | "session"
  | "conflict"
  | "server";

type Failure = { kind: FailureKind; message: string };

const FAILURE_LABEL: Record<FailureKind, string> = {
  validation: "Check the form",
  authorization: "Not permitted",
  session: "Session expired",
  conflict: "Cannot complete yet",
  server: "Service error",
};

/**
 * Classify a failed create.
 *
 * The Odoo message is ALWAYS preferred -- it is the specific, actionable one
 * ("A patient name is required.", "Doctor X is not in department Y."). The kind
 * only picks the label and the styling, so an error is never flattened into a
 * generic empty state.
 */
function describeFailure(status: number, payload: unknown): Failure {
  const code = codeFromPayload(payload);

  if (status === 401 || code === "unauthenticated") {
    return {
      kind: "session",
      message: "Your session has expired. Sign in again to register the visit.",
    };
  }
  if (status === 403 || code === "access_denied") {
    return {
      kind: "authorization",
      message: messageFromPayload(
        payload,
        "You are not allowed to register a visit.",
      ),
    };
  }
  if (status === 400 || status === 422 || code === "validation_error") {
    return {
      kind: "validation",
      message: messageFromPayload(payload, "The visit details are incomplete."),
    };
  }
  if (status === 404 || status === 409) {
    return {
      kind: "conflict",
      message: messageFromPayload(payload, "The visit could not be opened."),
    };
  }
  return {
    kind: "server",
    message: messageFromPayload(payload, "Unable to register the visit."),
  };
}

const FIELD =
  "mt-1 h-9 w-full rounded border border-slate-300 bg-white px-2 text-xs outline-none focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100";
const LABEL = "block text-[11px] font-semibold text-slate-600";

/**
 * Mounted only while open (the parent renders it conditionally), so every
 * registration starts from the useState initializers below. That is what keeps
 * a cancelled registration from leaking into the next patient -- there is no
 * reset effect, because there is no stale state to reset.
 */
export default function FrontDeskNewVisitDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  /** Called with the new appointment id once Odoo has committed the visit. */
  onCreated: (appointmentId: number) => void;
}) {
  const titleId = useId();
  const [mode, setMode] = useState<"existing" | "new">("existing");
  const [selectedPatient, setSelectedPatient] =
    useState<ReceptionPatientSearchResult | null>(null);
  const [patient, setPatient] = useState<NewPatientForm>(EMPTY_PATIENT);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [departmentId, setDepartmentId] = useState("");
  const [visitType, setVisitType] = useState("routine");
  const [departmentsError, setDepartmentsError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);

  // Guards the window between the click and setSubmitting(true) taking effect.
  // `disabled` alone leaves a real gap: a double click, or Enter held down in a
  // text input, can fire submit twice in the same tick and open two visits --
  // and create_visit() has NO server-side duplicate-visit or idempotency rule
  // to catch it (verified; only first-card issuance is protected, by a partial
  // unique index). This ref is therefore the actual protection.
  const inFlight = useRef(false);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadDepartments() {
      try {
        const response = await fetch("/api/reference/departments", {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = (await response.json()) as ApiEnvelope<{
          departments: Department[];
        }>;
        if (controller.signal.aborted) return;

        if (!response.ok || !payload.success) {
          setDepartmentsError(
            messageFromPayload(payload, "Unable to load departments."),
          );
          return;
        }
        setDepartmentsError(null);
        setDepartments(payload.data.departments ?? []);
      } catch {
        if (!controller.signal.aborted) {
          setDepartmentsError("Unable to reach the reference service.");
        }
      }
    }

    void loadDepartments();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !inFlight.current) {
        onClose();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  const handleSelect = useCallback(
    (found: ReceptionPatientSearchResult) => {
      setSelectedPatient(found);
      setFailure(null);
    },
    [],
  );

  const patientReady =
    mode === "existing"
      ? Boolean(selectedPatient)
      : patient.name.trim().length > 0;
  const canSubmit = patientReady && Boolean(departmentId) && !submitting;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (inFlight.current || !canSubmit) {
      return;
    }
    inFlight.current = true;
    setSubmitting(true);
    setFailure(null);

    // Exactly one of patient_id / patient_values -- the API 400s on both.
    //
    // appointment_date is deliberately OMITTED: create_visit() defaults it to
    // fields.Datetime.now() and stamps registered_at from the same clock. This
    // is a walk-in arriving now, and sending a client-computed timestamp would
    // put the browser's idea of the time into a hospital record.
    const payload: CreateVisitPayload = {
      visit_type: visitType,
      department_id: Number(departmentId),
    };

    if (mode === "existing" && selectedPatient) {
      payload.patient_id = selectedPatient.id;
    } else {
      const values: NewPatientValues = { name: patient.name.trim() };
      if (patient.gender) values.gender = patient.gender;
      if (patient.date_of_birth) values.date_of_birth = patient.date_of_birth;
      if (patient.phone.trim()) values.phone = patient.phone.trim();
      payload.patient_values = values;
    }

    try {
      const response = await fetch("/api/reception/visits", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const result = (await response.json()) as ApiEnvelope<ReceptionVisitDetail>;

      if (!response.ok || !result.success) {
        // Entered values are left untouched so nothing has to be retyped.
        setFailure(describeFailure(response.status, result));
        return;
      }

      onCreated(result.data.appointment.id);
    } catch {
      setFailure({
        kind: "server",
        message: "Unable to reach the reception service.",
      });
    } finally {
      inFlight.current = false;
      setSubmitting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-slate-950/40 p-4 sm:p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !inFlight.current) {
          onClose();
        }
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="my-auto w-full max-w-2xl rounded-lg border border-slate-200 bg-white shadow-xl outline-none"
      >
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <h2 id={titleId} className="text-sm font-semibold text-slate-950">
              New Visit
            </h2>
            <p className="text-[11px] text-slate-500">
              Search first, register only if this is a first visit.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            aria-label="Close"
            className="flex h-8 w-8 items-center justify-center rounded border border-slate-300 text-slate-600 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:text-slate-300"
          >
            &#10005;
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-4 py-3">
          <div
            role="group"
            aria-label="Patient type"
            className="mb-3 inline-flex rounded border border-slate-300 p-0.5"
          >
            {(["existing", "new"] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={mode === value}
                onClick={() => {
                  setMode(value);
                  setFailure(null);
                }}
                className={`rounded px-3 py-1 text-xs font-semibold outline-none transition focus-visible:ring-2 focus-visible:ring-emerald-600 ${
                  mode === value
                    ? "bg-emerald-700 text-white"
                    : "bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                {value === "existing" ? "Existing patient" : "New patient"}
              </button>
            ))}
          </div>

          {mode === "existing" ? (
            <div className="text-xs">
              <PatientSearch selected={selectedPatient} onSelect={handleSelect} />
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              <label className={`${LABEL} sm:col-span-2`}>
                Full name <span className="text-red-600">*</span>
                <input
                  type="text"
                  value={patient.name}
                  onChange={(event) =>
                    setPatient((prev) => ({ ...prev, name: event.target.value }))
                  }
                  required
                  autoComplete="off"
                  className={FIELD}
                />
              </label>
              <label className={LABEL}>
                Sex
                <select
                  value={patient.gender}
                  onChange={(event) =>
                    setPatient((prev) => ({
                      ...prev,
                      gender: event.target.value,
                    }))
                  }
                  className={FIELD}
                >
                  <option value="">Not recorded</option>
                  <option value="male">Male</option>
                  <option value="female">Female</option>
                </select>
              </label>
              <label className={LABEL}>
                Date of birth
                <input
                  type="date"
                  value={patient.date_of_birth}
                  onChange={(event) =>
                    setPatient((prev) => ({
                      ...prev,
                      date_of_birth: event.target.value,
                    }))
                  }
                  className={FIELD}
                />
              </label>
              <label className={`${LABEL} sm:col-span-2`}>
                Phone
                <input
                  type="tel"
                  value={patient.phone}
                  onChange={(event) =>
                    setPatient((prev) => ({
                      ...prev,
                      phone: event.target.value,
                    }))
                  }
                  autoComplete="off"
                  className={FIELD}
                />
              </label>
              <p className="text-[11px] text-slate-500 sm:col-span-2">
                The MRN is assigned by the hospital system. A first patient card
                is raised automatically when one is owed.
              </p>
            </div>
          )}

          <div className="mt-3 grid gap-2 border-t border-slate-200 pt-3 sm:grid-cols-2">
            <label className={LABEL}>
              Department <span className="text-red-600">*</span>
              <select
                value={departmentId}
                onChange={(event) => setDepartmentId(event.target.value)}
                required
                className={FIELD}
              >
                <option value="">Select department</option>
                {departments.map((department) => (
                  <option key={department.id} value={department.id}>
                    {department.name}
                  </option>
                ))}
              </select>
            </label>
            <label className={LABEL}>
              Visit type
              <select
                value={visitType}
                onChange={(event) => setVisitType(event.target.value)}
                className={FIELD}
              >
                {VISIT_TYPES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            {departmentsError ? (
              <p className="text-[11px] text-red-700 sm:col-span-2">
                {departmentsError}
              </p>
            ) : null}
            <p className="text-[11px] text-slate-500 sm:col-span-2">
              The doctor is assigned during triage. The visit opens at{" "}
              <span className="font-semibold">Intake</span> with today&apos;s
              arrival time.
            </p>
          </div>

          {failure ? (
            <div
              role="alert"
              className={`mt-3 rounded border px-3 py-2 text-xs ${
                failure.kind === "validation"
                  ? "border-amber-200 bg-amber-50 text-amber-900"
                  : "border-red-200 bg-red-50 text-red-800"
              }`}
            >
              <span className="font-semibold">
                {FAILURE_LABEL[failure.kind]}:
              </span>{" "}
              {failure.message}
            </div>
          ) : null}

          <div className="mt-4 flex items-center justify-end gap-2 border-t border-slate-200 pt-3">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="h-9 rounded border border-slate-300 bg-white px-3 text-xs font-semibold text-slate-700 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:text-slate-400"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={!canSubmit}
              className="h-9 rounded bg-emerald-700 px-4 text-xs font-semibold text-white outline-none transition hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:cursor-not-allowed disabled:bg-slate-400"
            >
              {submitting ? "Creating visit…" : "Create visit"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

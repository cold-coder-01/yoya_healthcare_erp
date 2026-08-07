"use client";

/**
 * Visit fields. Department/doctor/triage-destination are pickers backed by the
 * reference endpoints -- a receptionist never types a raw record ID.
 */
import { useEffect, useState } from "react";

import { messageFromPayload } from "@/lib/api-error";
import { VISIT_TYPE_OPTIONS } from "@/lib/reception-format";
import type { ApiEnvelope, Department, Doctor } from "@/types/reception";

export type VisitFormValues = {
  visit_type: string;
  department_id: string;
  doctor_id: string;
  appointment_date: string;
  reason: string;
  triage_destination_id: string;
};

const FIELD =
  "mt-2 h-11 w-full rounded-md border border-slate-300 px-3 text-sm " +
  "disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

export default function VisitDetailsForm({
  values,
  onChange,
}: {
  values: VisitFormValues;
  onChange: (values: VisitFormValues) => void;
}) {
  const [departments, setDepartments] = useState<Department[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [departmentsLoading, setDepartmentsLoading] = useState(true);
  const [doctorsLoading, setDoctorsLoading] = useState(false);
  const [referenceError, setReferenceError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    async function run() {
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
          setReferenceError(
            messageFromPayload(payload, "Unable to load departments."),
          );
          return;
        }
        setDepartments(payload.data.departments ?? []);
      } catch {
        if (!controller.signal.aborted) {
          setReferenceError("Unable to reach the reference service.");
        }
      } finally {
        if (!controller.signal.aborted) setDepartmentsLoading(false);
      }
    }
    void run();
    return () => controller.abort();
  }, []);

  const departmentId = values.department_id;

  // With no department the effect does nothing; the render path derives an
  // empty list (see `visibleDoctors`). Clearing state from the effect body
  // instead would trigger a cascading render.
  useEffect(() => {
    if (!departmentId) {
      return;
    }
    const controller = new AbortController();
    async function run() {
      setDoctorsLoading(true);
      try {
        const response = await fetch(
          `/api/reference/doctors?department_id=${encodeURIComponent(departmentId)}`,
          { cache: "no-store", signal: controller.signal },
        );
        const payload = (await response.json()) as ApiEnvelope<{
          doctors: Doctor[];
        }>;
        if (controller.signal.aborted) return;
        if (!response.ok || !payload.success) {
          setDoctors([]);
          return;
        }
        setDoctors(payload.data.doctors ?? []);
      } catch {
        if (!controller.signal.aborted) setDoctors([]);
      } finally {
        if (!controller.signal.aborted) setDoctorsLoading(false);
      }
    }
    void run();
    return () => controller.abort();
  }, [departmentId]);

  // Derived: a doctor list from a previously selected department must never
  // remain visible after the department is cleared.
  const visibleDoctors = departmentId ? doctors : [];

  function set<K extends keyof VisitFormValues>(
    key: K,
    value: VisitFormValues[K],
  ) {
    onChange({ ...values, [key]: value });
  }

  // Changing department invalidates the doctor: the API rejects a doctor who
  // belongs to a different department, so a stale value would fail on submit.
  function handleDepartmentChange(value: string) {
    onChange({
      ...values,
      department_id: value,
      doctor_id: "",
      triage_destination_id: values.triage_destination_id || value,
    });
  }

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <label className="block text-sm font-medium text-slate-700">
        Visit type <span className="text-red-600">*</span>
        <select
          value={values.visit_type}
          onChange={(event) => set("visit_type", event.target.value)}
          className={`${FIELD} bg-white`}
        >
          {VISIT_TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Department <span className="text-red-600">*</span>
        <select
          value={values.department_id}
          disabled={departmentsLoading}
          onChange={(event) => handleDepartmentChange(event.target.value)}
          className={`${FIELD} bg-white`}
        >
          <option value="">
            {departmentsLoading ? "Loading departments…" : "Select department"}
          </option>
          {departments.map((department) => (
            <option key={department.id} value={String(department.id)}>
              {department.name}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Doctor <span className="text-slate-400">(optional)</span>
        <select
          value={values.doctor_id}
          disabled={!values.department_id || doctorsLoading}
          onChange={(event) => set("doctor_id", event.target.value)}
          className={`${FIELD} bg-white`}
        >
          <option value="">
            {!values.department_id
              ? "Select a department first"
              : doctorsLoading
                ? "Loading doctors…"
                : "Any available doctor"}
          </option>
          {visibleDoctors.map((doctor) => (
            <option key={doctor.id} value={String(doctor.id)}>
              {doctor.name}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Appointment date &amp; time
        <input
          type="datetime-local"
          value={values.appointment_date}
          onChange={(event) => set("appointment_date", event.target.value)}
          className={FIELD}
        />
        <span className="mt-1 block text-xs font-normal text-slate-500">
          Hospital local time (Africa/Addis_Ababa).
        </span>
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Triage destination
        <select
          value={values.triage_destination_id}
          disabled={departmentsLoading}
          onChange={(event) => set("triage_destination_id", event.target.value)}
          className={`${FIELD} bg-white`}
        >
          <option value="">Same as department</option>
          {departments.map((department) => (
            <option key={department.id} value={String(department.id)}>
              {department.name}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm font-medium text-slate-700 md:col-span-2 xl:col-span-3">
        Reason for visit
        <textarea
          value={values.reason}
          onChange={(event) => set("reason", event.target.value)}
          rows={3}
          className="mt-2 w-full rounded-md border border-slate-300 p-3 text-sm"
          placeholder="Presenting complaint as reported at the front desk"
        />
      </label>

      {referenceError ? (
        <p className="text-sm text-red-700 md:col-span-2 xl:col-span-3">
          {referenceError}
        </p>
      ) : null}
    </div>
  );
}

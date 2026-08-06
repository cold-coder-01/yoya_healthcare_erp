"use client";

/**
 * Department and doctor pickers backed by the reference BFF routes.
 *
 * Replaces the raw numeric ID inputs: a receptionist has no way to know that
 * Outpatient is department 3. IDs remain the wire format, but are never shown.
 */
import { useEffect, useState } from "react";

import type { ApiEnvelope, Department, Doctor } from "@/types/reception";

function messageFrom(payload: unknown, fallback: string): string {
  if (
    typeof payload === "object" &&
    payload !== null &&
    "error" in payload &&
    typeof (payload as { error?: unknown }).error === "object" &&
    (payload as { error?: unknown }).error !== null
  ) {
    const error = (payload as { error: { message?: unknown } }).error;
    if (typeof error.message === "string" && error.message.trim()) {
      return error.message;
    }
  }
  return fallback;
}

const SELECT_CLASS =
  "mt-2 h-11 w-full rounded-md border border-slate-300 bg-white px-3 text-sm " +
  "disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

export default function ReferenceFilters({
  departmentId,
  doctorId,
  onDepartmentChange,
  onDoctorChange,
}: {
  departmentId: string;
  doctorId: string;
  /** Implementations MUST also clear the doctor: see the reset note below. */
  onDepartmentChange: (value: string) => void;
  onDoctorChange: (value: string) => void;
}) {
  const [departments, setDepartments] = useState<Department[]>([]);
  const [doctors, setDoctors] = useState<Doctor[]>([]);
  const [departmentsLoading, setDepartmentsLoading] = useState(true);
  const [doctorsLoading, setDoctorsLoading] = useState(true);
  const [departmentsError, setDepartmentsError] = useState<string | null>(null);
  const [doctorsError, setDoctorsError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadDepartments() {
      setDepartmentsLoading(true);
      setDepartmentsError(null);
      try {
        const response = await fetch("/api/reference/departments", {
          cache: "no-store",
        });
        const payload = (await response.json()) as ApiEnvelope<{
          departments: Department[];
        }>;
        if (cancelled) return;
        if (!response.ok || !payload.success) {
          setDepartmentsError(
            messageFrom(payload, "Unable to load departments."),
          );
          setDepartments([]);
          return;
        }
        setDepartments(payload.data.departments ?? []);
      } catch {
        if (!cancelled) {
          setDepartmentsError("Unable to reach the reference service.");
          setDepartments([]);
        }
      } finally {
        if (!cancelled) setDepartmentsLoading(false);
      }
    }

    void loadDepartments();
    return () => {
      cancelled = true;
    };
  }, []);

  // Re-fetches whenever the department changes, so the doctor list always
  // reflects the selected department.
  useEffect(() => {
    let cancelled = false;

    async function loadDoctors() {
      setDoctorsLoading(true);
      setDoctorsError(null);
      const query = departmentId
        ? `?department_id=${encodeURIComponent(departmentId)}`
        : "";
      try {
        const response = await fetch(`/api/reference/doctors${query}`, {
          cache: "no-store",
        });
        const payload = (await response.json()) as ApiEnvelope<{
          doctors: Doctor[];
        }>;
        if (cancelled) return;
        if (!response.ok || !payload.success) {
          setDoctorsError(messageFrom(payload, "Unable to load doctors."));
          setDoctors([]);
          return;
        }
        setDoctors(payload.data.doctors ?? []);
      } catch {
        if (!cancelled) {
          setDoctorsError("Unable to reach the reference service.");
          setDoctors([]);
        }
      } finally {
        if (!cancelled) setDoctorsLoading(false);
      }
    }

    void loadDoctors();
    return () => {
      cancelled = true;
    };
  }, [departmentId]);

  return (
    <>
      <label className="block text-sm font-medium text-slate-700">
        Department
        <select
          value={departmentId}
          disabled={departmentsLoading || Boolean(departmentsError)}
          onChange={(event) => onDepartmentChange(event.target.value)}
          className={SELECT_CLASS}
        >
          <option value="">
            {departmentsLoading ? "Loading departments…" : "All departments"}
          </option>
          {departments.map((department) => (
            <option key={department.id} value={String(department.id)}>
              {department.name}
            </option>
          ))}
        </select>
        {departmentsError ? (
          <span className="mt-1 block text-xs font-normal text-red-700">
            {departmentsError}
          </span>
        ) : null}
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Doctor
        <select
          value={doctorId}
          disabled={doctorsLoading || Boolean(doctorsError)}
          onChange={(event) => onDoctorChange(event.target.value)}
          className={SELECT_CLASS}
        >
          <option value="">
            {doctorsLoading ? "Loading doctors…" : "All doctors"}
          </option>
          {doctors.map((doctor) => (
            <option key={doctor.id} value={String(doctor.id)}>
              {doctor.name}
            </option>
          ))}
        </select>
        {doctorsError ? (
          <span className="mt-1 block text-xs font-normal text-red-700">
            {doctorsError}
          </span>
        ) : null}
        {!doctorsError && !doctorsLoading && doctors.length === 0 ? (
          <span className="mt-1 block text-xs font-normal text-slate-500">
            No doctors in this department.
          </span>
        ) : null}
      </label>
    </>
  );
}

"use client";

/**
 * Doctor picker for clinical screens.
 *
 * Reuses the EXISTING reference endpoint (/api/reference/doctors), which is
 * not reception-scoped: the Odoo route carries no role gate beyond auth="user",
 * and nurses hold read access on hospital.doctor. No new endpoint is needed.
 *
 * Names are shown; the underlying hospital.doctor id is what gets submitted.
 * No doctor id or name is ever hardcoded.
 */
import { useDoctors } from "@/lib/use-doctors";

export default function DoctorSelect({
  label,
  value,
  onChange,
  departmentId,
  /**
   * The doctor already recorded on the record. Kept selectable even when the
   * department filter would exclude them, so opening an evaluation whose
   * physician sits in another department never silently blanks the field and
   * drops the assignment on the next save.
   */
  currentDoctor,
  helpText,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  departmentId?: number | null;
  currentDoctor?: { id: number; name: string } | null;
  helpText?: string;
}) {
  const { options, loading, error } = useDoctors(departmentId, currentDoctor);

  return (
    <label className="text-sm font-medium text-slate-700">
      {label}
      <select
        value={value}
        disabled={loading || Boolean(error)}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm outline-none focus:border-emerald-600 focus:ring-2 focus:ring-emerald-100 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500"
      >
        <option value="">
          {loading ? "Loading doctors…" : "Not assigned"}
        </option>
        {options.map((doctor) => (
          <option key={doctor.id} value={String(doctor.id)}>
            {doctor.name}
          </option>
        ))}
      </select>
      {error ? (
        <span className="mt-1 block text-xs font-normal text-red-700">
          {error}
        </span>
      ) : helpText ? (
        <span className="mt-1 block text-xs font-normal text-slate-500">
          {helpText}
        </span>
      ) : null}
    </label>
  );
}

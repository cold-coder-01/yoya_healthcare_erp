"use client";

/**
 * New-patient fields.
 *
 * Every field here exists in PATIENT_VALUE_FIELDS on the Odoo side and was
 * confirmed against hospital.patient. `identification_code` is deliberately
 * absent: the MRN is sequence-assigned, and the API rejects it outright.
 */
import {
  BLOOD_GROUP_OPTIONS,
  GENDER_OPTIONS,
} from "@/lib/reception-format";
import type { NewPatientValues } from "@/types/reception";

const INPUT =
  "mt-2 h-11 w-full rounded-md border border-slate-300 px-3 text-sm " +
  "focus:border-emerald-600 focus:outline-none focus:ring-2 focus:ring-emerald-100";

export default function NewPatientForm({
  values,
  onChange,
}: {
  values: NewPatientValues;
  onChange: (values: NewPatientValues) => void;
}) {
  function set<K extends keyof NewPatientValues>(
    key: K,
    value: NewPatientValues[K],
  ) {
    onChange({ ...values, [key]: value });
  }

  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <label className="block text-sm font-medium text-slate-700 md:col-span-2 xl:col-span-1">
        Full name <span className="text-red-600">*</span>
        <input
          type="text"
          required
          value={values.name}
          onChange={(event) => set("name", event.target.value)}
          className={INPUT}
          placeholder="Patient full name"
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Date of birth
        <input
          type="date"
          value={values.date_of_birth ?? ""}
          onChange={(event) => set("date_of_birth", event.target.value)}
          className={INPUT}
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Gender
        <select
          value={values.gender ?? ""}
          onChange={(event) => set("gender", event.target.value)}
          className={`${INPUT} bg-white`}
        >
          <option value="">Not recorded</option>
          {GENDER_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Phone
        <input
          type="tel"
          value={values.phone ?? ""}
          onChange={(event) => set("phone", event.target.value)}
          className={INPUT}
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Mobile
        <input
          type="tel"
          value={values.mobile ?? ""}
          onChange={(event) => set("mobile", event.target.value)}
          className={INPUT}
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Blood group
        <select
          value={values.blood_group ?? ""}
          onChange={(event) => set("blood_group", event.target.value)}
          className={`${INPUT} bg-white`}
        >
          <option value="">Not recorded</option>
          {BLOOD_GROUP_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <label className="block text-sm font-medium text-slate-700 md:col-span-2 xl:col-span-3">
        Address
        <input
          type="text"
          value={values.address ?? ""}
          onChange={(event) => set("address", event.target.value)}
          className={INPUT}
          placeholder="Street / kebele / house number"
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        City
        <input
          type="text"
          value={values.city ?? ""}
          onChange={(event) => set("city", event.target.value)}
          className={INPUT}
        />
      </label>

      {/*
        Region / State is intentionally NOT offered.

        hospital.patient.state is a Char address field and the reception
        workflow's own allowlist permits it, but the reception API rejects any
        request containing it (it appears in both the allowlist and the
        forbidden list server-side, and forbidden wins). Rendering an input
        that silently cannot be saved -- or worse, that fails the whole
        atomic registration -- is worse than omitting it. Restore this field
        once the server-side contradiction is resolved.
      */}

      <label className="block text-sm font-medium text-slate-700">
        Country
        <input
          type="text"
          value={values.country ?? ""}
          onChange={(event) => set("country", event.target.value)}
          className={INPUT}
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Emergency contact name
        <input
          type="text"
          value={values.emergency_contact_name ?? ""}
          onChange={(event) => set("emergency_contact_name", event.target.value)}
          className={INPUT}
        />
      </label>

      <label className="block text-sm font-medium text-slate-700">
        Emergency contact phone
        <input
          type="tel"
          value={values.emergency_contact_phone ?? ""}
          onChange={(event) =>
            set("emergency_contact_phone", event.target.value)
          }
          className={INPUT}
        />
      </label>

      <p className="text-xs text-slate-500 md:col-span-2 xl:col-span-3">
        The medical record number (MRN) is assigned automatically by the
        hospital system and cannot be entered here.
      </p>
    </div>
  );
}

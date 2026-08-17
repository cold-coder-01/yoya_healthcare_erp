import {
  bloodPressureText,
  doctorLabel,
  hasAnyVital,
  vitalText,
} from "@/lib/doctor-format";
import type { DoctorVitals } from "@/types/doctor";

/**
 * Triage vitals, READ ONLY.
 *
 * The doctor desk never writes an evaluation: recording and completing triage
 * is the nurse's act, through the endpoints the triage screen already owns.
 * Showing these as inert readings rather than inputs is the whole point --
 * a doctor reads what triage found, and cannot silently amend it.
 *
 * The previous column is the patient's last COMPLETED evaluation, which is
 * what makes a single reading interpretable: 38.4 °C means something different
 * against 38.9 yesterday than against 36.6.
 */

const CELL = "flex flex-col gap-0.5 rounded border border-slate-200 bg-white px-2 py-1.5";
const LABEL = "text-[9px] font-bold uppercase tracking-wide text-slate-500";
const VALUE = "text-[13px] font-bold tabular-nums leading-tight text-slate-900";
const PREV = "text-[10px] tabular-nums leading-tight text-slate-400";

function Vital({
  label,
  value,
  previous,
}: {
  label: string;
  value: string;
  previous?: string;
}) {
  return (
    <div className={CELL}>
      <span className={LABEL}>{label}</span>
      <span className={VALUE}>{value}</span>
      {previous && previous !== "—" ? (
        <span className={PREV} title="Previous completed evaluation">
          prev {previous}
        </span>
      ) : null}
    </div>
  );
}

export default function DoctorVitalsGrid({
  vitals,
  previous,
}: {
  vitals: DoctorVitals;
  previous: DoctorVitals | null;
}) {
  if (!hasAnyVital(vitals)) {
    return (
      <p className="rounded border border-dashed border-slate-300 bg-slate-50 px-3 py-3 text-center text-[11px] text-slate-500">
        No vitals recorded for this visit yet.
      </p>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 xl:grid-cols-4">
      <Vital
        label="Temp"
        value={vitalText(vitals.temperature, "°C")}
        previous={previous ? vitalText(previous.temperature, "°C") : undefined}
      />
      <Vital
        label="BP"
        value={bloodPressureText(vitals)}
        previous={previous ? bloodPressureText(previous) : undefined}
      />
      <Vital
        label="Pulse"
        value={vitalText(vitals.heart_rate, "bpm", 0)}
        previous={previous ? vitalText(previous.heart_rate, "bpm", 0) : undefined}
      />
      <Vital
        label="Resp"
        value={vitalText(vitals.respiratory_rate, "/min", 0)}
        previous={previous ? vitalText(previous.respiratory_rate, "/min", 0) : undefined}
      />
      <Vital
        label="SpO₂"
        value={vitalText(vitals.spo2, "%", 0)}
        previous={previous ? vitalText(previous.spo2, "%", 0) : undefined}
      />
      <Vital
        label="RBS"
        value={vitalText(vitals.rbs, "mg/dL", 0)}
        previous={previous ? vitalText(previous.rbs, "mg/dL", 0) : undefined}
      />
      <Vital
        label="Weight"
        value={vitalText(vitals.weight, "kg")}
        previous={previous ? vitalText(previous.weight, "kg") : undefined}
      />
      <Vital
        label="Height"
        value={vitalText(vitals.height, "cm", 0)}
        previous={previous ? vitalText(previous.height, "cm", 0) : undefined}
      />
      <Vital
        label={`BMI${vitals.bmi_state ? ` · ${doctorLabel(vitals.bmi_state)}` : ""}`}
        value={vitalText(vitals.bmi)}
        previous={previous ? vitalText(previous.bmi) : undefined}
      />
      {vitals.head_circumference !== null ? (
        <Vital
          label="Head circ."
          value={vitalText(vitals.head_circumference, "cm")}
          previous={previous ? vitalText(previous.head_circumference, "cm") : undefined}
        />
      ) : null}
      {vitals.pain_level !== null ? (
        <Vital label="Pain score" value={`${vitals.pain_level} / 10`} />
      ) : null}
    </div>
  );
}

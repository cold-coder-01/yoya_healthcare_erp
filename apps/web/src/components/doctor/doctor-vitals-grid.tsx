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
 *
 * TWO TIERS, BECAUSE THEY ARE NOT EQUALLY URGENT.
 * Every vital used to render in an identical cell, which left the observation
 * set -- BP, pulse, temperature, respiratory rate, SpO2 -- competing for
 * attention with height and BMI. The five that drive an immediate clinical
 * judgement now sit in larger cells on their own row; anthropometry and the
 * situational readings follow in a denser strip. Nothing was removed, and the
 * secondary tier is still fully legible: it is de-emphasised, not hidden.
 */

const PRIMARY_CELL =
  "flex flex-col gap-0.5 rounded-md border border-slate-200 bg-white px-2.5 py-2";
const SECONDARY_CELL =
  "flex flex-col gap-0.5 rounded-md border border-slate-200 bg-slate-50/70 px-2 py-1.5";

const PRIMARY_LABEL =
  "text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500";
const SECONDARY_LABEL =
  "text-[9px] font-semibold uppercase tracking-[0.06em] text-slate-400";

const PRIMARY_VALUE =
  "text-[17px] font-bold leading-none tabular-nums text-slate-900";
const SECONDARY_VALUE =
  "text-[12px] font-bold leading-none tabular-nums text-slate-700";

const PREV = "text-[9px] tabular-nums leading-tight text-slate-400";

/** A unit rendered at a quieter weight than its number, so the value leads. */
function Reading({ text, className }: { text: string; className: string }) {
  const [value, ...unit] = text.split(" ");
  return (
    <span className={className}>
      {value}
      {unit.length ? (
        <span className="ml-0.5 text-[10px] font-semibold text-slate-400">
          {unit.join(" ")}
        </span>
      ) : null}
    </span>
  );
}

function Vital({
  label,
  value,
  previous,
  primary = false,
}: {
  label: string;
  value: string;
  previous?: string;
  primary?: boolean;
}) {
  return (
    <div className={primary ? PRIMARY_CELL : SECONDARY_CELL}>
      <span className={primary ? PRIMARY_LABEL : SECONDARY_LABEL}>{label}</span>
      <Reading text={value} className={primary ? PRIMARY_VALUE : SECONDARY_VALUE} />
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
      <p className="rounded-md border border-dashed border-slate-300 bg-slate-50 px-3 py-4 text-center text-[11px] text-slate-500">
        No vitals recorded for this visit yet.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      {/* Tier 1: the observation set a clinical judgement turns on. */}
      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 xl:grid-cols-5">
        <Vital
          primary
          label="BP"
          value={bloodPressureText(vitals)}
          previous={previous ? bloodPressureText(previous) : undefined}
        />
        <Vital
          primary
          label="Pulse"
          value={vitalText(vitals.heart_rate, "bpm", 0)}
          previous={previous ? vitalText(previous.heart_rate, "bpm", 0) : undefined}
        />
        <Vital
          primary
          label="Temp"
          value={vitalText(vitals.temperature, "°C")}
          previous={previous ? vitalText(previous.temperature, "°C") : undefined}
        />
        <Vital
          primary
          label="Resp"
          value={vitalText(vitals.respiratory_rate, "/min", 0)}
          previous={previous ? vitalText(previous.respiratory_rate, "/min", 0) : undefined}
        />
        <Vital
          primary
          label="SpO₂"
          value={vitalText(vitals.spo2, "%", 0)}
          previous={previous ? vitalText(previous.spo2, "%", 0) : undefined}
        />
      </div>

      {/* Tier 2: anthropometry and situational readings. */}
      <div className="grid grid-cols-3 gap-1.5 sm:grid-cols-4 xl:grid-cols-6">
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
        <Vital
          label="RBS"
          value={vitalText(vitals.rbs, "mg/dL", 0)}
          previous={previous ? vitalText(previous.rbs, "mg/dL", 0) : undefined}
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
    </div>
  );
}

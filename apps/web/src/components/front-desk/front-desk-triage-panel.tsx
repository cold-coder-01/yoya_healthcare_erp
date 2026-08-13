"use client";

/**
 * The Front Desk Nurse triage workstation, inside the selected-patient panel.
 *
 * The nurse never leaves /front-desk: the queue stays mounted on the left and
 * every action here mutates through an authoritative Odoo endpoint and then
 * refetches. NOTHING in this file decides a stage or a permission -- it renders
 * what `permitted_actions` and the serialized evaluation say, and re-reads them
 * after every write. A stage is never predicted client-side.
 *
 * Write paths, all server-authoritative:
 *   Start Triage    POST /api/front-desk/visits/<id>/start-triage
 *   Doctor          POST /api/front-desk/visits/<id>/doctor
 *   Save Draft      POST /api/clinical/evaluations/<APPOINTMENT id>/save
 *   Complete        POST /api/clinical/evaluations/<EVALUATION id>/complete
 *
 * Note the two different ids on the last pair: save upserts by appointment,
 * completion acts on the evaluation. That asymmetry is the existing Odoo
 * contract, not a mistake here.
 */
import { useCallback, useMemo, useRef, useState } from "react";

import { codeFromPayload, messageFromPayload } from "@/lib/api-error";
import { formatHospitalDateTime } from "@/lib/clinical-format";
import {
  blankIfUnrecorded,
  buildPayload,
  formFromEvaluation,
  VITAL_RANGES,
  type FormState,
  type NumericField,
} from "@/lib/evaluation-form";
import { frontDeskLabel } from "@/lib/front-desk-format";
import { useDoctors } from "@/lib/use-doctors";
import type { ApiEnvelope, FrontDeskVisit } from "@/types/front-desk";

type VitalSpec = {
  field: NumericField;
  label: string;
  unit: string;
  step?: string;
};

/**
 * PRIMARY vitals: the set a nurse or doctor scans first, and the set that must
 * be visible without scrolling at 1366x768. Blood pressure is not here because
 * it is rendered as one paired observation (see BP_FIELDS).
 */
const PRIMARY_VITALS: VitalSpec[] = [
  { field: "temperature", label: "Temperature", unit: "°C", step: "0.1" },
  { field: "heart_rate", label: "Pulse", unit: "bpm" },
  { field: "respiratory_rate", label: "Resp. rate", unit: "/min" },
  { field: "spo2", label: "SpO₂", unit: "%" },
];

/** Rendered inside ONE shell as `[sys] / [dia] mmHg`; still two backend fields. */
const BP_FIELDS: VitalSpec[] = [
  { field: "systolic_bp", label: "Systolic blood pressure", unit: "mmHg" },
  { field: "diastolic_bp", label: "Diastolic blood pressure", unit: "mmHg" },
];

/** SECONDARY: recorded when relevant, scanned less often. Nothing is hidden. */
const SECONDARY_VITALS: VitalSpec[] = [
  { field: "weight", label: "Weight", unit: "kg", step: "0.1" },
  { field: "height", label: "Height", unit: "cm", step: "0.1" },
  { field: "rbs", label: "RBS", unit: "mg/dL" },
  { field: "head_circumference", label: "Head circ.", unit: "cm", step: "0.1" },
];

/** Flat list for range checking; order drives which problem is reported first. */
const ALL_VITALS: VitalSpec[] = [
  ...PRIMARY_VITALS,
  ...BP_FIELDS,
  ...SECONDARY_VITALS,
];

const PRIORITIES = [
  { value: "routine", label: "Routine" },
  { value: "urgent", label: "Urgent" },
  { value: "emergency", label: "Emergency" },
];

const PAIN_LEVELS = Array.from({ length: 11 }, (_, index) => String(index));

/** Odoo returns 0.0 for an unrecorded Float; an empty box must stay empty. */
function formSourceFromVisit(visit: FrontDeskVisit) {
  const evaluation = visit.evaluation;
  if (!evaluation) return null;
  const vitals = evaluation.vitals;
  return {
    weight: blankIfUnrecorded(vitals.weight),
    height: blankIfUnrecorded(vitals.height),
    temperature: blankIfUnrecorded(vitals.temperature),
    heart_rate: blankIfUnrecorded(vitals.heart_rate),
    respiratory_rate: blankIfUnrecorded(vitals.respiratory_rate),
    systolic_bp: blankIfUnrecorded(vitals.systolic_bp),
    diastolic_bp: blankIfUnrecorded(vitals.diastolic_bp),
    spo2: blankIfUnrecorded(vitals.spo2),
    rbs: blankIfUnrecorded(vitals.rbs),
    head_circumference: blankIfUnrecorded(vitals.head_circumference),
    pain_level: vitals.pain_level,
    pain_note: vitals.pain_note,
    triage_priority: evaluation.triage_priority,
    chief_complaint: evaluation.chief_complaint,
    triage_notes: evaluation.triage_notes,
  };
}

/**
 * A measurement shell: ONE border around the value and its unit.
 *
 * Previously every vital was a generic 11px text box with its unit stranded in
 * the label, so ten identical grey rectangles carried no hierarchy and the
 * numbers -- the only thing that matters clinically -- were the smallest text
 * on screen. The shell carries the border and the focus ring, the input inside
 * is borderless and 14px semibold, and the unit sits directly against the
 * number. Fewer borders, louder values, same height.
 */
function shellClass(invalid: boolean, disabled: boolean) {
  return [
    "flex items-center rounded border transition-colors",
    "focus-within:ring-1",
    invalid
      ? "border-red-500 bg-red-50 focus-within:border-red-600 focus-within:ring-red-200"
      : "border-slate-300 focus-within:border-emerald-600 focus-within:ring-emerald-200",
    disabled ? "bg-slate-50" : invalid ? "" : "bg-white",
  ].join(" ");
}

// Spinners eat ~16px per field and are useless for clinical entry.
const VALUE_INPUT =
  "h-9 w-full min-w-0 border-0 bg-transparent px-2 text-[13px] font-semibold tabular-nums " +
  "text-slate-900 outline-none [appearance:textfield] " +
  "[&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none " +
  "disabled:cursor-not-allowed disabled:text-slate-700";

const UNIT = "shrink-0 pr-2 text-[10px] font-medium text-slate-500";

const PRIMARY_LABEL =
  "mb-0.5 block truncate text-[10px] font-bold uppercase tracking-wide text-slate-600";
const SECONDARY_LABEL =
  "mb-0.5 block truncate text-[10px] font-medium uppercase tracking-wide text-slate-500";

const TEXT_AREA =
  "w-full min-w-0 resize-y rounded border border-slate-300 bg-white px-2 py-1.5 text-[13px] " +
  "text-slate-900 outline-none focus:border-emerald-600 focus:ring-1 focus:ring-emerald-200 " +
  "disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-700";

function VitalField({
  spec,
  value,
  invalid,
  disabled,
  secondary,
  onChange,
}: {
  spec: VitalSpec;
  value: string;
  invalid: boolean;
  disabled: boolean;
  /** Quieter label. The control is identical -- grouping is typographic only. */
  secondary?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="min-w-0">
      <span className={secondary ? SECONDARY_LABEL : PRIMARY_LABEL}>{spec.label}</span>
      <span className={shellClass(invalid, disabled)}>
        <input
          type="number"
          inputMode="decimal"
          step={spec.step ?? "1"}
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          className={VALUE_INPUT}
        />
        <span className={UNIT}>{spec.unit}</span>
      </span>
    </label>
  );
}

function Btn({
  children,
  onClick,
  disabled,
  title,
  tone = "default",
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  tone?: "default" | "primary";
}) {
  const tones = {
    default: "border-slate-300 bg-white text-slate-800 hover:bg-slate-50",
    primary: "border-emerald-700 bg-emerald-700 text-white hover:bg-emerald-800",
  };
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`inline-flex h-9 items-center rounded border px-3 text-xs font-bold outline-none focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:cursor-not-allowed disabled:opacity-50 ${tones[tone]}`}
    >
      {children}
    </button>
  );
}

export default function FrontDeskTriagePanel({
  visit,
  capabilities,
  onMutated,
  children,
}: {
  visit: FrontDeskVisit;
  capabilities: Record<string, boolean>;
  onMutated: () => void;
  /** The parent's non-triage sections; they scroll with the form. */
  children?: React.ReactNode;
}) {
  const evaluation = visit.evaluation;
  const actions = visit.permitted_actions;
  const stage = visit.row.queue_stage;
  const appointmentId = visit.visit.appointment_id;

  const [form, setForm] = useState<FormState>(() =>
    formFromEvaluation(formSourceFromVisit(visit)),
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Synchronous, unlike state: two clicks in the same tick both read `false`
  // from a state variable but only the first gets past this ref.
  const inFlight = useRef(false);

  /**
   * Re-sync the form from the server whenever the serialized evaluation
   * changes -- a successful save, a completion, or a manual Refresh. The
   * signature covers the whole evaluation, so a change made by the OTHER front
   * desk nurse is picked up too, not just our own writes.
   *
   * Adjusted DURING RENDER rather than in an effect. This is React's documented
   * "adjusting state when a prop changes" pattern: React re-runs this component
   * immediately without committing the intermediate paint, so there is no
   * cascading render and no flash of stale values. An effect here would also
   * trip react-hooks/set-state-in-effect.
   *
   * There is no polling anywhere in this workstation, so this cannot interrupt
   * typing: the detail only refetches on selection change, on Refresh, or after
   * a mutation this panel performed.
   */
  const signature = useMemo(
    () => `${appointmentId}:${JSON.stringify(evaluation)}`,
    [appointmentId, evaluation],
  );
  const [syncedSignature, setSyncedSignature] = useState(signature);
  if (signature !== syncedSignature) {
    setSyncedSignature(signature);
    setForm(formFromEvaluation(formSourceFromVisit(visit)));
  }

  // Backend-derived, never a local stage guess.
  const started = Boolean(evaluation?.started_at) && evaluation?.state === "draft";
  const editable = Boolean(actions.record_triage) && started;
  const canStart = Boolean(actions.record_triage) && !started && evaluation?.state !== "done";
  const canAssignDoctor =
    Boolean(capabilities.intake) && Boolean(actions.record_triage);
  const locked = !editable;

  const { options: doctors, loading: doctorsLoading } = useDoctors(
    visit.visit.department?.id ?? null,
    visit.visit.doctor ?? null,
  );

  const set = useCallback((field: keyof FormState, value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
  }, []);

  const outOfRange = useCallback((field: NumericField, raw: string) => {
    const range = VITAL_RANGES[field];
    if (!range || !raw.trim()) return false;
    const value = Number(raw);
    return Number.isFinite(value) && (value < range.min || value > range.max);
  }, []);

  const rangeProblem = ALL_VITALS.find((spec) =>
    outOfRange(spec.field, form[spec.field]),
  );

  const missingComplaint = !form.chief_complaint.trim();
  const missingPriority = !form.triage_priority;

  /**
   * One POST. Returns ok plus the parsed envelope; it decides nothing.
   *
   * A 409 (or a locked/not-startable code) means our view of the record is
   * stale -- another nurse completed it, or it was already done. The right
   * answer is never to retry: refetch the canonical state and say what
   * happened.
   */
  const post = useCallback(
    async (url: string, body: unknown) => {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body ?? {}),
      });
      const payload = (await response.json()) as ApiEnvelope<unknown>;
      if (response.ok && payload.success) return { ok: true as const };

      const code = codeFromPayload(payload);
      const stale =
        response.status === 409 ||
        code === "evaluation_locked" ||
        code === "triage_not_startable";
      setError(
        stale
          ? `${messageFromPayload(payload, "This visit changed elsewhere.")} Reloading the current state.`
          : messageFromPayload(payload, "The action could not be completed."),
      );
      if (stale) onMutated();
      return { ok: false as const };
    },
    [onMutated],
  );

  /**
   * Synchronous in-flight gate around every mutation.
   *
   * A `busy` state variable is not enough on its own: two clicks dispatched in
   * the same tick both read the pre-update value. The ref is written before any
   * await, so the second click returns immediately.
   */
  const guard = useCallback(
    async (key: string, action: () => Promise<void>) => {
      if (inFlight.current) return;
      inFlight.current = true;
      setBusy(key);
      setError(null);
      setNotice(null);
      try {
        await action();
      } catch {
        setError("Unable to reach the front desk service.");
      } finally {
        inFlight.current = false;
        setBusy(null);
      }
    },
    [],
  );

  const savePayload = () =>
    // physician_id is omitted: the doctor is owned by the assignment endpoint,
    // which also keeps the appointment and encounter in step.
    buildPayload(form, { includePhysician: false });

  const startTriage = () =>
    void guard("start", async () => {
      const result = await post(
        `/api/front-desk/visits/${appointmentId}/start-triage`,
        {},
      );
      if (result.ok) {
        setNotice("Triage started.");
        onMutated();
      }
    });

  const assignDoctor = (doctorId: string) => {
    if (!doctorId) return;
    void guard("doctor", async () => {
      const result = await post(
        `/api/front-desk/visits/${appointmentId}/doctor`,
        { doctor_id: Number(doctorId) },
      );
      if (result.ok) {
        setNotice("Doctor assigned.");
        onMutated();
      }
    });
  };

  const saveDraft = () =>
    void guard("save", async () => {
      const result = await post(
        `/api/clinical/evaluations/${appointmentId}/save`,
        savePayload(),
      );
      if (result.ok) {
        setNotice("Draft saved.");
        onMutated();
      }
    });

  /**
   * Save, THEN complete -- one click, two authoritative calls.
   *
   * Completion validates against what is STORED, not what is on screen. Without
   * the save first, a nurse who typed a chief complaint and pressed Complete
   * would be told "a chief complaint is required" while reading the complaint
   * they just typed. The save is the same idempotent upsert Save Draft uses, so
   * this costs nothing and the two never disagree.
   */
  const completeTriage = () => {
    if (!evaluation) return;
    void guard("complete", async () => {
      const saved = await post(
        `/api/clinical/evaluations/${appointmentId}/save`,
        savePayload(),
      );
      if (!saved.ok) {
        onMutated();
        return;
      }
      // post() already surfaces any failure as an error banner, so the result
      // is not inspected here. No success notice either: the handoff strip and
      // the footer status line both state the outcome, and a third copy of
      // "triage completed" was noise.
      await post(`/api/clinical/evaluations/${evaluation.id}/complete`, {});
      // Refetch either way: the save landed, so the panel must reflect it even
      // when the completion was rejected.
      onMutated();
    });
  };

  const busyAny = busy !== null;


  const lastUpdatedAt =
    evaluation?.completed_at ?? evaluation?.started_at ?? evaluation?.evaluation_date;

  /**
   * Completed AND past the editable window: the footer becomes a read-only
   * status line instead of two dead buttons.
   */
  const readOnlyDone = !canStart && !editable && evaluation?.state === "done";

  return (
    /*
      The triage panel owns the whole panel BODY: a scrolling form, the history
      tab strip and a pinned action footer. The parent passes its non-triage
      sections (patient/visit, financial) as children so they scroll with the
      form while the footer stays put.
    */
    <div className="flex min-h-0 flex-1 flex-col">
      {/*
        The stage strip that used to sit here is GONE. It restated the stage a
        third time -- after the header badge and the handoff strip -- and the
        nurse attribution it carried is already in the footer's "last updated"
        line. Removing it reclaims ~34px for the form.
      */}
      {error ? (
        <p
          role="alert"
          className="shrink-0 border-b border-red-200 bg-red-50 px-3 py-1.5 text-[11px] font-medium text-red-800"
        >
          {error}
        </p>
      ) : null}
      {notice && !error ? (
        <p className="shrink-0 border-b border-emerald-200 bg-emerald-50 px-3 py-1.5 text-[11px] font-medium text-emerald-800">
          {notice}
        </p>
      ) : null}
      {editable && (missingComplaint || missingPriority) ? (
        <p className="shrink-0 border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-900">
          A chief complaint and a priority are required before triage can be
          completed.
        </p>
      ) : null}

      {/* ---------------- Scrolling form ---------------- */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* Doctor + department */}
        <div className="grid gap-2 border-b border-slate-200 px-3 py-1.5 sm:grid-cols-2">
          <label className="min-w-0">
            <span className={PRIMARY_LABEL}>Doctor</span>
            <select
              value={String(visit.visit.doctor?.id ?? "")}
              disabled={!canAssignDoctor || busyAny || doctorsLoading}
              onChange={(event) => assignDoctor(event.target.value)}
              className={`h-9 w-full min-w-0 rounded border bg-white px-2 text-[13px] font-semibold text-slate-900 outline-none focus:border-emerald-600 focus:ring-1 focus:ring-emerald-200 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-700 ${
                visit.visit.doctor ? "border-slate-300" : "border-amber-400"
              }`}
            >
              <option value="">
                {doctorsLoading ? "Loading doctors…" : "Not assigned"}
              </option>
              {doctors.map((doctor) => (
                <option key={doctor.id} value={String(doctor.id)}>
                  {doctor.name}
                </option>
              ))}
            </select>
          </label>
          <div className="min-w-0">
            <span className={PRIMARY_LABEL}>Department (set at registration)</span>
            <div className="flex h-9 items-center rounded border border-slate-200 bg-slate-50 px-2 text-[13px] font-semibold text-slate-700">
              {visit.visit.department?.name ?? "-"}
            </div>
          </div>
        </div>

        {/* ---------------- Vitals ---------------- */}
        <div className="border-b border-slate-200 px-3 py-1.5">
          <h3 className="mb-1 text-[10px] font-bold uppercase tracking-wide text-slate-600">
            Vitals
          </h3>

          {/*
            Six-column grid. Blood pressure and BMI span two columns because
            each carries two values; everything else takes one. That produces
            the reference layout -- primary vitals on the first row, secondary
            on the second -- and still reflows to 3 and 2 columns as the panel
            narrows, so nothing overflows at any split width.
          */}
          <div className="grid grid-cols-2 gap-x-2 gap-y-2 sm:grid-cols-3 min-[1100px]:grid-cols-6">
            {PRIMARY_VITALS.slice(0, 1).map((spec) => (
              <VitalField
                key={spec.field}
                spec={spec}
                value={form[spec.field]}
                invalid={outOfRange(spec.field, form[spec.field])}
                disabled={locked || busyAny}
                onChange={(value) => set(spec.field, value)}
              />
            ))}

            {/*
              BLOOD PRESSURE: one observation, one shell, two backend fields.
              Systolic and diastolic used to read as two unrelated measurements
              ("SYS BP" / "DIA BP"). They now read as `120 / 80 mmHg`, the way
              the reading is spoken, written and scanned. The payload is
              unchanged -- systolic_bp and diastolic_bp are still submitted
              separately. A <div role="group"> rather than a <label>, because
              one label cannot describe two inputs; each carries its own
              aria-label.
            */}
            <div className="col-span-2 min-w-0" role="group" aria-label="Blood pressure">
              <span className={PRIMARY_LABEL}>Blood pressure</span>
              <span
                className={shellClass(
                  BP_FIELDS.some((spec) => outOfRange(spec.field, form[spec.field])),
                  locked || busyAny,
                )}
              >
                <input
                  type="number"
                  inputMode="decimal"
                  aria-label="Systolic blood pressure"
                  value={form.systolic_bp}
                  disabled={locked || busyAny}
                  onChange={(event) => set("systolic_bp", event.target.value)}
                  className={`${VALUE_INPUT} text-right`}
                />
                <span aria-hidden className="shrink-0 text-[13px] font-semibold text-slate-400">
                  /
                </span>
                <input
                  type="number"
                  inputMode="decimal"
                  aria-label="Diastolic blood pressure"
                  value={form.diastolic_bp}
                  disabled={locked || busyAny}
                  onChange={(event) => set("diastolic_bp", event.target.value)}
                  className={VALUE_INPUT}
                />
                <span className={UNIT}>mmHg</span>
              </span>
            </div>

            {PRIMARY_VITALS.slice(1).map((spec) => (
              <VitalField
                key={spec.field}
                spec={spec}
                value={form[spec.field]}
                invalid={outOfRange(spec.field, form[spec.field])}
                disabled={locked || busyAny}
                onChange={(value) => set(spec.field, value)}
              />
            ))}

            {/* Secondary row: same controls, quieter labels, nothing hidden. */}
            {SECONDARY_VITALS.slice(0, 2).map((spec) => (
              <VitalField
                key={spec.field}
                spec={spec}
                value={form[spec.field]}
                invalid={outOfRange(spec.field, form[spec.field])}
                disabled={locked || busyAny}
                secondary
                onChange={(value) => set(spec.field, value)}
              />
            ))}

            {/* Computed by Odoo from weight/height. Read-only, never submitted. */}
            <div className="col-span-2 min-w-0">
              <span className={SECONDARY_LABEL}>BMI</span>
              <span className="flex h-9 items-baseline gap-1.5 rounded border border-dashed border-slate-200 bg-slate-50 px-2">
                <span className="text-[13px] font-bold tabular-nums text-slate-800">
                  {evaluation?.vitals.bmi ? evaluation.vitals.bmi.toFixed(1) : "-"}
                </span>
                <span className="truncate text-[10px] text-slate-500">
                  {frontDeskLabel(evaluation?.vitals.bmi_state, "")}
                </span>
              </span>
            </div>

            {SECONDARY_VITALS.slice(2).map((spec) => (
              <VitalField
                key={spec.field}
                spec={spec}
                value={form[spec.field]}
                invalid={outOfRange(spec.field, form[spec.field])}
                disabled={locked || busyAny}
                secondary
                onChange={(value) => set(spec.field, value)}
              />
            ))}

            <label className="min-w-0">
              <span className={SECONDARY_LABEL}>Pain (0-10)</span>
              <span className={shellClass(false, locked || busyAny)}>
                <select
                  value={form.pain_level}
                  disabled={locked || busyAny}
                  onChange={(event) => set("pain_level", event.target.value)}
                  className={VALUE_INPUT}
                >
                  <option value="">-</option>
                  {PAIN_LEVELS.map((level) => (
                    <option key={level} value={level}>
                      {level}
                    </option>
                  ))}
                </select>
              </span>
            </label>
          </div>

          {rangeProblem ? (
            <p className="mt-1.5 text-[10px] font-medium text-red-700">
              {rangeProblem.label} is outside the accepted range (
              {VITAL_RANGES[rangeProblem.field]?.min}–
              {VITAL_RANGES[rangeProblem.field]?.max} {rangeProblem.unit}).
            </p>
          ) : null}
        </div>

        {/* ---------------- Complaint + priority ---------------- */}
        <div className="grid gap-2 border-b border-slate-200 px-3 py-1.5 sm:grid-cols-2">
          <label className="min-w-0">
            <span className={PRIMARY_LABEL}>
              Chief complaint <span className="text-red-600">*</span>
            </span>
            <textarea
              rows={2}
              value={form.chief_complaint}
              disabled={locked || busyAny}
              placeholder="What the patient is presenting with"
              onChange={(event) => set("chief_complaint", event.target.value)}
              className={TEXT_AREA}
            />
          </label>
          <label className="min-w-0">
            <span className={PRIMARY_LABEL}>
              Priority <span className="text-red-600">*</span>
            </span>
            <select
              value={form.triage_priority}
              disabled={locked || busyAny}
              onChange={(event) => set("triage_priority", event.target.value)}
              className="h-9 w-full min-w-0 rounded border border-slate-300 bg-white px-2 text-[13px] font-semibold text-slate-900 outline-none focus:border-emerald-600 focus:ring-1 focus:ring-emerald-200 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-700"
            >
              <option value="">-</option>
              {PRIORITIES.map((priority) => (
                <option key={priority.value} value={priority.value}>
                  {priority.label}
                </option>
              ))}
            </select>
            <span className="mt-1 block text-[9px] leading-tight text-slate-500">
              Clinical urgency only. Does not change payment or the cashier step.
            </span>
          </label>
        </div>

        {/* ---------------- Notes ---------------- */}
        <div className="grid gap-2 border-b border-slate-200 px-3 py-1.5 sm:grid-cols-2">
          <label className="min-w-0">
            <span className={SECONDARY_LABEL}>Triage notes</span>
            <textarea
              rows={2}
              value={form.triage_notes}
              disabled={locked || busyAny}
              onChange={(event) => set("triage_notes", event.target.value)}
              className={TEXT_AREA}
            />
          </label>
          <label className="min-w-0">
            <span className={SECONDARY_LABEL}>Pain note</span>
            <textarea
              rows={2}
              value={form.pain_note}
              disabled={locked || busyAny}
              onChange={(event) => set("pain_note", event.target.value)}
              className={TEXT_AREA}
            />
          </label>
        </div>

        {children}
      </div>

      {/* ---------------- History tabs (later phase) ---------------- */}
      <div className="grid h-7 shrink-0 grid-cols-3 border-t border-slate-200 bg-slate-50 sm:grid-cols-6">
        {["Previous Vitals", "Visits", "Diagnoses", "Medications", "Lab", "Radiology"].map(
          (label) => (
            <button
              key={label}
              type="button"
              disabled
              className="min-w-0 truncate border-l border-slate-200 px-1 text-[10px] font-semibold text-slate-400 first:border-l-0"
            >
              {label}
            </button>
          ),
        )}
      </div>

      {/*
        ---------------- Action footer ----------------
        Pinned OUTSIDE the scroll area, so the commit actions are reachable
        however far the nurse has scrolled and can never overlap or cover a
        field. Normal DOM and focus order.

        Once triage is complete the footer becomes a read-only STATUS line
        rather than two dead buttons: a disabled "Complete Triage" reads as
        something that broke, whereas a stated outcome reads as a finished job.
        No mutation action is exposed after completion, and the gating itself is
        unchanged -- readOnlyDone is derived from the same backend-driven
        `editable` / `canStart` flags.
      */}
      <div className="flex min-h-11 shrink-0 flex-wrap items-center justify-between gap-2 border-t border-slate-200 bg-white px-3 py-1.5">
        <div className="min-w-0">
          <div className="text-[9px] font-bold uppercase tracking-wide text-slate-500">
            Last updated
          </div>
          <div className="truncate text-[11px] text-slate-600">
            {lastUpdatedAt
              ? `${formatHospitalDateTime(lastUpdatedAt)}${
                  evaluation?.assigned_nurse?.name
                    ? ` by ${evaluation.assigned_nurse.name}`
                    : ""
                }`
              : "Triage not started"}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {readOnlyDone ? (
            <span className="inline-flex items-center gap-1.5 rounded border border-emerald-200 bg-emerald-50 px-2.5 py-1 text-[11px] font-bold text-emerald-900">
              <svg aria-hidden viewBox="0 0 20 20" fill="currentColor" className="h-3.5 w-3.5">
                <path d="M8.1 13.6 4.9 10.4l1.2-1.2 2 2 5.8-5.8 1.2 1.2z" />
              </svg>
              Triage completed
              <span className="font-medium text-emerald-700">
                &middot; {frontDeskLabel(stage)}
              </span>
            </span>
          ) : canStart ? (
            <Btn onClick={startTriage} disabled={busyAny} tone="primary">
              {busy === "start" ? "Starting…" : "Start Triage"}
            </Btn>
          ) : (
            <>
              <Btn
                onClick={saveDraft}
                disabled={!editable || busyAny}
                title={editable ? undefined : "Triage is read-only at this stage."}
              >
                {busy === "save" ? "Saving…" : "Save Draft"}
              </Btn>
              <Btn
                onClick={completeTriage}
                disabled={
                  !actions.complete_triage ||
                  !editable ||
                  busyAny ||
                  missingComplaint ||
                  missingPriority
                }
                tone="primary"
                title={
                  actions.complete_triage
                    ? undefined
                    : "Triage is already complete for this visit."
                }
              >
                {busy === "complete" ? "Completing…" : "Complete Triage"}
              </Btn>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

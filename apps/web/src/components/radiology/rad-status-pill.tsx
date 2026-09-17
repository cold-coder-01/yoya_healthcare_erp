import { radLaneCode, radPriorityCode } from "@/lib/rad-desk-format";

/**
 * Lane, priority and report-state chips for the Radiology Desk.
 *
 * NEVER COLOUR ALONE. Every chip carries a short text code as well as a tone,
 * the same two-encoding rule the Laboratory and Doctor desks apply. The tone is
 * keyed on the SERVER's lane key and the label rendered is the server's own
 * wording: the client styles, it does not name.
 *
 * Teal is this workstation's accent, so Radiology reads as its own desk beside
 * the Laboratory's indigo and the Doctor Desk's emerald.
 */
const LANE_TONE: Record<string, string> = {
  // Amber: "money is holding this", the register every desk uses for it.
  awaiting_clearance: "border-amber-400 bg-amber-100 text-amber-900",
  to_schedule: "border-sky-400 bg-sky-50 text-sky-900",
  ready_to_start: "border-teal-500 bg-teal-100 text-teal-900",
  awaiting_report: "border-violet-400 bg-violet-100 text-violet-900",
  awaiting_validation: "border-fuchsia-400 bg-fuchsia-50 text-fuchsia-900",
  awaiting_release: "border-cyan-500 bg-cyan-100 text-cyan-900",
  // Red with a heavier border: this needs a person to look at it.
  anomaly: "border-red-500 bg-red-50 text-red-900",
  completed: "border-emerald-500 bg-emerald-100 text-emerald-900",
  cancelled: "border-slate-300 bg-white text-slate-500 line-through",
  draft: "border-slate-300 bg-slate-100 text-slate-600",
};

const PRIORITY_TONE: Record<string, string> = {
  routine: "border-slate-200 bg-slate-100 text-slate-600",
  urgent: "border-amber-400 bg-amber-50 text-amber-900",
  stat: "border-red-500 bg-red-100 text-red-900",
};

const REPORT_TONE: Record<string, string> = {
  draft: "border-slate-300 bg-white text-slate-700",
  entered: "border-violet-300 bg-violet-50 text-violet-900",
  validated: "border-cyan-400 bg-cyan-50 text-cyan-900",
  released: "border-emerald-400 bg-emerald-50 text-emerald-900",
  cancelled: "border-slate-300 bg-white text-slate-500",
};

export function RadLanePill({
  lane,
  label,
  compact = false,
}: {
  lane: string;
  /** The server's own wording. Falls back to the code when absent. */
  label?: string | null;
  compact?: boolean;
}) {
  const tone = LANE_TONE[lane] ?? LANE_TONE.draft;
  const code = radLaneCode(lane);

  if (compact) {
    return (
      <span
        title={label ?? code}
        className={`inline-flex h-[19px] min-w-[46px] items-center justify-center rounded border px-1 cl-meta font-bold ${tone}`}
      >
        {code}
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 cl-secondary font-bold ${tone}`}
    >
      <span className="cl-micro font-mono">{code}</span>
      <span aria-hidden className="opacity-40">
        ·
      </span>
      {label ?? code}
    </span>
  );
}

export function RadPriorityPill({
  priority,
  label,
  compact = false,
}: {
  priority: string | null;
  label?: string | null;
  compact?: boolean;
}) {
  const key = priority ?? "routine";
  const tone = PRIORITY_TONE[key] ?? PRIORITY_TONE.routine;
  const code = radPriorityCode(key);

  return (
    <span
      title={label ?? code}
      className={`inline-flex items-center justify-center rounded border px-1.5 cl-micro font-bold uppercase tracking-[0.04em] ${tone} ${
        compact ? "h-[17px] min-w-[38px]" : "h-[19px] py-0.5"
      }`}
    >
      {compact ? code : (label ?? code)}
    </span>
  );
}

/** A report's own state, in the model's words, uppercase for scanning. */
export function RadReportStatePill({
  state,
  label,
}: {
  state: string;
  label?: string | null;
}) {
  const tone = REPORT_TONE[state] ?? REPORT_TONE.draft;
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-px cl-micro font-bold tracking-wide ${tone}`}
    >
      {(label ?? state).toUpperCase()}
    </span>
  );
}

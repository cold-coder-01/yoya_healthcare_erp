import { pharmacyLaneCode, pharmacyPriorityCode } from "@/lib/pharmacy-desk-format";

/**
 * Lane and priority chips for the Pharmacy Desk.
 *
 * NEVER COLOUR ALONE. Every chip carries a short text code as well as a tone.
 * The tone is keyed on the SERVER's lane key and the label rendered is the
 * server's own wording: the client styles, it does not name.
 */
const LANE_TONE: Record<string, string> = {
  awaiting_preparation: "border-sky-400 bg-sky-50 text-sky-900",
  // Amber: "money is holding this", the register every desk uses for it.
  awaiting_clearance: "border-amber-400 bg-amber-100 text-amber-900",
  ready_to_validate: "border-violet-500 bg-violet-100 text-violet-900",
  partially_supplied: "border-cyan-500 bg-cyan-50 text-cyan-900",
  blocked: "border-orange-500 bg-orange-50 text-orange-900",
  // Red with a heavier border: this needs a person to look at it.
  anomaly: "border-red-500 bg-red-50 text-red-900",
  completed: "border-emerald-500 bg-emerald-100 text-emerald-900",
  cancelled: "border-slate-300 bg-white text-slate-500 line-through",
};
const FALLBACK_TONE = "border-slate-300 bg-slate-100 text-slate-600";

const PRIORITY_TONE: Record<string, string> = {
  routine: "border-slate-200 bg-slate-100 text-slate-600",
  urgent: "border-amber-400 bg-amber-50 text-amber-900",
  emergency: "border-red-500 bg-red-100 text-red-900",
};

export function PharmacyLanePill({
  lane,
  label,
  compact = false,
}: {
  lane: string;
  label?: string | null;
  compact?: boolean;
}) {
  const tone = LANE_TONE[lane] ?? FALLBACK_TONE;
  const code = pharmacyLaneCode(lane);

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
    <span className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 cl-secondary font-bold ${tone}`}>
      <span className="cl-micro font-mono">{code}</span>
      <span aria-hidden className="opacity-40">
        ·
      </span>
      {label ?? code}
    </span>
  );
}

export function PharmacyPriorityPill({
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
  const code = pharmacyPriorityCode(key);
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

/** A yes/no operational verdict, in words, with a tone. Never a number. */
export function VerdictChip({ ok, text }: { ok: boolean | null; text: string }) {
  const tone =
    ok === null
      ? "border-slate-200 bg-white text-slate-500"
      : ok
        ? "border-emerald-300 bg-emerald-50 text-emerald-900"
        : "border-orange-400 bg-orange-50 text-orange-900";
  return (
    <span className={`inline-flex items-center rounded border px-1.5 py-px cl-micro font-bold ${tone}`}>
      {ok === false ? "✕ " : ok ? "✓ " : ""}
      {text}
    </span>
  );
}

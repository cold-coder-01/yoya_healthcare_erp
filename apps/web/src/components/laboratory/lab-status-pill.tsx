import { labPriorityCode, labStatusCode } from "@/lib/lab-desk-format";

/**
 * Status and priority chips for the Laboratory Desk.
 *
 * NEVER COLOUR ALONE. Every chip carries a short text code as well as a tone,
 * and the queue's own header repeats the full wording -- so a technician with
 * any colour vision reads the same fact. The tone is peripheral vision; the
 * letters are certainty. This is the same two-encoding rule the Doctor Desk's
 * queue applies to its stat column.
 *
 * The tones are keyed on the SERVER's status key, and the label rendered is
 * the server's own `status_label`. The client styles; it does not name.
 */
const STATUS_TONE: Record<string, string> = {
  draft: "border-slate-300 bg-slate-100 text-slate-600",
  // Amber, matching the Cashier Desk's "money is holding this" register.
  awaiting_clearance: "border-amber-400 bg-amber-100 text-amber-900",
  // Indigo is this workstation's accent and marks the one status that means
  // "you may act now".
  ready_for_collection: "border-indigo-500 bg-indigo-100 text-indigo-900",
  sample_collected: "border-sky-400 bg-sky-100 text-sky-900",
  in_progress: "border-violet-400 bg-violet-100 text-violet-900",
  completed: "border-emerald-500 bg-emerald-100 text-emerald-900",
  cancelled: "border-slate-300 bg-white text-slate-500 line-through",
};

const PRIORITY_TONE: Record<string, string> = {
  routine: "border-slate-200 bg-slate-100 text-slate-600",
  urgent: "border-amber-400 bg-amber-50 text-amber-900",
  stat: "border-red-500 bg-red-100 text-red-900",
};

export function LabStatusPill({
  status,
  label,
  compact = false,
}: {
  status: string;
  /** The server's own wording. Falls back to the code when absent. */
  label?: string | null;
  compact?: boolean;
}) {
  const tone = STATUS_TONE[status] ?? STATUS_TONE.draft;
  const code = labStatusCode(status);

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

export function LabPriorityPill({
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
  const code = labPriorityCode(key);

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

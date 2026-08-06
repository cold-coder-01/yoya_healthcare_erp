/**
 * Reception clearance and clinical queue stage are DIFFERENT concepts and must
 * never be mistaken for one another at a glance:
 *
 *   reception clearance -> is the money settled for this visit?
 *   clinical queue stage -> where is the patient in the care pathway?
 *
 * The previous implementation rendered both through the clinical StatusBadge,
 * mapping clearance onto stage vocabulary (`ok ? "completed" : "awaiting_payment"`),
 * so the two columns looked identical and clearance inherited a stage label.
 *
 * They are separated here by SHAPE as well as colour, so the distinction
 * survives greyscale, low-quality monitors and colour-blindness:
 *   clearance -> solid filled pill
 *   stage     -> outlined pill with a leading dot
 */
import {
  formatClearanceState,
  formatQueueStage,
} from "@/lib/reception-format";

type Tone = "neutral" | "warning" | "success" | "info" | "critical";

const SOLID: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700 ring-slate-200",
  warning: "bg-amber-100 text-amber-900 ring-amber-200",
  success: "bg-emerald-100 text-emerald-900 ring-emerald-200",
  info: "bg-sky-100 text-sky-900 ring-sky-200",
  critical: "bg-red-100 text-red-900 ring-red-200",
};

const DOT: Record<Tone, string> = {
  neutral: "bg-slate-400",
  warning: "bg-amber-500",
  success: "bg-emerald-600",
  info: "bg-sky-600",
  critical: "bg-red-600",
};

const CLEARANCE_TONES: Record<string, Tone> = {
  not_required: "neutral",
  pending: "warning",
  cleared: "success",
  credit_authorized: "info",
  emergency_bypass: "critical",
};

const STAGE_TONES: Record<string, Tone> = {
  registered: "neutral",
  awaiting_payment: "warning",
  awaiting_triage: "info",
  in_triage: "info",
  awaiting_doctor: "info",
  in_consultation: "success",
  completed: "success",
  cancelled: "neutral",
};

export function ClearanceBadge({
  state,
  ok,
}: {
  state: string | null;
  ok: boolean;
}) {
  // Trust the explicit state when Odoo sent one; fall back to the boolean.
  const key = state ?? (ok ? "cleared" : "pending");
  const tone = CLEARANCE_TONES[key] ?? (ok ? "success" : "warning");
  return (
    <span
      className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-semibold ring-1 ring-inset ${SOLID[tone]}`}
    >
      {formatClearanceState(key)}
    </span>
  );
}

export function QueueStageBadge({ stage }: { stage: string | null }) {
  // Explicit ternary rather than `stage && ...`: an empty-string stage would
  // short-circuit to "" , which is not nullish, so ?? would not apply.
  const tone: Tone = (stage ? STAGE_TONES[stage] : undefined) ?? "neutral";
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-300 bg-white px-2 py-0.5 text-xs font-medium text-slate-700">
      <span
        aria-hidden="true"
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT[tone]}`}
      />
      {formatQueueStage(stage)}
    </span>
  );
}

export function EmergencyTag() {
  return (
    <span className="inline-flex items-center rounded-md bg-red-600 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-white">
      Emergency
    </span>
  );
}

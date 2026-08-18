import { doctorLabel } from "@/lib/doctor-format";
import type { DoctorQueueStage, DoctorTriageStatus } from "@/types/doctor";

/**
 * COLOUR CARRIES ONE MEANING EACH, and selection is not one of them.
 *
 * amber  = something is owed or missing
 * cyan   = triage in flight
 * emerald= satisfied / cleared
 * indigo = consultation under way
 * red    = urgency
 * slate  = neutral, no state
 *
 * The queue's selected row is a slate tint plus an emerald rail, never a
 * status colour -- the same rule the Front Desk queue settled on after amber
 * selection made every selected patient look like they owed money.
 */

const BADGE_BASE =
  "inline-flex h-5 shrink-0 items-center rounded border px-1.5 text-[10px] font-bold uppercase tracking-wide";

const TRIAGE_TONE: Record<DoctorTriageStatus, string> = {
  not_started: "border-slate-300 bg-slate-100 text-slate-700",
  waiting: "border-amber-400 bg-amber-50 text-amber-900",
  in_progress: "border-cyan-300 bg-cyan-50 text-cyan-900",
  completed: "border-emerald-300 bg-emerald-50 text-emerald-900",
  cancelled: "border-slate-300 bg-white text-slate-500",
};

export function TriageBadge({ status }: { status: DoctorTriageStatus }) {
  return (
    <span className={`${BADGE_BASE} ${TRIAGE_TONE[status]}`}>
      {doctorLabel(status)}
    </span>
  );
}

const PRIORITY_TONE: Record<string, string> = {
  routine: "border-slate-300 bg-slate-100 text-slate-700",
  urgent: "border-orange-400 bg-orange-50 text-orange-900",
  emergency: "border-red-400 bg-red-50 text-red-900",
};

export function PriorityBadge({ priority }: { priority: string | null }) {
  if (!priority) return null;
  return (
    <span className={`${BADGE_BASE} ${PRIORITY_TONE[priority] ?? PRIORITY_TONE.routine}`}>
      {doctorLabel(priority)}
    </span>
  );
}

const STATE_TONE: Record<string, string> = {
  confirmed: "border-slate-300 bg-slate-100 text-slate-700",
  in_consultation: "border-indigo-300 bg-indigo-50 text-indigo-900",
  done: "border-slate-300 bg-white text-slate-600",
  cancelled: "border-slate-300 bg-white text-slate-500",
  draft: "border-slate-300 bg-white text-slate-600",
};

export function VisitStateBadge({ state }: { state: string | null }) {
  if (!state) return null;
  return (
    <span className={`${BADGE_BASE} ${STATE_TONE[state] ?? STATE_TONE.confirmed}`}>
      {doctorLabel(state)}
    </span>
  );
}

/**
 * The clearance indicator: a VERDICT, never an amount.
 *
 * There is no figure to show here and no field on the payload that carries
 * one. A doctor needs to know whether the financial gate will refuse and why;
 * what is owed, what was paid and who is responsible are the cashier's screen.
 */
export function ClearanceBadge({ blocked }: { blocked: boolean }) {
  return (
    <span
      className={`${BADGE_BASE} ${
        blocked
          ? "border-amber-400 bg-amber-50 text-amber-900"
          : "border-emerald-300 bg-emerald-50 text-emerald-900"
      }`}
    >
      {blocked ? "Not cleared" : "Cleared"}
    </span>
  );
}

/**
 * The AUTHORITATIVE queue stage: hospital.appointment.front_desk_stage.
 *
 * Emerald is reserved for ready_doctor, and this is the only badge on the desk
 * allowed to render it -- the one state that means "you may see this patient".
 * awaiting_cashier is amber (something is owed), triage cyan (in flight),
 * everything else neutral slate.
 */
const STAGE_TONE: Record<string, string> = {
  new: "border-slate-300 bg-slate-100 text-slate-700",
  intake: "border-slate-300 bg-slate-100 text-slate-700",
  triage: "border-cyan-300 bg-cyan-50 text-cyan-900",
  awaiting_cashier: "border-amber-400 bg-amber-50 text-amber-900",
  ready_doctor: "border-emerald-400 bg-emerald-50 text-emerald-900",
  in_consultation: "border-indigo-300 bg-indigo-50 text-indigo-900",
  completed: "border-slate-300 bg-white text-slate-600",
  cancelled: "border-slate-300 bg-white text-slate-500",
};

export function StageBadge({ stage }: { stage: DoctorQueueStage }) {
  return (
    <span className={`${BADGE_BASE} ${STAGE_TONE[stage] ?? STAGE_TONE.intake}`}>
      {doctorLabel(stage)}
    </span>
  );
}

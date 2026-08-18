import { doctorLabel } from "@/lib/doctor-format";
import type { DoctorQueueStage } from "@/types/doctor";

/**
 * COLOUR CARRIES ONE MEANING EACH, and selection is not one of them.
 *
 * emerald = ready / cleared / satisfied
 * amber   = something is owed or missing
 * cyan    = triage in flight
 * indigo  = consultation under way
 * red     = urgency
 * slate   = neutral, no state
 *
 * The queue's selected row is a slate tint plus an emerald rail, never a
 * status colour -- the same rule the Front Desk queue settled on after amber
 * selection made every selected patient look like they owed money.
 *
 * ONE CONCEPT IS SHOWN ONCE.
 * The header used to carry five badges -- stage, appointment state, triage
 * status, clearance and priority -- and four of them answered the same
 * question. front_desk_stage already composes appointment state, the nursing
 * evaluation and financial clearance, so "In Consultation" next to
 * "In Consultation", or "Ready" next to "Cleared", added length without adding
 * information and diluted the one badge that matters. StageBadge is therefore
 * the primary indicator; priority and visit type qualify it only when they are
 * not the default. VisitStateBadge and TriageBadge were removed rather than
 * left unused.
 */

const BADGE_BASE =
  "inline-flex h-[22px] shrink-0 items-center gap-1 rounded-md border px-2 text-[10px] font-bold uppercase tracking-[0.06em]";

/**
 * The AUTHORITATIVE queue stage: hospital.appointment.front_desk_stage.
 *
 * Emerald is reserved for ready_doctor, and this is the only badge on the desk
 * allowed to render it -- the one state that means "you may see this patient".
 * The wording is the operator's, not the schema's: a doctor reads
 * "Ready for doctor", not "ready_doctor".
 */
const STAGE_TONE: Record<DoctorQueueStage, { chip: string; label: string; dot: string }> = {
  new: {
    chip: "border-slate-300 bg-slate-100 text-slate-700",
    label: "Intake",
    dot: "bg-slate-400",
  },
  intake: {
    chip: "border-slate-300 bg-slate-100 text-slate-700",
    label: "Awaiting triage",
    dot: "bg-slate-400",
  },
  triage: {
    chip: "border-cyan-300 bg-cyan-50 text-cyan-900",
    label: "In triage",
    dot: "bg-cyan-500",
  },
  awaiting_cashier: {
    chip: "border-amber-400 bg-amber-50 text-amber-900",
    label: "Waiting for cashier",
    dot: "bg-amber-500",
  },
  ready_doctor: {
    chip: "border-emerald-500 bg-emerald-50 text-emerald-900",
    label: "Ready for doctor",
    dot: "bg-emerald-600",
  },
  in_consultation: {
    chip: "border-indigo-300 bg-indigo-50 text-indigo-900",
    label: "In consultation",
    dot: "bg-indigo-500",
  },
  completed: {
    chip: "border-slate-300 bg-white text-slate-600",
    label: "Completed",
    dot: "bg-slate-400",
  },
  cancelled: {
    chip: "border-slate-300 bg-white text-slate-500",
    label: "Cancelled",
    dot: "bg-slate-300",
  },
};

export function stageTone(stage: DoctorQueueStage) {
  return STAGE_TONE[stage] ?? STAGE_TONE.intake;
}

export function StageBadge({ stage }: { stage: DoctorQueueStage }) {
  const tone = stageTone(stage);
  return (
    <span className={`${BADGE_BASE} ${tone.chip}`}>
      <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
      {tone.label}
    </span>
  );
}

const PRIORITY_TONE: Record<string, string> = {
  urgent: "border-orange-400 bg-orange-50 text-orange-900",
  emergency: "border-red-500 bg-red-50 text-red-900",
};

/**
 * Triage priority, shown ONLY when it is not routine.
 *
 * A "Routine" badge on nine rows out of ten is noise that makes the tenth --
 * the emergency -- harder to see, which is the opposite of what a priority
 * indicator is for.
 */
export function PriorityBadge({ priority }: { priority: string | null }) {
  if (!priority || !PRIORITY_TONE[priority]) return null;
  return (
    <span className={`${BADGE_BASE} ${PRIORITY_TONE[priority]}`}>
      <svg
        aria-hidden
        viewBox="0 0 16 16"
        fill="currentColor"
        className="h-3 w-3"
      >
        <path d="M8 1.5 15 14H1L8 1.5Zm0 4.2a.8.8 0 0 0-.8.85l.25 3.1a.55.55 0 0 0 1.1 0l.25-3.1A.8.8 0 0 0 8 5.7Zm0 5.3a.85.85 0 1 0 0 1.7.85.85 0 0 0 0-1.7Z" />
      </svg>
      {doctorLabel(priority)}
    </span>
  );
}

/**
 * Visit type, shown ONLY when it is not routine -- same reasoning as priority.
 */
export function VisitTypeBadge({ visitType }: { visitType: string | null }) {
  if (!visitType || visitType === "routine") return null;
  return (
    <span className={`${BADGE_BASE} border-slate-300 bg-white text-slate-700`}>
      {doctorLabel(visitType)}
    </span>
  );
}

/*
 * THERE IS NO ClearanceBadge HERE ANY MORE, AND THAT IS DELIBERATE.
 *
 * The clearance VERDICT is still shown -- it moved to the panel footer, where
 * it tints the action strip and carries Odoo's allowlisted reason sentence.
 * As a badge it could only ever restate the stage: a visit at ready_doctor is
 * necessarily cleared (front_desk_stage will not return it otherwise), and one
 * at awaiting_cashier is necessarily not. "Ready for doctor · Cleared" is one
 * fact wearing two chips.
 *
 * The confidentiality rule is unchanged and still enforced server-side: the
 * doctor surface receives a verdict, a categorical state and a fixed sentence,
 * never an amount and never a payer name.
 */

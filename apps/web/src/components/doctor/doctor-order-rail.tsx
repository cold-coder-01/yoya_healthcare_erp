import { doctorLabel } from "@/lib/doctor-format";

/**
 * The Clinical Actions rail: the workspace prepared for ordering.
 *
 * The vendor OPD screen keeps a permanent right-hand column of order types, and
 * a doctor's hand goes to it the moment a consultation opens. Reserving that
 * column now means ordering lands into a layout doctors already read, instead
 * of forcing a re-teach later.
 *
 * NOTHING HERE IS INTERACTIVE, AND IT DOES NOT PRETEND TO BE.
 * No ordering endpoint exists on the doctor surface yet, so these are rendered
 * as inert rows -- not buttons, not disabled buttons that invite a click and
 * swallow it. A control that looks pressable and does nothing is worse than an
 * honest placeholder, especially in a clinical tool where a doctor may believe
 * an order was placed.
 *
 * WHY THEY ARE ROWS AND NOT GREYED-OUT TILES.
 * The previous rail rendered dashed, washed-out chips that read as a broken or
 * disabled control panel -- the doctor's first question was "why is this
 * greyed out?", not "what is coming?". These are full-contrast, legible rows
 * with a leading rule, which read as a roadmap of the workstation rather than
 * as functionality that has failed to load. Nothing is dimmed; what changes
 * between the two states is the WORDING, not the opacity.
 *
 * THE BADGE IS ABOUT ORDERING, NOT ABOUT THE CONSULTATION.
 * It used to read "Open" once a visit reached in_consultation, which became a
 * lie the moment the consultation note itself started working in the centre
 * column: a doctor reading "Open" beside Laboratory would reasonably conclude
 * they could order a test. Ordering is unavailable in EVERY state in this
 * slice, so the badge says so in every state.
 */

type ActionGroup = {
  title: string;
  /** Marked on the single item that is genuinely next in the plan. */
  next?: string;
  items: string[];
};

const ACTION_GROUPS: ActionGroup[] = [
  { title: "Diagnosis", next: "Diagnosis", items: ["Diagnosis"] },
  { title: "Investigations", items: ["Laboratory", "Radiology", "Pathology"] },
  { title: "Treatment", items: ["Medication", "Procedure", "Injection"] },
  {
    title: "Documentation",
    items: ["Certificate", "Referral", "Progress note"],
  },
];

function LockIcon({ className }: { className: string }) {
  return (
    <svg
      aria-hidden
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      className={className}
    >
      <rect x="3.25" y="7" width="9.5" height="6.75" rx="1.5" />
      <path d="M5.5 7V5a2.5 2.5 0 0 1 5 0v2" strokeLinecap="round" />
    </svg>
  );
}

export default function DoctorOrderRail({
  visitState,
  encounterName,
}: {
  visitState: string | null;
  encounterName: string | null;
}) {
  const active = visitState === "in_consultation";

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <header className="flex h-9 shrink-0 items-center justify-between border-b border-slate-200 bg-slate-50 px-2.5">
        <h2 className="text-[10.5px] font-bold uppercase tracking-[0.08em] text-slate-700">
          Clinical Actions
        </h2>
        <span className="inline-flex items-center gap-1 rounded bg-slate-200 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-slate-600">
          <LockIcon className="h-2.5 w-2.5" />
          Not yet
        </span>
      </header>

      {/* The state, stated first. A doctor should never have to scan the whole
          column to learn why none of it does anything. */}
      <div
        className={`shrink-0 border-b px-2.5 py-1.5 ${
          active
            ? "border-emerald-100 bg-emerald-50/60"
            : "border-slate-200 bg-slate-50"
        }`}
      >
        {active ? (
          <p className="text-[10px] leading-snug text-emerald-900">
            <span className="font-semibold">Note open for writing</span> on{" "}
            <span className="font-mono font-semibold">
              {encounterName ?? "this encounter"}
            </span>
            . Ordering arrives in the next clinical slice.
          </p>
        ) : (
          <p className="text-[10px] leading-snug text-slate-600">
            <span className="font-semibold text-slate-800">
              Start the consultation to open the clinical note.
            </span>{" "}
            This visit is{" "}
            <span className="font-semibold text-slate-700">
              {doctorLabel(visitState)}
            </span>
            .
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-2">
        <div className="flex flex-col gap-2.5">
          {ACTION_GROUPS.map((group) => (
            <div key={group.title} className="flex flex-col gap-1">
              <h3 className="text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500">
                {group.title}
              </h3>
              <ul className="flex flex-col gap-px border-l border-slate-200 pl-2">
                {group.items.map((item) => (
                  <li
                    key={item}
                    className="flex items-center justify-between gap-1.5 py-[3px]"
                  >
                    <span className="truncate text-[11px] font-semibold text-slate-700">
                      {item}
                    </span>
                    {group.next === item ? (
                      <span className="shrink-0 rounded bg-emerald-50 px-1 py-px text-[8.5px] font-bold uppercase tracking-wide text-emerald-700 ring-1 ring-inset ring-emerald-200">
                        Next
                      </span>
                    ) : (
                      <span className="shrink-0 text-[8.5px] font-semibold uppercase tracking-wide text-slate-400">
                        Soon
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <p className="mt-2.5 border-t border-slate-200 pt-1.5 text-[9px] leading-snug text-slate-400">
          This column is reserved so ordering lands where doctors already look.
          The consultation note itself is written in the centre panel.
        </p>
      </div>
    </section>
  );
}

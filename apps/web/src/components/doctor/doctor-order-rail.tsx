import { doctorLabel } from "@/lib/doctor-format";

/**
 * The Clinical Actions rail: the workspace prepared for ordering.
 *
 * The vendor OPD screen keeps a permanent right-hand column of order types, and
 * a doctor's hand goes to it the moment a consultation opens. Reserving that
 * column now means ordering lands into a layout doctors already read, instead
 * of forcing a re-teach later.
 *
 * EXACTLY ONE CONTROL HERE IS INTERACTIVE, AND ONLY WHEN IT WORKS.
 * Diagnosis ships in this slice, so it is a real button that focuses the
 * Diagnosis section -- and it is rendered ONLY when there is an open
 * consultation to focus, rather than being present and inert. Everything below
 * it has no endpoint yet and is rendered as an inert row: not a button, and not
 * a disabled button that invites a click and swallows it. A control that looks
 * pressable and does nothing is worse than an honest placeholder, especially in
 * a clinical tool where a doctor may believe an order was placed.
 *
 * WHY THEY ARE ROWS AND NOT GREYED-OUT TILES.
 * The previous rail rendered dashed, washed-out chips that read as a broken or
 * disabled control panel -- the doctor's first question was "why is this
 * greyed out?", not "what is coming?". These are full-contrast, legible rows
 * with a leading rule, which read as a roadmap of the workstation rather than
 * as functionality that has failed to load. Nothing is dimmed; what changes
 * between the two states is the WORDING, not the opacity.
 *
 * THE HEADER BADGE IS ABOUT ORDERING, NOT ABOUT THE CONSULTATION.
 * It used to read "Open" once a visit reached in_consultation, which became a
 * lie the moment the consultation note started working in the centre column: a
 * doctor reading "Open" beside Laboratory would reasonably conclude they could
 * order a test. ORDERING is still unavailable in every state, so the badge
 * still says so -- the Diagnosis button above carries its own, separate state.
 */

type ActionGroup = { title: string; items: string[] };

/**
 * The groups that are still ahead. DIAGNOSIS IS NOT AMONG THEM ANY MORE: it
 * ships in this slice and is rendered above as a real control, so listing it
 * here as "Soon" would be the same kind of lie the "Open" badge used to be.
 */
const ACTION_GROUPS: ActionGroup[] = [
  // Laboratory is NOT here any more: it ships in this slice and is rendered
  // above as a real control, so listing it as "Soon" would be the same kind of
  // lie the old "Open" badge was.
  { title: "Investigations", items: ["Radiology", "Pathology"] },
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
  diagnosisActive = false,
  onOpenDiagnosis = null,
  laboratoryActive = false,
  onOpenLaboratory = null,
}: {
  visitState: string | null;
  encounterName: string | null;
  /** The centre panel is currently showing the Diagnosis section. */
  diagnosisActive?: boolean;
  /** The centre panel is currently showing Orders. */
  laboratoryActive?: boolean;
  /**
   * Focuses Orders > Laboratory. Null when there is no open consultation, so
   * the control is absent rather than present and inert.
   */
  onOpenLaboratory?: (() => void) | null;
  /**
   * Focuses the Diagnosis section. Null when there is no open consultation to
   * focus, which is what keeps the control from existing at all rather than
   * existing and doing nothing.
   */
  onOpenDiagnosis?: (() => void) | null;
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
            . Diagnosis and laboratory ordering are available.
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
        {/* THE ONE LIVE ACTION. A real button when there is a consultation to
            act on, and simply absent otherwise -- never a dead control. */}
        {onOpenDiagnosis ? (
          <div className="mb-2.5 flex flex-col gap-1">
            <h3 className="text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500">
              Clinical
            </h3>
            <button
              type="button"
              onClick={onOpenDiagnosis}
              aria-current={diagnosisActive ? "true" : undefined}
              className={`flex items-center justify-between gap-1.5 rounded border px-2 py-1.5 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 ${
                diagnosisActive
                  ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                  : "border-slate-200 bg-white text-slate-700 hover:border-emerald-300 hover:bg-emerald-50/60"
              }`}
            >
              <span className="truncate text-[11px] font-bold">Diagnosis</span>
              <span className="shrink-0 text-[8.5px] font-bold uppercase tracking-wide text-emerald-700">
                {diagnosisActive ? "Open" : "Record"}
              </span>
            </button>
            {onOpenLaboratory ? (
              <button
                type="button"
                onClick={onOpenLaboratory}
                aria-current={laboratoryActive ? "true" : undefined}
                className={`flex items-center justify-between gap-1.5 rounded border px-2 py-1.5 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-emerald-600 ${
                  laboratoryActive
                    ? "border-emerald-300 bg-emerald-50 text-emerald-900"
                    : "border-slate-200 bg-white text-slate-700 hover:border-emerald-300 hover:bg-emerald-50/60"
                }`}
              >
                <span className="truncate text-[11px] font-bold">Laboratory</span>
                <span className="shrink-0 text-[8.5px] font-bold uppercase tracking-wide text-emerald-700">
                  {laboratoryActive ? "Open" : "Order"}
                </span>
              </button>
            ) : null}
          </div>
        ) : null}

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
                    <span className="shrink-0 text-[8.5px] font-semibold uppercase tracking-wide text-slate-400">
                      Soon
                    </span>
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

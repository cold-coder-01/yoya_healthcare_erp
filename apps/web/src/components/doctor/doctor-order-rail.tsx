import { doctorLabel } from "@/lib/doctor-format";

/**
 * The Order Key rail: the workspace prepared for clinical documentation.
 *
 * The vendor OPD screen keeps a permanent right-hand grid of order types, and
 * a doctor's hand goes to it the moment a consultation opens. Reserving that
 * column now means documentation lands into a layout doctors already read,
 * instead of forcing a re-teach later.
 *
 * NOTHING HERE IS INTERACTIVE, AND IT DOES NOT PRETEND TO BE.
 * No ordering endpoint exists on the doctor surface yet, so these are rendered
 * as inert labels -- not buttons, not disabled buttons that invite a click and
 * swallow it. A control that looks pressable and does nothing is worse than an
 * honest placeholder, especially in a clinical tool where a doctor may believe
 * an order was placed.
 *
 * LOCKED MUST READ AS "NOT YET", NOT AS "BROKEN".
 * The rail previously dimmed the whole column to 45% opacity, which made a
 * third of the screen look like a rendering failure and left the reason
 * buried in a paragraph at the bottom. The state is now stated FIRST, in a
 * banner that says what unlocks it, and the items below keep legible contrast
 * -- they are a preview of the workspace, and a preview nobody can read is
 * not worth reserving the column for.
 */

const ORDER_GROUPS: Array<{ title: string; hint: string; items: string[] }> = [
  {
    title: "Diagnostics",
    hint: "Investigations",
    items: ["Laboratory", "Radiology", "Pathology", "Endoscopy", "Echocardiography"],
  },
  {
    title: "Treatment",
    hint: "Therapeutics",
    items: ["Medication", "Injection", "Procedure", "Physiotherapy", "Anesthesia"],
  },
  {
    title: "Documentation",
    hint: "Clinical record",
    items: ["Progress note", "Diagnosis", "OP note", "Certificate"],
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
    <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <header className="flex h-9 shrink-0 items-center justify-between border-b border-slate-200 bg-slate-50 px-3">
        <h2 className="text-[11px] font-bold uppercase tracking-[0.08em] text-slate-700">
          Order Key
        </h2>
        <span
          className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${
            active
              ? "bg-indigo-100 text-indigo-800"
              : "bg-slate-200 text-slate-600"
          }`}
        >
          {active ? (
            <>
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
              Open
            </>
          ) : (
            <>
              <LockIcon className="h-2.5 w-2.5" />
              Locked
            </>
          )}
        </span>
      </header>

      {/* The state, stated first. A doctor should never have to scroll to the
          bottom of a dimmed column to learn why it is dimmed. */}
      <div
        className={`shrink-0 border-b px-2.5 py-2 ${
          active
            ? "border-indigo-100 bg-indigo-50/60"
            : "border-slate-200 bg-slate-50"
        }`}
      >
        {active ? (
          <p className="text-[10px] leading-snug text-indigo-900">
            Consultation open on{" "}
            <span className="font-mono font-semibold">
              {encounterName ?? "this encounter"}
            </span>
            . Ordering arrives in the next phase.
          </p>
        ) : (
          <div className="flex gap-2">
            <LockIcon className="mt-px h-3.5 w-3.5 shrink-0 text-slate-400" />
            <p className="text-[10px] leading-snug text-slate-600">
              <span className="font-semibold text-slate-800">
                Start the consultation to unlock ordering.
              </span>{" "}
              This visit is{" "}
              <span className="font-semibold text-slate-700">
                {doctorLabel(visitState)}
              </span>
              .
            </p>
          </div>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2.5">
        <div className="flex flex-col gap-3">
          {ORDER_GROUPS.map((group) => (
            <div key={group.title} className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between gap-2">
                <h3
                  className={`text-[9px] font-bold uppercase tracking-[0.08em] ${
                    active ? "text-slate-600" : "text-slate-500"
                  }`}
                >
                  {group.title}
                </h3>
                <span className="truncate text-[9px] text-slate-400">
                  {group.hint}
                </span>
              </div>
              <ul className="grid grid-cols-2 gap-1">
                {group.items.map((item) => (
                  <li
                    key={item}
                    className={`truncate rounded border px-1.5 py-1 text-[10px] font-semibold transition-colors ${
                      active
                        ? "border-slate-200 bg-white text-slate-700"
                        : "border-dashed border-slate-200 bg-slate-50 text-slate-500"
                    }`}
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <p className="mt-3 border-t border-slate-200 pt-2 text-[9px] leading-snug text-slate-400">
          Ordering and clinical notes arrive in a later phase. This column is
          reserved so they land where doctors already look.
        </p>
      </div>
    </section>
  );
}

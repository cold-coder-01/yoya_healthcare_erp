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
 * The rail dims until the consultation is actually in progress, which mirrors
 * the real rule: nothing may be ordered against a visit that has not started.
 */

const ORDER_GROUPS: Array<{ title: string; items: string[] }> = [
  {
    title: "Diagnostics",
    items: ["Laboratory", "Radiology", "Pathology", "Endoscopy", "Echocardiography"],
  },
  {
    title: "Treatment",
    items: ["Medication", "Injection", "Procedure", "Physiotherapy", "Anesthesia"],
  },
  {
    title: "Documentation",
    items: ["Progress note", "Diagnosis", "OP note", "Certificate"],
  },
];

export default function DoctorOrderRail({
  visitState,
  encounterName,
}: {
  visitState: string | null;
  encounterName: string | null;
}) {
  const active = visitState === "in_consultation";

  return (
    <section className="flex min-h-0 flex-col overflow-hidden rounded border border-slate-200 bg-white shadow-sm">
      <header className="flex h-8 shrink-0 items-center justify-between border-b border-slate-200 bg-slate-50 px-2.5">
        <h2 className="text-[11px] font-bold uppercase tracking-wide text-slate-700">
          Order Key
        </h2>
        <span
          className={`text-[10px] font-bold uppercase tracking-wide ${
            active ? "text-emerald-700" : "text-slate-400"
          }`}
        >
          {active ? "Consultation open" : "Locked"}
        </span>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <div className={`flex flex-col gap-2.5 ${active ? "" : "opacity-45"}`}>
          {ORDER_GROUPS.map((group) => (
            <div key={group.title} className="flex flex-col gap-1">
              <h3 className="text-[9px] font-bold uppercase tracking-wide text-slate-500">
                {group.title}
              </h3>
              <ul className="grid grid-cols-2 gap-1">
                {group.items.map((item) => (
                  <li
                    key={item}
                    className="truncate rounded border border-dashed border-slate-300 bg-slate-50 px-1.5 py-1 text-[10px] font-semibold text-slate-500"
                  >
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <p className="mt-3 border-t border-slate-200 pt-2 text-[10px] leading-snug text-slate-500">
          {active ? (
            <>
              Consultation is open on{" "}
              <span className="font-mono font-semibold text-slate-700">
                {encounterName ?? "this encounter"}
              </span>
              . Ordering and clinical notes arrive in the next phase; this column is
              reserved for them.
            </>
          ) : (
            <>
              Ordering unlocks once the consultation has been started through the
              hospital workflow. Current visit state:{" "}
              <span className="font-semibold text-slate-700">
                {doctorLabel(visitState)}
              </span>
              .
            </>
          )}
        </p>
      </div>
    </section>
  );
}

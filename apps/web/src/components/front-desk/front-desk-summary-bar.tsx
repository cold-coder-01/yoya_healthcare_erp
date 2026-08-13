import type { FrontDeskCounts } from "@/types/front-desk";

const ITEMS = [
  { key: "today", label: "Today", stage: "" },
  { key: "intake", label: "Intake", stage: "new,intake" },
  { key: "triage", label: "Triage", stage: "triage" },
  {
    key: "awaiting_cashier",
    label: "Awaiting Cashier",
    stage: "awaiting_cashier",
  },
  { key: "ready_doctor", label: "Ready Doctor", stage: "ready_doctor" },
  { key: "urgent", label: "Urgent", stage: null },
] as const;

export default function FrontDeskSummaryBar({
  counts,
  activeStage,
  onStageChange,
}: {
  counts: FrontDeskCounts | null;
  activeStage: string;
  onStageChange: (stage: string) => void;
}) {
  return (
    // h-11, down from h-14: these are scanning counters, not dashboard KPIs.
    // The number stays the loudest thing in the strip at a smaller size.
    <section
      aria-label="Queue summary"
      className="grid h-11 grid-cols-3 overflow-hidden border border-slate-200 bg-white shadow-sm sm:grid-cols-6"
    >
      {ITEMS.map((item) => {
        const active = item.stage !== null && activeStage === item.stage;
        const value = counts?.[item.key];
        // Urgent earns red only when there IS something urgent. A permanently
        // red cell reading 0 is decoration; it trains the eye to ignore it.
        const alarming = item.key === "urgent" && (value ?? 0) > 0;
        const content = (
          <>
            <span
              className={`truncate text-[10px] font-semibold uppercase tracking-wide ${
                alarming
                  ? "text-red-700"
                  : active
                    ? "text-emerald-800"
                    : "text-slate-500"
              }`}
            >
              {item.label}
            </span>
            <span
              className={`shrink-0 text-base font-bold tabular-nums ${
                alarming
                  ? "text-red-700"
                  : active
                    ? "text-emerald-950"
                    : "text-slate-950"
              }`}
            >
              {value ?? "-"}
            </span>
          </>
        );

        return item.stage === null ? (
          <div
            key={item.key}
            className={`flex min-w-0 items-center justify-between gap-2 border-l px-3 ${
              alarming ? "border-red-200 bg-red-50" : "border-slate-200 bg-white"
            }`}
          >
            {content}
          </div>
        ) : (
          <button
            key={item.key}
            type="button"
            aria-pressed={active}
            onClick={() => onStageChange(item.stage)}
            className={`flex min-w-0 items-center justify-between gap-2 border-l border-slate-200 px-3 text-left outline-none first:border-l-0 hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-600 ${
              active ? "bg-emerald-50" : "bg-white"
            }`}
          >
            {content}
          </button>
        );
      })}
    </section>
  );
}

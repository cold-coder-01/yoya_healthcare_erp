import { formatHospitalTime } from "@/lib/clinical-format";
import { compactGender, frontDeskLabel } from "@/lib/front-desk-format";
import { formatEtb } from "@/lib/reception-format";
import type { FrontDeskQueueRow } from "@/types/front-desk";

const STAGE_TONE: Record<string, string> = {
  new: "border-slate-300 bg-slate-100 text-slate-800",
  intake: "border-slate-300 bg-slate-100 text-slate-800",
  triage: "border-cyan-300 bg-cyan-50 text-cyan-900",
  awaiting_cashier: "border-amber-400 bg-amber-50 text-amber-900",
  ready_doctor: "border-emerald-300 bg-emerald-50 text-emerald-900",
  in_consultation: "border-indigo-300 bg-indigo-50 text-indigo-900",
  completed: "border-slate-300 bg-white text-slate-600",
  cancelled: "border-slate-300 bg-white text-slate-500",
};

export function FrontDeskStageBadge({ stage }: { stage: string }) {
  return (
    <span
      className={`inline-flex h-5 items-center rounded border px-1.5 text-[10px] font-bold uppercase tracking-wide ${
        STAGE_TONE[stage] ?? STAGE_TONE.new
      }`}
    >
      {frontDeskLabel(stage)}
    </span>
  );
}

/**
 * ONE grid template, shared by the column header and every row, so the two can
 * never drift out of alignment.
 *
 * The full seven columns now appear from 640px up, not 1400px. That threshold
 * was calibrated against the OLD shell, where a 256px sidebar left the queue
 * roughly 440px at 1366. Sidebarless, the same viewport gives the queue ~690px,
 * so Age/Sex, Dept and Doctor no longer have to fold into the patient cell at
 * any realistic desk resolution -- the fold survives only for genuinely narrow
 * screens, where nothing is dropped, just restacked.
 */
const GRID =
  "grid grid-cols-[52px_minmax(0,1fr)_78px_88px] gap-x-2 " +
  "sm:grid-cols-[52px_minmax(0,1.5fr)_60px_minmax(0,1fr)_minmax(0,1fr)_78px_88px]";

function QueueRow({
  row,
  selected,
  onSelect,
}: {
  row: FrontDeskQueueRow;
  selected: boolean;
  onSelect: () => void;
}) {
  const showOutstanding =
    row.queue_stage === "awaiting_cashier" || row.clearance.outstanding > 0;

  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        /*
          SELECTION IS NOT STATUS.
          The selected row used to be amber-tinted, which is exactly the colour
          this queue uses to mean "Awaiting Cashier" -- so selecting any patient
          made them look like they owed money. Selection is now a neutral slate
          tint plus a strong emerald rail; amber, red and emerald stay reserved
          for the stage badge and the amount, where they carry meaning.
        */
        className={`${GRID} min-h-[54px] w-full items-center border-b border-slate-200 px-2 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-emerald-600 ${
          selected
            ? "bg-slate-100 shadow-[inset_3px_0_0_#047857] hover:bg-slate-100"
            : "bg-white hover:bg-slate-50"
        }`}
      >
        <span className="text-[11px] font-bold leading-tight tabular-nums text-slate-700">
          {formatHospitalTime(row.arrived_at)}
        </span>

        <span className="min-w-0 self-start">
          <span className="flex min-w-0 items-center gap-1.5">
            {row.urgent ? (
              <span className="h-2 w-2 shrink-0 rounded-full bg-red-600" title="Urgent" />
            ) : null}
            <span className="truncate text-[13px] font-bold leading-tight text-slate-950">
              {row.patient_name}
            </span>
          </span>
          <span className="mt-0.5 block truncate font-mono text-[11px] font-semibold leading-tight text-slate-600">
            {row.mrn ?? "No MRN"}
          </span>
          {/* Folded-in columns, shown only where the grid drops them. */}
          <span className="mt-0.5 block truncate text-[10px] leading-tight text-slate-500 sm:hidden">
            {row.age ?? "-"}y/{compactGender(row.gender)}
            <span className="text-slate-300"> · </span>
            {row.department?.name ?? "No dept"}
            <span className="text-slate-300"> · </span>
            {row.doctor?.name ?? "Unassigned"}
          </span>
        </span>

        <span className="hidden text-[11px] leading-tight text-slate-700 sm:block">
          {row.age ?? "-"}y / {compactGender(row.gender)}
        </span>

        <span className="hidden min-w-0 text-[11px] font-medium leading-tight text-slate-700 sm:block">
          {row.department?.name ?? "No department"}
        </span>

        <span
          className={`hidden min-w-0 text-[11px] font-medium leading-tight sm:block ${
            row.doctor ? "text-slate-700" : "italic text-amber-700"
          }`}
        >
          {row.doctor?.name ?? "Unassigned"}
        </span>

        <span className="flex items-center">
          <FrontDeskStageBadge stage={row.queue_stage} />
        </span>

        <span className="text-right text-[11px] font-bold leading-tight tabular-nums">
          {showOutstanding ? (
            <span
              className={
                row.clearance.outstanding > 0 ? "text-amber-800" : "text-emerald-700"
              }
            >
              {formatEtb(row.clearance.outstanding)}
            </span>
          ) : (
            <span className="text-slate-300">—</span>
          )}
        </span>
      </button>
    </li>
  );
}

export default function FrontDeskQueue({
  rows,
  selectedId,
  loading,
  error,
  truncated,
  onSelect,
}: {
  rows: FrontDeskQueueRow[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  truncated: boolean;
  onSelect: (appointmentId: number) => void;
}) {
  return (
    <section className="flex min-h-[360px] min-w-0 flex-col overflow-hidden rounded border border-slate-200 bg-white shadow-sm min-[1100px]:min-h-0">
      <header className="flex h-8 shrink-0 items-center justify-between border-b border-slate-200 px-2.5">
        <h2 className="text-[11px] font-bold uppercase tracking-wide text-slate-700">
          Patient Queue ({rows.length})
        </h2>
        {loading ? (
          <span className="text-[10px] tabular-nums text-slate-500">Updating…</span>
        ) : null}
      </header>

      {/* Column headers, aligned to the rows by the shared GRID template. */}
      <div
        className={`${GRID} shrink-0 border-b border-slate-200 bg-slate-50 px-2 py-1 text-[9px] font-bold uppercase tracking-wide text-slate-500`}
      >
        <span>Time</span>
        <span>Patient / MRN</span>
        <span className="hidden sm:block">Age / Sex</span>
        <span className="hidden sm:block">Dept</span>
        <span className="hidden sm:block">Doctor</span>
        <span>Stage</span>
        <span className="text-right">Amount</span>
      </div>

      {error ? (
        <div className="border-b border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
          {error}
        </div>
      ) : null}
      {truncated ? (
        <div className="border-b border-amber-200 bg-amber-50 px-3 py-1.5 text-[11px] text-amber-900">
          Queue limit reached. Narrow the filters to see all patients.
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading && rows.length === 0 ? (
          <div className="space-y-px" aria-label="Loading patient queue">
            {Array.from({ length: 8 }, (_, index) => (
              <div
                key={index}
                className="h-[54px] animate-pulse border-b border-slate-100 bg-slate-50"
              />
            ))}
          </div>
        ) : rows.length === 0 ? (
          /* Subtle and centred. A queue with nobody in it is a normal state at
             a hospital entrance, not an error worth an illustration. */
          <div className="flex h-full min-h-32 items-center justify-center px-6 text-center">
            <p className="text-xs text-slate-500">
              No patients match the current filters.
            </p>
          </div>
        ) : (
          <ul>
            {rows.map((row) => (
              <QueueRow
                key={row.appointment_id}
                row={row}
                selected={selectedId === row.appointment_id}
                onSelect={() => onSelect(row.appointment_id)}
              />
            ))}
          </ul>
        )}
      </div>

      <footer className="flex h-7 shrink-0 items-center border-t border-slate-200 bg-slate-50 px-2.5 text-[10px] text-slate-500">
        Showing {rows.length} {rows.length === 1 ? "patient" : "patients"}
        {truncated ? " (limit reached)" : ""}
      </footer>
    </section>
  );
}

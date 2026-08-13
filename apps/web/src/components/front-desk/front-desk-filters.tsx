"use client";

/**
 * Front Desk filter toolbar.
 *
 * ONE compact operational band, not a form block. Filters are secondary
 * controls at a busy entrance: the original wrapped to two rows of 36px
 * controls with 11px stacked labels and dominated the top of the workstation.
 * Labels are now 9px and sit tight above 32px controls, so the whole toolbar is
 * a single ~55px row. Every filter stays on screen -- nothing is hidden behind
 * a popup.
 *
 * The department and doctor pickers are local rather than
 * components/reception/reference-filters, which is shared with the legacy
 * /reception queue and carries that page's taller styling. Only the markup is
 * duplicated; the fetching lives in the shared useDepartments/useDoctors hooks.
 */
import { useDepartments } from "@/lib/use-departments";
import { useDoctors } from "@/lib/use-doctors";

export const FRONT_DESK_STAGE_OPTIONS = [
  { value: "", label: "All active stages" },
  { value: "new,intake", label: "Intake" },
  { value: "triage", label: "Triage" },
  { value: "awaiting_cashier", label: "Awaiting Cashier" },
  { value: "ready_doctor", label: "Ready Doctor" },
  { value: "in_consultation", label: "In Consultation" },
  { value: "completed", label: "Completed" },
  { value: "cancelled", label: "Cancelled" },
];

const CONTROL =
  "h-8 w-full min-w-0 rounded border border-slate-300 bg-white px-2 text-xs text-slate-900 outline-none " +
  "focus:border-emerald-600 focus:ring-1 focus:ring-emerald-200 " +
  "disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

const LABEL =
  "mb-0.5 block text-[9px] font-bold uppercase leading-none tracking-wide text-slate-500";

export default function FrontDeskFilters({
  date,
  stage,
  departmentId,
  doctorId,
  search,
  loading,
  onDateChange,
  onStageChange,
  onDepartmentChange,
  onDoctorChange,
  onSearchChange,
  onRefresh,
  onNewVisit,
}: {
  date: string;
  stage: string;
  departmentId: string;
  doctorId: string;
  search: string;
  loading: boolean;
  onDateChange: (value: string) => void;
  onStageChange: (value: string) => void;
  onDepartmentChange: (value: string) => void;
  onDoctorChange: (value: string) => void;
  onSearchChange: (value: string) => void;
  onRefresh: () => void;
  onNewVisit: () => void;
}) {
  const { departments, loading: departmentsLoading } = useDepartments();
  const { options: doctors, loading: doctorsLoading } = useDoctors(
    departmentId ? Number(departmentId) : null,
  );

  return (
    <section className="rounded border border-slate-200 bg-white px-2.5 py-1.5 shadow-sm">
      <div className="flex flex-wrap items-end gap-x-2 gap-y-1.5">
        <label className="w-[118px] shrink-0">
          <span className={LABEL}>Date</span>
          <input
            type="date"
            value={date}
            onChange={(event) => onDateChange(event.target.value)}
            className={CONTROL}
          />
        </label>

        <label className="w-[132px] shrink-0">
          <span className={LABEL}>Stage</span>
          <select
            value={stage}
            onChange={(event) => onStageChange(event.target.value)}
            className={CONTROL}
          >
            {FRONT_DESK_STAGE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="w-[138px] shrink-0">
          <span className={LABEL}>Dept</span>
          <select
            value={departmentId}
            disabled={departmentsLoading}
            onChange={(event) => onDepartmentChange(event.target.value)}
            className={CONTROL}
          >
            <option value="">
              {departmentsLoading ? "Loading…" : "All departments"}
            </option>
            {departments.map((department) => (
              <option key={department.id} value={String(department.id)}>
                {department.name}
              </option>
            ))}
          </select>
        </label>

        <label className="w-[138px] shrink-0">
          <span className={LABEL}>Doctor</span>
          <select
            value={doctorId}
            disabled={doctorsLoading}
            onChange={(event) => onDoctorChange(event.target.value)}
            className={CONTROL}
          >
            <option value="">
              {doctorsLoading ? "Loading…" : "All doctors"}
            </option>
            {doctors.map((doctor) => (
              <option key={doctor.id} value={String(doctor.id)}>
                {doctor.name}
              </option>
            ))}
          </select>
        </label>

        <label className="min-w-[200px] flex-[2]">
          <span className={LABEL}>Patient / MRN</span>
          <input
            type="search"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            className={CONTROL}
            placeholder="Name or MRN"
          />
        </label>

        {/*
          The desk's primary action. Opens the registration flow IN the
          workstation -- it deliberately does not link to the legacy
          /reception/new page, because the front desk nurse owns reception,
          intake and triage as one job and must not be sent to another
          workspace to start it.
        */}
        <div className="flex shrink-0 items-end gap-1.5">
          <button
            type="button"
            onClick={onNewVisit}
            className="h-8 whitespace-nowrap rounded bg-emerald-700 px-3 text-xs font-bold text-white outline-none transition hover:bg-emerald-800 focus-visible:ring-2 focus-visible:ring-emerald-600"
          >
            + New Visit
          </button>
          <button
            type="button"
            onClick={onRefresh}
            disabled={loading}
            title="Refresh queue"
            aria-label="Refresh queue"
            className="flex h-8 w-8 items-center justify-center rounded border border-slate-300 bg-white text-base leading-none text-slate-700 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-emerald-600 disabled:text-slate-400"
          >
            &#8635;
          </button>
        </div>
      </div>
    </section>
  );
}

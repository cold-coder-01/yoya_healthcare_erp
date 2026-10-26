"use client";

import type { ReactNode } from "react";

import {
  DELIVERED_HELP,
  DELIVERED_LABEL,
  DIRECT_REVENUE_HELP,
  DIRECT_REVENUE_LABEL,
  EPISODE_REVENUE_HELP,
  EPISODE_REVENUE_LABEL,
  NO_DOCTOR_TOTAL_NOTE,
  PATIENTS_NOT_ADDITIVE_NOTE,
  RECOGNIZED_HELP,
  RECOGNIZED_LABEL,
  UNBILLED_EXECUTIVE_LABEL,
  UNBILLED_HELP,
  UNBILLED_LABEL,
  agingLabel,
  formatCount,
  formatMoney,
  formatQuantity,
  formatShare,
} from "@/lib/revenue-analytics-format";
import type {
  AnalyticsDataQuality,
  AnalyticsDepartments,
  AnalyticsDoctors,
  AnalyticsServices,
  AnalyticsSummary,
  AnalyticsUnbilled,
  DoctorSort,
  ServiceGroup,
} from "@/types/revenue-analytics";

import TrendChart from "./trend-chart";

// --- Building blocks ---------------------------------------------------------
export function Card({ title, help, children }: { title?: string; help?: string; children: ReactNode }) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-3">
      {title && <h2 className="text-sm font-bold text-slate-900">{title}</h2>}
      {help && <p className="mt-0.5 text-xs text-slate-500">{help}</p>}
      <div className={title || help ? "mt-2" : ""}>{children}</div>
    </section>
  );
}

function Tile({ label, value, help, tone = "default" }: {
  label: string;
  value: string;
  help?: string;
  tone?: "default" | "muted" | "attention";
}) {
  const ring =
    tone === "attention" ? "border-amber-300 bg-amber-50" : tone === "muted" ? "border-slate-200 bg-slate-50" : "border-slate-200 bg-white";
  return (
    <div className={`rounded-md border p-3 ${ring}`} title={help}>
      <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-xl font-bold tabular-nums text-slate-950">{value}</div>
      {help && <div className="mt-1 text-[11px] leading-snug text-slate-500">{help}</div>}
    </div>
  );
}

function Th({ children, right = false, title }: { children: ReactNode; right?: boolean; title?: string }) {
  return (
    <th title={title} className={`px-2 py-1.5 text-[11px] font-bold uppercase tracking-wide text-slate-500 ${right ? "text-right" : "text-left"}`}>
      {children}
    </th>
  );
}

function Td({ children, right = false, strong = false }: { children: ReactNode; right?: boolean; strong?: boolean }) {
  return (
    <td className={`px-2 py-1.5 ${right ? "text-right tabular-nums" : ""} ${strong ? "font-semibold text-slate-950" : "text-slate-700"}`}>
      {children}
    </td>
  );
}

function Empty() {
  return <p className="py-6 text-center text-sm text-slate-500">No recognized activity in this period.</p>;
}

function topName(entry: { name: string; value: number } | null, money = true) {
  if (!entry) return "—";
  return `${entry.name} · ${money ? formatMoney(entry.value) : formatCount(entry.value)}`;
}

// --- Executive Summary ----------------------------------------------------------
export function SummaryPanel({ summary, finance }: { summary: AnalyticsSummary; finance: boolean }) {
  const { revenue, patients, indicators, top } = summary;
  const currency = summary.scope.currency;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Tile label={`Self-pay ${RECOGNIZED_LABEL}`} value={`${formatMoney(revenue.net)} ${currency}`} help={RECOGNIZED_HELP} />
        {revenue.credits > 0 ? (
          <Tile
            label="Gross / Credit Notes"
            value={`${formatMoney(revenue.gross)} / −${formatMoney(revenue.credits)}`}
            help="Net recognized revenue = gross invoices − credit notes posted in this period."
          />
        ) : (
          <Tile label={DELIVERED_LABEL} value={formatMoney(summary.delivered_value)} help={DELIVERED_HELP} tone="muted" />
        )}
        <Tile label="Unique Patients" value={formatCount(patients.unique)} help="Distinct patients with recognized revenue in this period." />
        <Tile label="Avg Revenue / Patient" value={formatMoney(patients.avg_revenue_per_patient)} help="Net recognized revenue ÷ the same unique patients." />
        <Tile
          label={finance ? UNBILLED_LABEL : UNBILLED_EXECUTIVE_LABEL}
          value={formatMoney(summary.unbilled.value)}
          help={`${UNBILLED_HELP} As of ${summary.unbilled.as_of}; ${formatCount(summary.unbilled.charges)} charges.`}
          tone={summary.unbilled.value > 0 ? "attention" : "default"}
        />
        {revenue.credits > 0 && (
          <Tile label={DELIVERED_LABEL} value={formatMoney(summary.delivered_value)} help={DELIVERED_HELP} tone="muted" />
        )}
        <Tile
          label="Credit-note rate"
          value={formatShare(indicators.credit_note_rate)}
          help={`Credit notes ${formatMoney(indicators.credit_notes)} of gross invoices.`}
          tone="muted"
        />
        <Tile
          label="Advance refund rate"
          value={formatShare(indicators.advance_refund_rate)}
          help={`Patient advance refunds ${formatMoney(indicators.advance_refunds)} of cash received ${formatMoney(indicators.cash_received)}. Refunds never reduce revenue.`}
          tone="muted"
        />
      </div>

      <Card title="Leaders">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
          <Leader label="Top doctor · patient volume" value={topName(top.doctor_by_patients, false)} />
          <Leader label={`Top doctor · ${DIRECT_REVENUE_LABEL}`} value={topName(top.doctor_by_direct_revenue)} />
          <Leader label={`Top doctor · ${EPISODE_REVENUE_LABEL}`} value={topName(top.doctor_by_episode_revenue)} />
          <Leader label="Top department" value={topName(top.department)} />
          <Leader label="Top service category" value={topName(top.category)} />
          <Leader label="Completed consultations" value={`${formatCount(patients.completed_consultations)} (${formatCount(patients.consulted_patients)} patients)`} />
        </dl>
      </Card>

      <Card title={`${RECOGNIZED_LABEL} trend`} help={`${RECOGNIZED_HELP} ${DELIVERED_LABEL} shown for the recognition lag.`}>
        <TrendChart points={summary.trend.points} grain={summary.trend.grain} currency={currency} />
      </Card>

      {summary.excluded_scope.sponsored_revenue !== 0 && (
        <p className="text-xs text-slate-500">
          Outside the v1 headline: payer / sponsored recognized revenue {formatMoney(summary.excluded_scope.sponsored_revenue)} on{" "}
          {formatCount(summary.excluded_scope.sponsored_encounters)} visits.
        </p>
      )}
    </div>
  );
}

function Leader({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 border-b border-slate-100 py-1">
      <dt className="text-slate-500">{label}</dt>
      <dd className="text-right font-semibold text-slate-900">{value}</dd>
    </div>
  );
}

// --- Doctors ------------------------------------------------------------------------
export function DoctorsPanel({ data, sort, onSort }: {
  data: AnalyticsDoctors;
  sort: DoctorSort;
  onSort: (sort: DoctorSort) => void;
}) {
  const sorts: { key: DoctorSort; label: string }[] = [
    { key: "patients", label: "Patient volume" },
    { key: "direct", label: DIRECT_REVENUE_LABEL },
    { key: "episode", label: "Episode-attributed" },
  ];
  return (
    <Card
      title="Doctor Performance"
      help={`${NO_DOCTOR_TOTAL_NOTE} ${PATIENTS_NOT_ADDITIVE_NOTE}`}
    >
      <div className="mb-2 flex flex-wrap items-center gap-1 text-xs">
        <span className="text-slate-500">Rank by</span>
        {sorts.map((option) => (
          <button
            key={option.key}
            type="button"
            onClick={() => onSort(option.key)}
            aria-pressed={sort === option.key}
            className={`rounded px-2 py-1 font-semibold ${sort === option.key ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
          >
            {option.label}
          </button>
        ))}
      </div>
      {data.rows.length === 0 ? <Empty /> : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-200">
                <Th>Doctor</Th>
                <Th>Department</Th>
                <Th right title="Distinct patients with a consultation completed by this doctor">Patients treated</Th>
                <Th right>Completed consultations</Th>
                <Th right title={DIRECT_REVENUE_HELP}>{DIRECT_REVENUE_LABEL}</Th>
                <Th right title={EPISODE_REVENUE_HELP}>{EPISODE_REVENUE_LABEL}</Th>
                <Th right title="Episode-attributed revenue ÷ the distinct patients of those episodes">Avg episode revenue / patient</Th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => (
                <tr key={row.doctor_id ?? "unassigned"} className="border-b border-slate-100">
                  <Td strong>{row.doctor_name}</Td>
                  <Td>{row.department_name ?? "—"}</Td>
                  <Td right>{formatCount(row.patients_treated)}</Td>
                  <Td right>{formatCount(row.completed_consultations)}</Td>
                  <td className="bg-sky-50/60 px-2 py-1.5 text-right tabular-nums font-semibold text-slate-950">{formatMoney(row.direct_revenue)}</td>
                  <td className="bg-amber-50/60 px-2 py-1.5 text-right tabular-nums text-slate-900">{formatMoney(row.episode_revenue)}</td>
                  <Td right>{formatMoney(row.avg_episode_revenue_per_patient)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <dl className="mt-3 grid gap-2 text-xs text-slate-600 sm:grid-cols-2">
        <div className="rounded border border-sky-200 bg-sky-50 p-2"><dt className="font-bold text-slate-800">{DIRECT_REVENUE_LABEL}</dt><dd>{DIRECT_REVENUE_HELP}</dd></div>
        <div className="rounded border border-amber-200 bg-amber-50 p-2"><dt className="font-bold text-slate-800">{EPISODE_REVENUE_LABEL}</dt><dd>{EPISODE_REVENUE_HELP}</dd></div>
      </dl>
    </Card>
  );
}

// --- Services -----------------------------------------------------------------------
export function ServicesPanel({ data, group, onGroup }: {
  data: AnalyticsServices;
  group: ServiceGroup;
  onGroup: (group: ServiceGroup) => void;
}) {
  return (
    <Card
      title="Service Performance"
      help="Service-line revenue, independent of doctor attribution. Quantities are shown per service, never summed across units."
    >
      <div className="mb-2 flex items-center gap-1 text-xs">
        {(["category", "service"] as ServiceGroup[]).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => onGroup(option)}
            aria-pressed={group === option}
            className={`rounded px-2 py-1 font-semibold ${group === option ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700 hover:bg-slate-200"}`}
          >
            {option === "category" ? "By category" : "By service"}
          </button>
        ))}
      </div>
      {data.rows.length === 0 ? <Empty /> : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <Th>{group === "category" ? "Category" : "Service"}</Th>
              {group === "service" && <Th>Category</Th>}
              <Th right>Orders</Th>
              <Th right>Quantity</Th>
              <Th right>Revenue</Th>
              <Th right>Contribution</Th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={`${row.category}:${row.service_id ?? ""}:${row.uom ?? ""}`} className="border-b border-slate-100">
                <Td strong>{row.label}</Td>
                {group === "service" && <Td>{row.category_label}</Td>}
                <Td right>{formatCount(row.orders)}</Td>
                <Td right>{formatQuantity(row.quantity, row.uom)}</Td>
                <Td right>{formatMoney(row.value)}</Td>
                <Td right>{formatShare(row.share)}</Td>
              </tr>
            ))}
            <tr>
              <Td strong>Total</Td>
              {group === "service" && <Td>{""}</Td>}
              <Td right>{""}</Td>
              <Td right>{""}</Td>
              <Td right strong>{formatMoney(data.total)}</Td>
              <Td right>{data.total ? "100.0%" : "—"}</Td>
            </tr>
          </tbody>
        </table>
      )}
    </Card>
  );
}

// --- Departments ----------------------------------------------------------------------
export function DepartmentsPanel({ data }: { data: AnalyticsDepartments }) {
  return (
    <Card
      title="Department Performance"
      help={`Episode-attributed revenue by clinical department: the visit's department before admission, the ward's department during the stay. Laboratory, Radiology and Pharmacy are service lines, not departments. ${PATIENTS_NOT_ADDITIVE_NOTE}`}
    >
      {data.rows.length === 0 ? <Empty /> : (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-200">
              <Th>Department</Th>
              <Th right>Unique patients</Th>
              <Th right>Episode-attributed revenue</Th>
              <Th right>Avg revenue / patient</Th>
              <Th right>Contribution</Th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row) => (
              <tr key={row.department_id ?? "unassigned"} className="border-b border-slate-100">
                <Td strong>{row.department_name}</Td>
                <Td right>{formatCount(row.patients)}</Td>
                <Td right>{formatMoney(row.value)}</Td>
                <Td right>{formatMoney(row.avg_revenue_per_patient)}</Td>
                <Td right>{formatShare(row.share)}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

// --- Data Quality (Finance) ---------------------------------------------------------------
function QualityRow({ label, value, detail, attention }: {
  label: string;
  value: string;
  detail: string;
  attention: boolean;
}) {
  return (
    <tr className="border-b border-slate-100 align-top">
      <td className="px-2 py-1.5">
        <span className={`mr-2 inline-block rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${attention ? "bg-amber-100 text-amber-900" : "bg-emerald-100 text-emerald-900"}`}>
          {attention ? "Review" : "OK"}
        </span>
        <span className="font-semibold text-slate-900">{label}</span>
      </td>
      <td className="px-2 py-1.5 text-right tabular-nums font-semibold text-slate-950">{value}</td>
      <td className="px-2 py-1.5 text-xs text-slate-600">{detail}</td>
    </tr>
  );
}

export function QualityPanel({ quality, unbilled }: { quality: AnalyticsDataQuality; unbilled: AnalyticsUnbilled | null }) {
  const recon = quality.legacy_manual_revenue;
  const legacy = quality.legacy_accounting;
  return (
    <div className="space-y-3">
      <Card title="Data Quality" help="What could distort these figures. Surfaced, never silently compensated.">
        <table className="w-full text-sm">
          <tbody>
            <QualityRow label={UNBILLED_LABEL} attention={quality.unbilled.value > 0}
              value={formatMoney(quality.unbilled.value)}
              detail={`${formatCount(quality.unbilled.charges)} delivered charges no posted invoice carries yet (${formatMoney(quality.unbilled.awaiting_posting_value)} on draft invoices). Outpatient care is not invoiced automatically.`} />
            <QualityRow label="Missing doctor" attention={quality.missing_doctor.count > 0}
              value={formatMoney(quality.missing_doctor.amount)}
              detail={`${formatCount(quality.missing_doctor.count)} visits whose revenue has no episode doctor (shown as Unassigned).`} />
            <QualityRow label="Admission physician fallback" attention={quality.admission_doctor_fallback.count > 0}
              value={formatMoney(quality.admission_doctor_fallback.amount)}
              detail={`${formatCount(quality.admission_doctor_fallback.count)} admissions with no physician; their inpatient revenue was attributed to the visit doctor.`} />
            <QualityRow label="Admission / visit doctor differ" attention={quality.doctor_mismatch.count > 0}
              value={formatCount(quality.doctor_mismatch.count)}
              detail={`Admissions whose physician differs from the visit doctor (${formatMoney(quality.doctor_mismatch.amount)} inpatient revenue). Expected; no multi-doctor split in v1.`} />
            <QualityRow label="Missing department" attention={quality.missing_department.count > 0}
              value={formatMoney(quality.missing_department.amount)}
              detail={`${formatCount(quality.missing_department.count)} visits with no department (shown as Unassigned).`} />
            <QualityRow label="Ward department fallback" attention={quality.ward_department_fallback.count > 0}
              value={formatMoney(quality.ward_department_fallback.amount)}
              detail={`${formatCount(quality.ward_department_fallback.count)} admissions in a ward with no department; attributed to the visit department.`} />
            <QualityRow label="Unattributed legacy / manual revenue" attention={recon.unattributed_amount !== 0}
              value={formatMoney(recon.unattributed_amount)}
              detail={`Revenue-account GL with no charge provenance (${formatCount(recon.unattributed_moves)} entries). Never assigned to a doctor, department or service.`} />
            <QualityRow label="Registration / Card (excluded)" attention={quality.registration_card.charges > 0}
              value={formatCount(quality.registration_card.charges)}
              detail={`Card charges raised in the period, ${formatCount(quality.registration_card.delivered)} delivered; ${formatMoney(quality.registration_card.cash_held)} cash held. Excluded until card delivery semantics are fixed.`} />
            <QualityRow label="Consultation delivery gap" attention={quality.consultation_gap.charges > 0}
              value={formatCount(quality.consultation_gap.charges)}
              detail={`Consultation charges raised in the period never delivered (not completed); ${formatCount(quality.consultation_gap.funded)} funded, ${formatMoney(quality.consultation_gap.cash_held)} cash held.`} />
            <QualityRow label="Payer / sponsored (excluded)" attention={quality.sponsored_excluded.count > 0}
              value={formatMoney(quality.sponsored_excluded.amount)}
              detail={`${formatCount(quality.sponsored_excluded.count)} visits. Outside the v1 self-pay headline until payer accounting is complete.`} />
            <QualityRow label="Legacy accounting pending" attention={legacy.legacy_pending + legacy.not_posted + legacy.visitless_legacy > 0}
              value={formatCount(legacy.legacy_pending + legacy.not_posted)}
              detail={`Of ${formatCount(legacy.discharged)} stays discharged in the period: ${formatCount(legacy.not_posted)} not yet closed in accounting (their revenue is not recognized yet), ${formatCount(legacy.legacy_pending)} legacy refunds pending, ${formatCount(legacy.visitless_legacy)} legacy visit-less stays${legacy.truncated ? " (first stays only)" : ""}.`} />
          </tbody>
        </table>
      </Card>

      <Card title="GL Reconciliation" help="Posted revenue on the configured revenue accounts, explained.">
        <table className="w-full text-sm">
          <tbody>
            <ReconRow label="Hospital GL revenue (revenue accounts)" value={recon.gl_revenue_total} strong />
            <ReconRow label="  Attributed · self-pay (headline)" value={recon.attributed_self_pay} />
            <ReconRow label="  Attributed · payer / sponsored" value={recon.attributed_sponsored} />
            <ReconRow label="  Attributed · excluded categories" value={recon.attributed_excluded_categories} />
            <ReconRow label="  Unattributed legacy / manual" value={recon.unattributed_amount} />
            <ReconRow label="Reconciliation difference" value={recon.difference} strong attention={recon.difference !== 0} />
          </tbody>
        </table>
      </Card>

      {unbilled && (
        <Card title={UNBILLED_LABEL} help={`${UNBILLED_HELP} As of ${unbilled.as_of}.`}>
          <div className="grid gap-3 lg:grid-cols-2">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-200"><Th>Category</Th><Th right>Charges</Th><Th right>Value</Th><Th>Oldest</Th></tr></thead>
              <tbody>
                {unbilled.by_category.map((row) => (
                  <tr key={row.category} className="border-b border-slate-100">
                    <Td strong>{row.label}</Td><Td right>{formatCount(row.charges)}</Td>
                    <Td right>{formatMoney(row.value)}</Td><Td>{row.oldest_delivered_on ?? "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
            <table className="w-full text-sm">
              <thead><tr className="border-b border-slate-200"><Th>Age</Th><Th right>Charges</Th><Th right>Value</Th></tr></thead>
              <tbody>
                {unbilled.aging.map((row) => (
                  <tr key={row.bucket} className="border-b border-slate-100">
                    <Td strong>{agingLabel(row.bucket)}</Td><Td right>{formatCount(row.charges)}</Td><Td right>{formatMoney(row.value)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

function ReconRow({ label, value, strong = false, attention = false }: {
  label: string;
  value: number;
  strong?: boolean;
  attention?: boolean;
}) {
  return (
    <tr className="border-b border-slate-100">
      <td className={`whitespace-pre px-2 py-1.5 ${strong ? "font-semibold text-slate-900" : "text-slate-700"}`}>{label}</td>
      <td className={`px-2 py-1.5 text-right tabular-nums ${strong ? "font-bold" : ""} ${attention ? "text-amber-800" : "text-slate-950"}`}>
        {formatMoney(value)}
      </td>
    </tr>
  );
}

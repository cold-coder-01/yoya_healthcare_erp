"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";

import { messageFromPayload } from "@/lib/api-error";
import {
  DELIVERED_HELP,
  DELIVERED_LABEL,
  RECOGNIZED_HELP,
  RECOGNIZED_LABEL,
  SELF_PAY_SCOPE_NOTE,
  analyticsQuery,
  safeGrain,
  visibleTabs,
  type AnalyticsTab,
} from "@/lib/revenue-analytics-format";
import type {
  AnalyticsBasis,
  AnalyticsDataQuality,
  AnalyticsDepartments,
  AnalyticsDoctors,
  AnalyticsFilters,
  AnalyticsServices,
  AnalyticsSession,
  AnalyticsSummary,
  AnalyticsTrend,
  AnalyticsUnbilled,
  DoctorSort,
  ServiceGroup,
  TrendGrain,
} from "@/types/revenue-analytics";
import type { ApiEnvelope } from "@/types/reception";

import {
  Card,
  DepartmentsPanel,
  DoctorsPanel,
  QualityPanel,
  ServicesPanel,
  SummaryPanel,
} from "./analytics-panels";
import TrendChart from "./trend-chart";

/**
 * FINANCE & EXECUTIVE REVENUE ANALYTICS.
 *
 * Read-only. Every figure, every share and every attribution is the server's
 * (hospital_revenue_analytics); the browser only chooses filters and renders.
 * The tabs a session sees follow its capabilities, but the SERVER gates every
 * request: an Executive asking for data quality gets a 403, not a hidden tab.
 */
type Loaded = {
  summary?: AnalyticsSummary;
  doctors?: AnalyticsDoctors;
  services?: AnalyticsServices;
  departments?: AnalyticsDepartments;
  trend?: AnalyticsTrend;
  quality?: AnalyticsDataQuality;
  unbilled?: AnalyticsUnbilled | null;
};

async function load<T>(path: string, signal: AbortSignal): Promise<{ data?: T; error?: string }> {
  try {
    const response = await fetch(path, { cache: "no-store", signal });
    const payload = (await response.json()) as ApiEnvelope<T>;
    if (!response.ok || !payload.success) {
      return { error: messageFromPayload(payload, "Unable to load revenue analytics.") };
    }
    return { data: payload.data };
  } catch {
    if (signal.aborted) return {};
    return { error: "Unable to reach the YOYA EMR gateway." };
  }
}

const BREAKDOWN_TABS: AnalyticsTab[] = ["doctors", "services", "departments"];

export default function RevenueAnalyticsWorkstation() {
  const [session, setSession] = useState<AnalyticsSession | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [filters, setFilters] = useState<AnalyticsFilters | null>(null);
  const [tab, setTab] = useState<AnalyticsTab>("summary");
  const [doctorSort, setDoctorSort] = useState<DoctorSort>("episode");
  const [serviceGroup, setServiceGroup] = useState<ServiceGroup>("category");
  const [grain, setGrain] = useState<TrendGrain>("day");
  const [loaded, setLoaded] = useState<Loaded>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // --- session --------------------------------------------------------
  useEffect(() => {
    const controller = new AbortController();
    void load<AnalyticsSession>("/api/revenue-analytics/session", controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (result.error || !result.data) {
        setSessionError(result.error ?? "Unable to load the analytics session.");
        return;
      }
      setSession(result.data);
      if (result.data.capabilities.analytics) {
        setFilters({
          date_from: result.data.default_range.date_from,
          date_to: result.data.default_range.date_to,
          company: result.data.default_company ? String(result.data.default_company) : "",
          category: "",
          basis: "recognized",
        });
      }
    });
    return () => controller.abort();
  }, []);

  const tabs = useMemo(() => visibleTabs(session?.capabilities), [session]);

  // --- data for the open tab ------------------------------------------
  useEffect(() => {
    if (!filters) return;
    const current = filters;
    const controller = new AbortController();
    const base = "/api/revenue-analytics";
    const basis = BREAKDOWN_TABS.includes(tab) ? current.basis : "recognized";
    const query = (extra: Record<string, string> = {}) =>
      analyticsQuery({ ...current, basis }, extra);

    async function run() {
      setLoading(true);
      setError(null);
      let next: Loaded = {};
      let failure: string | undefined;
      if (tab === "summary") {
        const result = await load<AnalyticsSummary>(`${base}/summary${query()}`, controller.signal);
        next = { summary: result.data };
        failure = result.error;
      } else if (tab === "doctors") {
        const result = await load<AnalyticsDoctors>(`${base}/doctors${query({ sort: doctorSort })}`, controller.signal);
        next = { doctors: result.data };
        failure = result.error;
      } else if (tab === "services") {
        const result = await load<AnalyticsServices>(`${base}/services${query({ group: serviceGroup })}`, controller.signal);
        next = { services: result.data };
        failure = result.error;
      } else if (tab === "departments") {
        const result = await load<AnalyticsDepartments>(`${base}/departments${query()}`, controller.signal);
        next = { departments: result.data };
        failure = result.error;
      } else if (tab === "trend") {
        const result = await load<AnalyticsTrend>(`${base}/trend${query({ grain: safeGrain(grain, current.date_from, current.date_to) })}`, controller.signal);
        next = { trend: result.data };
        failure = result.error;
      } else if (tab === "quality") {
        const [quality, unbilled] = await Promise.all([
          load<AnalyticsDataQuality>(`${base}/data-quality${query()}`, controller.signal),
          load<AnalyticsUnbilled>(`${base}/unbilled${query()}`, controller.signal),
        ]);
        next = { quality: quality.data, unbilled: unbilled.data ?? null };
        failure = quality.error ?? unbilled.error;
      }
      if (controller.signal.aborted) return;
      setLoaded(next);
      setError(failure ?? null);
      setLoading(false);
    }
    void run();
    return () => controller.abort();
  }, [filters, tab, doctorSort, serviceGroup, grain]);

  // --- render ---------------------------------------------------------
  if (sessionError) {
    return <Notice tone="error">{sessionError}</Notice>;
  }
  if (!session) {
    return <Notice>Loading revenue analytics…</Notice>;
  }
  if (!session.capabilities.analytics || !filters) {
    return (
      <Notice tone="error">
        Revenue analytics requires the Hospital Finance Analytics or Hospital Executive role.
        This view is not available to {session.user.name}.
      </Notice>
    );
  }

  const finance = session.capabilities.finance;
  const update = (patch: Partial<AnalyticsFilters>) => setFilters((current) => (current ? { ...current, ...patch } : current));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2 rounded-md border border-slate-200 bg-white p-2 text-xs">
        <Field label="From">
          <input type="date" value={filters.date_from} max={filters.date_to}
            onChange={(event) => event.target.value && update({ date_from: event.target.value })}
            className="rounded border border-slate-300 px-2 py-1" />
        </Field>
        <Field label="To">
          <input type="date" value={filters.date_to} min={filters.date_from}
            onChange={(event) => event.target.value && update({ date_to: event.target.value })}
            className="rounded border border-slate-300 px-2 py-1" />
        </Field>
        {session.companies.length > 1 && (
          <Field label="Company">
            <select value={filters.company} onChange={(event) => update({ company: event.target.value })}
              className="rounded border border-slate-300 px-2 py-1">
              {session.companies.map((company) => (
                <option key={company.id} value={String(company.id)}>{company.name}</option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Service category">
          <select value={filters.category} onChange={(event) => update({ category: event.target.value })}
            className="rounded border border-slate-300 px-2 py-1">
            <option value="">All categories</option>
            {session.categories.map((category) => (
              <option key={category.key} value={category.key}>{category.label}</option>
            ))}
          </select>
        </Field>
        {BREAKDOWN_TABS.includes(tab) && (
          <Field label="Basis">
            <div className="flex rounded border border-slate-300" role="group" aria-label="Basis">
              {(["recognized", "delivered"] as AnalyticsBasis[]).map((basis) => (
                <button key={basis} type="button" onClick={() => update({ basis })}
                  aria-pressed={filters.basis === basis}
                  title={basis === "recognized" ? RECOGNIZED_HELP : DELIVERED_HELP}
                  className={`px-2 py-1 font-semibold ${filters.basis === basis ? "bg-slate-900 text-white" : "text-slate-700"}`}>
                  {basis === "recognized" ? RECOGNIZED_LABEL : DELIVERED_LABEL}
                </button>
              ))}
            </div>
          </Field>
        )}
        <p className="ml-auto max-w-md text-[11px] leading-snug text-slate-500">
          {SELF_PAY_SCOPE_NOTE} Dates are {session.timezone} calendar days.
        </p>
      </div>

      <nav aria-label="Analytics views" className="flex flex-wrap gap-1">
        {tabs.map((item) => (
          <button key={item.key} type="button" onClick={() => setTab(item.key)}
            aria-current={tab === item.key ? "page" : undefined}
            className={`rounded px-3 py-1.5 text-xs font-semibold ${tab === item.key ? "bg-emerald-700 text-white" : "bg-white text-slate-700 ring-1 ring-slate-200 hover:bg-slate-50"}`}>
            {item.label}
          </button>
        ))}
        {loading && <span className="self-center pl-2 text-xs text-slate-500">Loading…</span>}
      </nav>

      {error && <Notice tone="error">{error}</Notice>}

      {tab === "summary" && loaded.summary && <SummaryPanel summary={loaded.summary} finance={finance} />}
      {tab === "doctors" && loaded.doctors && (
        <DoctorsPanel data={loaded.doctors} sort={doctorSort} onSort={setDoctorSort} />
      )}
      {tab === "services" && loaded.services && (
        <ServicesPanel data={loaded.services} group={serviceGroup} onGroup={setServiceGroup} />
      )}
      {tab === "departments" && loaded.departments && <DepartmentsPanel data={loaded.departments} />}
      {tab === "trend" && loaded.trend && (
        <Card title={`${RECOGNIZED_LABEL} vs ${DELIVERED_LABEL}`}
          help={`${RECOGNIZED_HELP} ${DELIVERED_HELP} The gap between them is the invoicing / accounting lag.`}>
          <div className="mb-2 flex gap-1 text-xs">
            {(["day", "week", "month"] as TrendGrain[]).map((option) => (
              <button key={option} type="button" onClick={() => setGrain(option)} aria-pressed={grain === option}
                className={`rounded px-2 py-1 font-semibold ${grain === option ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-700"}`}>
                {option === "day" ? "Daily" : option === "week" ? "Weekly" : "Monthly"}
              </button>
            ))}
          </div>
          <TrendChart points={loaded.trend.points} grain={loaded.trend.grain}
            currency={loaded.trend.scope?.currency ?? ""} showTable />
        </Card>
      )}
      {tab === "quality" && finance && loaded.quality && (
        <QualityPanel quality={loaded.quality} unbilled={loaded.unbilled ?? null} />
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-0.5">
      <span className="text-[10px] font-bold uppercase tracking-wide text-slate-500">{label}</span>
      {children}
    </label>
  );
}

function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "error" }) {
  return (
    <div className={`rounded-md border p-3 text-sm ${tone === "error" ? "border-rose-200 bg-rose-50 text-rose-900" : "border-slate-200 bg-white text-slate-600"}`}>
      {children}
    </div>
  );
}

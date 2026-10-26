/**
 * REVENUE ANALYTICS on /finance-analytics: wording, formatting and chart
 * geometry. Pure functions only -- every figure comes from the server.
 *
 * The vocabulary is part of the contract. Recognized Revenue, Delivered
 * Value and Delivered-Not-Yet-Invoiced are three different things, and the
 * two doctor measures must never read as one "doctor total".
 */
import type {
  AgingBucket,
  AnalyticsCapabilities,
  AnalyticsFilters,
  TrendGrain,
  TrendPoint,
} from "../types/revenue-analytics";

export const ANALYTICS_ROUTE = "/finance-analytics";

export type AnalyticsTab =
  | "summary"
  | "doctors"
  | "services"
  | "departments"
  | "trend"
  | "quality";

export const ANALYTICS_TABS: { key: AnalyticsTab; label: string; financeOnly: boolean }[] = [
  { key: "summary", label: "Executive Summary", financeOnly: false },
  { key: "doctors", label: "Doctors", financeOnly: false },
  { key: "services", label: "Services", financeOnly: false },
  { key: "departments", label: "Departments", financeOnly: false },
  { key: "trend", label: "Trend", financeOnly: false },
  { key: "quality", label: "Data Quality", financeOnly: true },
];

/** The tabs this session may open. Navigation only: the server gates data. */
export function visibleTabs(capabilities: AnalyticsCapabilities | null | undefined) {
  if (!capabilities?.analytics) return [];
  return ANALYTICS_TABS.filter((tab) => !tab.financeOnly || capabilities.data_quality);
}

// --- The measures, named once ---------------------------------------------
export const RECOGNIZED_LABEL = "Recognized Revenue";
export const RECOGNIZED_HELP =
  "Posted invoice revenue (net of credit notes), dated by the accounting date. " +
  "Self-pay only; VAT excluded; net of discounts.";
export const DELIVERED_LABEL = "Delivered Value";
export const DELIVERED_HELP =
  "Delivered, billable care at its net price, dated by when it was delivered. " +
  "Operational value - not revenue until it is invoiced and posted.";
export const UNBILLED_LABEL = "Delivered, Not Yet Invoiced";
export const UNBILLED_EXECUTIVE_LABEL = "Unbilled Delivered Value";
export const UNBILLED_HELP =
  "Care already delivered that no posted invoice carries yet. Awaiting " +
  "recognition - not revenue.";

export const DIRECT_REVENUE_LABEL = "Direct (performed)";
export const DIRECT_REVENUE_HELP =
  "Revenue from consultations the doctor personally performed. Lab, " +
  "radiology, pharmacy, bed and admission are never direct revenue.";
export const EPISODE_REVENUE_LABEL = "Episode-attributed hospital revenue";
export const EPISODE_REVENUE_HELP =
  "All hospital revenue of the episodes this doctor is responsible for " +
  "(outpatient: the visit doctor; inpatient: the admitting physician). " +
  "Hospital revenue, not revenue the doctor performed.";
export const NO_DOCTOR_TOTAL_NOTE =
  "Direct and episode-attributed revenue overlap by design and are never added together.";
export const PATIENTS_NOT_ADDITIVE_NOTE =
  "One patient can appear under several doctors or departments, so row patient " +
  "counts do not add up to the hospital total.";
export const SELF_PAY_SCOPE_NOTE =
  "Headline scope: self-pay recognized revenue. Payer / sponsored revenue is " +
  "excluded and reported separately until payer accounting is complete.";

// --- Formatting -------------------------------------------------------------
function finite(value: number | null | undefined) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function formatMoney(value: number | null | undefined) {
  return finite(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Compact money for chart axes and tiles: 1.2M, 45.3K, 980. */
export function formatCompactMoney(value: number | null | undefined) {
  const amount = finite(value);
  const sign = amount < 0 ? "-" : "";
  const absolute = Math.abs(amount);
  if (absolute >= 1_000_000) return `${sign}${(absolute / 1_000_000).toFixed(1)}M`;
  if (absolute >= 1_000) return `${sign}${(absolute / 1_000).toFixed(1)}K`;
  return `${sign}${absolute.toFixed(0)}`;
}

export function formatShare(share: number | null | undefined) {
  return `${(finite(share) * 100).toFixed(1)}%`;
}

export function formatCount(value: number | null | undefined) {
  return finite(value).toLocaleString("en-US");
}

/** Quantity, or a dash where the server withholds a meaningless sum. */
export function formatQuantity(value: number | null | undefined, uom?: string | null) {
  if (value === null || value === undefined) return "—";
  const text = finite(value).toLocaleString("en-US", { maximumFractionDigits: 3 });
  return uom ? `${text} ${uom}` : text;
}

const AGING_LABELS: Record<AgingBucket, string> = {
  "0_7": "0–7 days",
  "8_30": "8–30 days",
  "31_90": "31–90 days",
  over_90: "Over 90 days",
};

export function agingLabel(bucket: AgingBucket) {
  return AGING_LABELS[bucket] ?? bucket;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** A trend bucket (YYYY-MM-DD, the bucket start) as an axis label. */
export function bucketLabel(bucket: string, grain: TrendGrain) {
  const [year, month, day] = bucket.split("-").map((part) => Number(part));
  if (!year || !month) return bucket;
  if (grain === "month") return `${MONTHS[month - 1]} ${year}`;
  if (grain === "week") return `Wk ${day} ${MONTHS[month - 1]}`;
  return `${day} ${MONTHS[month - 1]}`;
}

/** The server's per-grain span limits (days), mirrored for the default
 *  choice only: a too-long daily range steps up to weekly, then monthly. */
export const GRAIN_MAX_DAYS: Record<TrendGrain, number> = { day: 92, week: 731, month: 1096 };

export function safeGrain(grain: TrendGrain, dateFrom: string, dateTo: string): TrendGrain {
  const span = Math.round((Date.parse(dateTo) - Date.parse(dateFrom)) / 86_400_000) + 1;
  if (!Number.isFinite(span)) return grain;
  const order: TrendGrain[] = ["day", "week", "month"];
  for (const candidate of order.slice(order.indexOf(grain))) {
    if (span <= GRAIN_MAX_DAYS[candidate]) return candidate;
  }
  return "month";
}

// --- Query ------------------------------------------------------------------
/** The only query keys the BFF forwards. Anything else is dropped. */
export const ANALYTICS_QUERY_KEYS = [
  "date_from",
  "date_to",
  "company",
  "doctor",
  "department",
  "category",
  "basis",
  "grain",
  "sort",
  "group",
] as const;

export function analyticsQuery(
  filters: Partial<AnalyticsFilters>,
  extra: Record<string, string | undefined> = {},
) {
  const query = new URLSearchParams();
  const merged: Record<string, string | undefined> = { ...filters, ...extra };
  for (const key of ANALYTICS_QUERY_KEYS) {
    const value = merged[key];
    if (value) query.set(key, value);
  }
  const text = query.toString();
  return text ? `?${text}` : "";
}

/** Keep only the forwarded keys of an incoming query string. */
export function forwardedQuery(search: string) {
  const incoming = new URLSearchParams(search);
  const extra: Record<string, string> = {};
  for (const key of ANALYTICS_QUERY_KEYS) {
    const value = incoming.get(key);
    if (value) extra[key] = value.slice(0, 64);
  }
  return analyticsQuery({}, extra);
}

// --- Trend chart geometry -----------------------------------------------------
export type TrendGeometry = {
  width: number;
  height: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
  min: number;
  max: number;
  x: (index: number) => number;
  y: (value: number) => number;
  ticks: number[];
  recognizedPath: string;
  deliveredPath: string;
};

function niceStep(span: number, steps: number) {
  const raw = span / Math.max(steps, 1);
  const power = 10 ** Math.floor(Math.log10(raw || 1));
  const scaled = raw / power;
  const nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return nice * power;
}

/**
 * One shared y-axis for both series (same unit, same scale): never a dual
 * axis. The domain always includes zero, and goes below it when a credit
 * note makes a bucket's recognized revenue negative.
 */
export function trendGeometry(
  points: TrendPoint[],
  width = 720,
  height = 240,
  margin = { left: 56, right: 92, top: 16, bottom: 28 },
): TrendGeometry {
  const values = points.flatMap((point) => [point.recognized, point.delivered]).map(finite);
  const rawMax = Math.max(0, ...values);
  const rawMin = Math.min(0, ...values);
  const step = niceStep(rawMax - rawMin || 1, 4);
  const max = Math.ceil(rawMax / step) * step || step;
  const min = Math.floor(rawMin / step) * step;
  const ticks: number[] = [];
  for (let tick = min; tick <= max + step / 2; tick += step) ticks.push(Number(tick.toFixed(6)));

  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const x = (index: number) =>
    margin.left + (points.length <= 1 ? plotWidth / 2 : (index * plotWidth) / (points.length - 1));
  const y = (value: number) =>
    margin.top + plotHeight - ((finite(value) - min) / (max - min || 1)) * plotHeight;
  const path = (key: "recognized" | "delivered") =>
    points
      .map((point, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(1)},${y(point[key]).toFixed(1)}`)
      .join(" ");

  return {
    width,
    height,
    left: margin.left,
    right: width - margin.right,
    top: margin.top,
    bottom: height - margin.bottom,
    min,
    max,
    x,
    y,
    ticks,
    recognizedPath: path("recognized"),
    deliveredPath: path("delivered"),
  };
}

/** At most `limit` evenly spaced axis labels, always including the ends. */
export function labelIndexes(count: number, limit = 8) {
  if (count <= limit) return Array.from({ length: count }, (_value, index) => index);
  const step = (count - 1) / (limit - 1);
  return Array.from({ length: limit }, (_value, index) => Math.round(index * step));
}

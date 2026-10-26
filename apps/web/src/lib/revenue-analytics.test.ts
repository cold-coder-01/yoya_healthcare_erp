/**
 * REVENUE ANALYTICS on /finance-analytics.
 *
 * Run with `npm test`. Wording, formatting, query whitelisting and chart
 * geometry are exercised directly; the BFF routes, the page and the panels
 * are held at the source (no DOM test stack exists). Sources are normalized
 * to LF before matching, so a CRLF checkout cannot fail these.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";

import {
  ANALYTICS_QUERY_KEYS,
  ANALYTICS_ROUTE,
  DELIVERED_LABEL,
  DIRECT_REVENUE_LABEL,
  EPISODE_REVENUE_LABEL,
  NO_DOCTOR_TOTAL_NOTE,
  RECOGNIZED_LABEL,
  UNBILLED_LABEL,
  analyticsQuery,
  bucketLabel,
  formatCompactMoney,
  formatMoney,
  formatQuantity,
  formatShare,
  forwardedQuery,
  labelIndexes,
  safeGrain,
  trendGeometry,
  visibleTabs,
} from "./revenue-analytics-format.ts";

function read(relative: string): string {
  return readFileSync(new URL(`../${relative}`, import.meta.url), "utf8").replace(/\r\n/g, "\n");
}

function code(relative: string): string {
  return read(relative)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const CAPS = {
  analytics: true, finance: false, executive: true, data_quality: false,
  unbilled_detail: false, patient_drilldown: false,
};
const ENDPOINTS = ["session", "summary", "trend", "doctors", "services", "departments", "unbilled", "data-quality"];

test("tabs follow capabilities: Data Quality is Finance-only, no role sees nothing", () => {
  assert.deepEqual(visibleTabs(null), []);
  assert.deepEqual(visibleTabs({ ...CAPS, analytics: false }), []);
  const executive = visibleTabs(CAPS).map((tab) => tab.key);
  assert.deepEqual(executive, ["summary", "doctors", "services", "departments", "trend"]);
  const finance = visibleTabs({ ...CAPS, finance: true, data_quality: true }).map((tab) => tab.key);
  assert.ok(finance.includes("quality"));
});

test("the measures are named apart and never as one doctor total", () => {
  assert.equal(DIRECT_REVENUE_LABEL, "Direct (performed)");
  assert.equal(EPISODE_REVENUE_LABEL, "Episode-attributed hospital revenue");
  assert.notEqual(RECOGNIZED_LABEL, DELIVERED_LABEL);
  assert.equal(UNBILLED_LABEL, "Delivered, Not Yet Invoiced");
  assert.match(NO_DOCTOR_TOTAL_NOTE, /never added together/);
});

test("formatting", () => {
  assert.equal(formatMoney(1234.5), "1,234.50");
  assert.equal(formatMoney(Number.NaN), "0.00");
  assert.equal(formatShare(0.1234), "12.3%");
  assert.equal(formatQuantity(null), "—");
  assert.equal(formatQuantity(3, "Days"), "3 Days");
  assert.equal(formatCompactMoney(1_250_000), "1.3M");
  assert.equal(formatCompactMoney(-45_300), "-45.3K");
  assert.equal(bucketLabel("2031-03-01", "month"), "Mar 2031");
  assert.equal(bucketLabel("2031-03-09", "day"), "9 Mar");
});

test("the query is whitelisted and empty values are dropped", () => {
  assert.equal(
    analyticsQuery({ date_from: "2031-03-01", date_to: "2031-03-31", company: "", category: "", basis: "recognized" }),
    "?date_from=2031-03-01&date_to=2031-03-31&basis=recognized",
  );
  assert.equal(forwardedQuery("?date_from=2031-03-01&evil=1&patient_id=7"), "?date_from=2031-03-01");
  assert.ok(!ANALYTICS_QUERY_KEYS.includes("patient_id" as never));
  assert.equal(forwardedQuery("?basis=" + "x".repeat(200)).length, "?basis=".length + 64);
});

test("a too-long daily trend steps up to the server's allowed grain", () => {
  assert.equal(safeGrain("day", "2031-03-01", "2031-03-31"), "day");
  assert.equal(safeGrain("day", "2031-01-01", "2031-12-31"), "week");
  assert.equal(safeGrain("week", "2029-01-01", "2031-12-31"), "month");
  assert.equal(safeGrain("month", "2031-03-01", "2031-03-31"), "month");
});

test("trend geometry: one shared axis that includes zero and goes negative for credits", () => {
  const positive = trendGeometry([
    { bucket: "2031-03-01", recognized: 100, delivered: 250 },
    { bucket: "2031-04-01", recognized: 300, delivered: 0 },
  ]);
  assert.equal(positive.min, 0);
  assert.ok(positive.max >= 300);
  assert.ok(positive.ticks.includes(0));
  assert.ok(positive.recognizedPath.startsWith("M"));
  assert.ok(positive.y(0) > positive.y(300), "larger values sit higher");
  const credited = trendGeometry([
    { bucket: "2031-03-01", recognized: 300, delivered: 0 },
    { bucket: "2031-04-01", recognized: -300, delivered: 0 },
  ]);
  assert.ok(credited.min <= -300);
  assert.ok(credited.ticks.includes(0));
  assert.deepEqual(labelIndexes(3), [0, 1, 2]);
  const many = labelIndexes(31, 8);
  assert.equal(many.length, 8);
  assert.equal(many[0], 0);
  assert.equal(many[7], 30);
});

test("every BFF route is a read-only GET pass-through", () => {
  const forward = code("app/api/revenue-analytics/_forward.ts");
  assert.ok(forward.includes("forwardedQuery(new URL(request.url).search)"));
  assert.ok(forward.includes('"GET"'));
  assert.ok(!forward.includes('"POST"'));
  for (const endpoint of ENDPOINTS) {
    const route = code(`app/api/revenue-analytics/${endpoint}/route.ts`);
    assert.ok(route.includes("export async function GET"), endpoint);
    assert.ok(!/export async function (POST|PUT|PATCH|DELETE)/.test(route), endpoint);
    assert.ok(route.includes(`forwardAnalytics(request, "${endpoint}"`), endpoint);
  }
});

test("the page is the workstation; the workstation never writes", () => {
  assert.ok(existsSync(new URL("../app/finance-analytics/page.tsx", import.meta.url)));
  assert.equal(ANALYTICS_ROUTE, "/finance-analytics");
  const page = code("app/finance-analytics/page.tsx");
  assert.ok(page.includes("<RevenueAnalyticsWorkstation />"));
  const desk = code("components/revenue-analytics/revenue-analytics-workstation.tsx");
  assert.ok(!desk.includes('method: "POST"'));
  assert.ok(desk.includes("/api/revenue-analytics/session"));
  assert.ok(desk.includes('tab === "quality" && finance'), "data quality renders for Finance only");
});

test("direct and episode doctor revenue are separate columns, never summed", () => {
  const panels = code("components/revenue-analytics/analytics-panels.tsx");
  assert.ok(panels.includes("{DIRECT_REVENUE_LABEL}</Th>"));
  assert.ok(panels.includes("{EPISODE_REVENUE_LABEL}</Th>"));
  assert.ok(panels.includes("row.direct_revenue"));
  assert.ok(panels.includes("row.episode_revenue"));
  assert.ok(!/direct_revenue\s*\+\s*\w*\.?episode_revenue/.test(panels));
  assert.ok(!/doctor total/i.test(panels));
});

test("no chart dependency: the trend is plain SVG", () => {
  const pkg = JSON.parse(read("../package.json")) as { dependencies: Record<string, string> };
  for (const name of Object.keys(pkg.dependencies)) {
    assert.ok(!/chart|d3|recharts|plotly|victory|nivo/i.test(name), name);
  }
  const chart = code("components/revenue-analytics/trend-chart.tsx");
  assert.ok(chart.includes("<svg"));
  assert.ok(chart.includes("strokeDasharray"), "Delivered Value carries a secondary encoding");
});

test("the Accountant Desk links to the analytics view", () => {
  const layout = code("app/accountant/layout.tsx");
  assert.ok(layout.includes("href={ANALYTICS_ROUTE}"));
});

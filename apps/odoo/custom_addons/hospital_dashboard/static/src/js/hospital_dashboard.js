/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

/**
 * Hospital Dashboard — executive command center client action.
 *
 * All data comes from a single RPC to `hospital.dashboard.get_dashboard_data`.
 * The component itself is purely presentational: SVG line chart, donuts and
 * sparklines are computed from the returned aggregates.
 */
export class HospitalDashboard extends Component {
    static template = "hospital_dashboard.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.state = useState({
            loading: true,
            failed: false,
            data: null,
            range: "today",
            departmentId: false,
        });
        onWillStart(() => this.loadData());
    }

    async loadData() {
        this.state.loading = true;
        this.state.failed = false;
        try {
            this.state.data = await this.orm.call("hospital.dashboard", "get_dashboard_data", [
                { range: this.state.range, department_id: this.state.departmentId },
            ]);
        } catch (e) {
            console.error("Hospital dashboard load failed", e);
            this.state.failed = true;
        }
        this.state.loading = false;
    }

    onRangeChange(ev) {
        this.state.range = ev.target.value;
        this.loadData();
    }

    onDepartmentChange(ev) {
        const val = parseInt(ev.target.value, 10);
        this.state.departmentId = isNaN(val) ? false : val;
        this.loadData();
    }

    openAction(action) {
        if (action) {
            this.actionService.doAction(action);
        }
    }

    get rangeLabel() {
        return {
            today: "Today",
            "7d": "Last 7 Days",
            "30d": "Last 30 Days",
            month: "This Month",
        }[this.state.range];
    }

    // ------------------------------------------------------------------
    // Trend helpers
    // ------------------------------------------------------------------

    trendClass(trend) {
        if (trend === null || trend === undefined) {
            return "o_hd_trend_flat";
        }
        return trend >= 0 ? "o_hd_trend_up" : "o_hd_trend_down";
    }

    trendIcon(trend) {
        if (trend === null || trend === undefined || trend === 0) {
            return "fa-minus";
        }
        return trend > 0 ? "fa-arrow-up" : "fa-arrow-down";
    }

    trendText(trend) {
        if (trend === null || trend === undefined) {
            return "—";
        }
        return `${Math.abs(trend)}%`;
    }

    // ------------------------------------------------------------------
    // Patient flow line chart (SVG)
    // ------------------------------------------------------------------

    get flow() {
        return (this.state.data && this.state.data.patient_flow) || null;
    }

    get flowGeom() {
        return { w: 620, h: 210, padL: 34, padR: 10, padT: 14, padB: 26 };
    }

    get flowMax() {
        const f = this.flow;
        if (!f) {
            return 5;
        }
        const max = Math.max(...f.outpatients, ...f.inpatients, 1);
        // Round the axis top up to a friendly step.
        const step = Math.pow(10, Math.floor(Math.log10(max)));
        return Math.ceil(max / step) * step;
    }

    flowXY(values, i) {
        const g = this.flowGeom;
        const n = values.length;
        const x = g.padL + (i * (g.w - g.padL - g.padR)) / Math.max(n - 1, 1);
        const y = g.padT + (1 - values[i] / this.flowMax) * (g.h - g.padT - g.padB);
        return [x, y];
    }

    flowPoints(values) {
        return values.map((v, i) => this.flowXY(values, i).join(",")).join(" ");
    }

    flowAreaPath(values) {
        const g = this.flowGeom;
        const baseline = g.h - g.padB;
        const pts = values.map((v, i) => this.flowXY(values, i));
        if (!pts.length) {
            return "";
        }
        const line = pts.map((p, i) => `${i === 0 ? "M" : "L"}${p[0]},${p[1]}`).join(" ");
        return `${line} L${pts[pts.length - 1][0]},${baseline} L${pts[0][0]},${baseline} Z`;
    }

    flowDots(values, series) {
        const f = this.flow;
        return values
            .map((v, i) => {
                const [x, y] = this.flowXY(values, i);
                return { x, y, v, label: `${f.labels[i] || ""} — ${series}: ${v}` };
            })
            .filter((d, i) => values[i] > 0 || values.length <= 14);
    }

    get flowGrid() {
        const g = this.flowGeom;
        const lines = [];
        for (let i = 0; i <= 4; i++) {
            const y = g.padT + (i * (g.h - g.padT - g.padB)) / 4;
            lines.push({
                y,
                label: Math.round(this.flowMax * (1 - i / 4)),
            });
        }
        return lines;
    }

    get flowXLabels() {
        const f = this.flow;
        if (!f) {
            return [];
        }
        const step = f.labels.length > 8 ? 2 : 1;
        return f.labels
            .map((label, i) => ({ label, x: this.flowXY(f.outpatients, i)[0] }))
            .filter((l, i) => l.label && (f.labels.length > 13 || i % step === 0));
    }

    // ------------------------------------------------------------------
    // Donut helpers (billing + beds)
    // ------------------------------------------------------------------

    donutArcs(segments) {
        const r = 70;
        const c = 2 * Math.PI * r;
        const gap = 3; // ~2px surface gap between segments
        const items = (segments || []).filter((s) => s.value > 0);
        const total = items.reduce((acc, s) => acc + s.value, 0);
        if (!total) {
            return [];
        }
        let offset = 0;
        return items.map((s) => {
            const len = (s.value / total) * c;
            const arc = {
                color: s.color,
                label: s.label,
                dasharray: `${Math.max(len - (items.length > 1 ? gap : 0), 1)} ${c}`,
                dashoffset: -offset,
                tooltip: `${s.label}: ${s.value_fmt !== undefined ? s.value_fmt : s.value}`,
            };
            offset += len;
            return arc;
        });
    }

    // ------------------------------------------------------------------
    // Sparklines
    // ------------------------------------------------------------------

    sparkPoints(values) {
        const w = 110;
        const h = 30;
        const max = Math.max(...(values || [0]), 1);
        const n = (values || []).length;
        return (values || [])
            .map((v, i) => {
                const x = 2 + (i * (w - 4)) / Math.max(n - 1, 1);
                const y = 2 + (1 - v / max) * (h - 4);
                return `${x},${y}`;
            })
            .join(" ");
    }
}

registry.category("actions").add("hospital_dashboard.dashboard", HospitalDashboard);

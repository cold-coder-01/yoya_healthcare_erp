import logging
from datetime import datetime, time, timedelta

import pytz

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class HospitalDashboard(models.AbstractModel):
    """Read-only data provider for the Hospital Dashboard client action.

    Every section is computed defensively: optional hospital modules are
    detected at runtime (``model in self.env``) and every block is wrapped
    so a failure in one section can never break the whole dashboard.

    The provider never writes anything. Aggregations run as superuser so
    every hospital role sees the same operational totals; record-level
    drill-downs (the "View ..." buttons) open normal actions where the
    user's own access rights apply.
    """

    _name = "hospital.dashboard"
    _description = "Hospital Dashboard Data Provider"

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    @api.model
    def get_dashboard_data(self, filters=None):
        filters = filters or {}
        rng = filters.get("range") or "today"
        department_id = filters.get("department_id") or False

        env = self.sudo().env
        start, end = self._range_bounds(rng)
        prev_start, prev_end = self._previous_bounds(start, end)

        data = {"meta": self._safe(self._meta, env, rng, department_id)}
        sections = {
            "kpis": lambda: self._kpis(env, start, end, prev_start, prev_end, department_id),
            "patient_flow": lambda: self._patient_flow(env, start, end, rng, department_id),
            "billing": lambda: self._billing_summary(env, start, end),
            "department_load": lambda: self._department_load(env, start, end, department_id),
            "beds": lambda: self._bed_occupancy(env),
            "clinical": lambda: self._clinical_operations(env, start, end, prev_start, prev_end),
            "alerts": lambda: self._alerts(env),
            "finance": lambda: self._finance_overview(env, start, end),
            "activity": lambda: self._recent_activity(env),
            "quick_actions": lambda: self._quick_actions(env),
        }
        for key, fn in sections.items():
            data[key] = self._safe(fn)
        return data

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except Exception:  # noqa: BLE001 - dashboard must never crash
            _logger.exception("Hospital dashboard: section failed")
            return None

    def _tz(self):
        return pytz.timezone(self.env.user.tz or "UTC")

    def _range_bounds(self, rng):
        """Return (start, end) as naive UTC datetimes for the selected range."""
        tz = self._tz()
        today = fields.Datetime.now().astimezone(pytz.utc).astimezone(tz).date()
        if rng == "7d":
            start_d = today - timedelta(days=6)
        elif rng == "30d":
            start_d = today - timedelta(days=29)
        elif rng == "month":
            start_d = today.replace(day=1)
        else:  # today
            start_d = today
        start = tz.localize(datetime.combine(start_d, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        end = tz.localize(datetime.combine(today, time.max)).astimezone(pytz.utc).replace(tzinfo=None)
        return start, end

    def _previous_bounds(self, start, end):
        span = end - start
        return start - span - timedelta(seconds=1), start - timedelta(seconds=1)

    def _local_date(self, dt):
        return fields.Datetime.context_timestamp(self, dt).date()

    def _dt_domain(self, field, start, end):
        return [(field, ">=", fields.Datetime.to_string(start)),
                (field, "<=", fields.Datetime.to_string(end))]

    def _d_domain(self, field, start, end):
        return [(field, ">=", self._local_date(start).isoformat()),
                (field, "<=", self._local_date(end).isoformat())]

    def _count(self, env, model, domain):
        if model not in env:
            return None
        return env[model].search_count(domain)

    def _trend(self, current, previous):
        if previous is None or current is None:
            return None
        if not previous:
            return 100.0 if current else 0.0
        return round((current - previous) * 100.0 / previous, 1)

    def _currency_label(self, env):
        cur = env.company.currency_id
        return cur.name or "ETB" if cur else "ETB"

    def _company_currency_domain(self, env, model):
        """Restrict accounting aggregates to the active company/currency.

        The dashboard provider runs defensively and with sudo for aggregate
        visibility, so financial totals must explicitly stay inside the user's
        selected company and company currency.
        """
        if model not in env:
            return []
        Model = env[model]
        domain = []
        if "company_id" in Model._fields:
            domain.append(("company_id", "=", env.company.id))
        if "currency_id" in Model._fields and env.company.currency_id:
            domain.append(("currency_id", "=", env.company.currency_id.id))
        return domain

    def _fmt_money(self, env, amount):
        label = self._currency_label(env)
        amount = amount or 0.0
        sign = "-" if amount < 0 else ""
        a = abs(amount)
        if a >= 1_000_000:
            num = f"{a / 1_000_000:.2f}M"
        elif a >= 10_000:
            num = f"{a / 1_000:.1f}K"
        else:
            num = f"{a:,.0f}"
        return f"{sign}{num} {label}"

    def _sum_field(self, records, field_name):
        return sum((records.mapped(field_name) if records else []) or [0.0])

    def _hospital_invoice_domain(self, env, start=None, end=None, include_credit_notes=False):
        if "account.move" not in env or "hospital_managed_invoice" not in env["account.move"]._fields:
            return None
        move_types = ["out_invoice"]
        if include_credit_notes:
            move_types.append("out_refund")
        domain = [
            ("hospital_managed_invoice", "=", True),
            ("move_type", "in", move_types),
            ("state", "=", "posted"),
        ] + self._company_currency_domain(env, "account.move")
        if start and end:
            domain += self._d_domain("date", start, end)
        return domain

    def _posted_hospital_invoice_amount(self, env, start, end):
        domain = self._hospital_invoice_domain(env, start, end, include_credit_notes=True)
        if domain is None:
            return None
        total = 0.0
        for move in env["account.move"].search(domain):
            sign = -1.0 if move.move_type == "out_refund" else 1.0
            total += sign * abs(move.amount_total_signed or move.amount_total or 0.0)
        return round(total, 2)

    def _posted_hospital_invoice_residual(self, env):
        domain = self._hospital_invoice_domain(env)
        if domain is None:
            return None
        return round(sum(env["account.move"].search(domain).mapped("amount_residual")), 2)

    def _posted_manual_receipts_amount(self, env, start, end):
        if "hospital.charge.receipt" not in env:
            return None
        Receipt = env["hospital.charge.receipt"]
        domain = [
            ("state", "=", "confirmed"),
            ("accounting_move_id", "!=", False),
            ("accounting_move_id.state", "=", "posted"),
        ] + self._dt_domain("received_at", start, end)
        if "payment_origin" in Receipt._fields:
            domain.append(("payment_origin", "=", "manual"))
        domain += self._company_currency_domain(env, "hospital.charge.receipt")
        return round(sum(Receipt.search(domain).mapped("amount")), 2)

    def _posted_patient_advances_held(self, env):
        if "hospital.billing.accounting.config" not in env or "account.move.line" not in env:
            return None
        configs = env["hospital.billing.accounting.config"].search([
            ("company_id", "=", env.company.id),
            ("active", "=", True),
            ("patient_advance_liability_account_id", "!=", False),
        ])
        accounts = configs.mapped("patient_advance_liability_account_id")
        if not accounts:
            return 0.0
        lines = env["account.move.line"].search([
            ("company_id", "=", env.company.id),
            ("move_id.state", "=", "posted"),
            ("account_id", "in", accounts.ids),
            ("amount_residual", "<", -0.005),
        ])
        return round(sum(-line.amount_residual for line in lines), 2)

    def _financial_kpi_totals(self, env, start, end):
        """Authoritative 32B financial lifecycle totals for the dashboard.

        - revenue: posted hospital customer invoices/credit notes only;
        - collected: confirmed manual receipt headers whose accounting move posted;
        - outstanding: posted hospital invoice receivable residual;
        - advances_held: unreconciled patient-advance liability residual.
        """
        revenue = self._posted_hospital_invoice_amount(env, start, end)
        collected = self._posted_manual_receipts_amount(env, start, end)
        outstanding = self._posted_hospital_invoice_residual(env)
        advances_held = self._posted_patient_advances_held(env)
        return {
            "available": revenue is not None or collected is not None or outstanding is not None or advances_held is not None,
            "revenue": revenue or 0.0,
            "collected": collected or 0.0,
            "outstanding": outstanding or 0.0,
            "advances_held": advances_held or 0.0,
        }

    def _action(self, env, xmlid, name=None, model=None, domain=None, view_mode=None):
        """Build an action descriptor; prefer the existing window action."""
        action = None
        if xmlid and env.ref(xmlid, raise_if_not_found=False):
            try:
                action = env["ir.actions.actions"]._for_xml_id(xmlid)
            except Exception:  # noqa: BLE001
                action = None
        if action is None:
            if not model or model not in env:
                return None
            action = {
                "type": "ir.actions.act_window",
                "res_model": model,
                "name": name or model,
                "view_mode": "list,form",
                "views": [[False, "list"], [False, "form"]],
                "target": "current",
            }
        if name:
            action["name"] = name
            action["display_name"] = name
        if domain is not None:
            action["domain"] = domain
        if view_mode:
            action["view_mode"] = view_mode
            action["views"] = [[False, m] for m in view_mode.split(",")]
        action.pop("context", None)
        return action

    def _daily_series(self, env, model, dt_field, extra_domain=None, days=7, is_date=False):
        """Counts per local day for the last ``days`` days (sparklines)."""
        if model not in env:
            return [0] * days
        tz = self._tz()
        today = fields.Datetime.now().astimezone(pytz.utc).astimezone(tz).date()
        start_d = today - timedelta(days=days - 1)
        if is_date:
            domain = [(dt_field, ">=", start_d.isoformat()), (dt_field, "<=", today.isoformat())]
        else:
            start = tz.localize(datetime.combine(start_d, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
            domain = self._dt_domain(dt_field, start, fields.Datetime.now() + timedelta(days=1))
        domain += extra_domain or []
        buckets = [0] * days
        for rec in env[model].search_read(domain, [dt_field], limit=4000):
            val = rec.get(dt_field)
            if not val:
                continue
            day = val if is_date and not isinstance(val, datetime) else self._local_date(val)
            idx = (day - start_d).days
            if 0 <= idx < days:
                buckets[idx] += 1
        return buckets

    # ------------------------------------------------------------------
    # Meta / header
    # ------------------------------------------------------------------

    def _meta(self, env, rng, department_id):
        now_local = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        departments = []
        if "hospital.department" in env:
            departments = [
                {"id": d["id"], "name": d["name"]}
                for d in env["hospital.department"].search_read([], ["name"], limit=50, order="name")
            ]
        return {
            "company": env.company.name,
            "currency": self._currency_label(env),
            "date_label": now_local.strftime("%A, %d %B %Y"),
            "time_label": now_local.strftime("%H:%M"),
            "range": rng,
            "department_id": department_id,
            "departments": departments,
            "branches": [c.name for c in env.user.company_ids] or [env.company.name],
        }

    # ------------------------------------------------------------------
    # KPI row
    # ------------------------------------------------------------------

    def _kpis(self, env, start, end, prev_start, prev_end, department_id):
        kpis = []

        def add(key, label, icon, color, value, prev, value_fmt=None, unit=""):
            kpis.append({
                "key": key,
                "label": label,
                "icon": icon,
                "color": color,
                "value": value if value is not None else 0,
                "value_fmt": value_fmt or (f"{value:,}" if value is not None else "0"),
                "unit": unit,
                "trend": self._trend(value, prev),
                "action": None,
            })

        appt_dom = [("state", "!=", "cancelled")]
        if department_id:
            appt_dom.append(("department_id", "=", department_id))

        # Today's / period patients: unique patients with an appointment.
        patients = prev_patients = None
        if "hospital.appointment" in env:
            groups = env["hospital.appointment"]._read_group(
                appt_dom + self._dt_domain("appointment_date", start, end), ["patient_id"], [])
            patients = len(groups)
            prev_groups = env["hospital.appointment"]._read_group(
                appt_dom + self._dt_domain("appointment_date", prev_start, prev_end), ["patient_id"], [])
            prev_patients = len(prev_groups)
        add("patients", "Today's Patients", "fa-user-plus", "green", patients, prev_patients)

        appts = self._count(env, "hospital.appointment", appt_dom + self._dt_domain("appointment_date", start, end))
        prev_appts = self._count(env, "hospital.appointment", appt_dom + self._dt_domain("appointment_date", prev_start, prev_end))
        kpis.append({
            "key": "appointments", "label": "Appointments", "icon": "fa-calendar-check-o",
            "color": "teal", "value": appts or 0, "value_fmt": f"{appts or 0:,}", "unit": "",
            "trend": self._trend(appts, prev_appts),
            "action": self._action(env, "hospital_management.action_hospital_appointment",
                                   "Appointments", "hospital.appointment"),
        })

        adm = self._count(env, "hospital.admission",
                          [("state", "!=", "cancelled")] + self._dt_domain("admission_date", start, end))
        prev_adm = self._count(env, "hospital.admission",
                               [("state", "!=", "cancelled")] + self._dt_domain("admission_date", prev_start, prev_end))
        add("admissions", "Admissions", "fa-bed", "blue", adm, prev_adm)

        lab = self._count(env, "hospital.laboratory.request",
                          [("state", "!=", "cancelled")] + self._d_domain("request_date", start, end))
        prev_lab = self._count(env, "hospital.laboratory.request",
                               [("state", "!=", "cancelled")] + self._d_domain("request_date", prev_start, prev_end))
        add("laboratory", "Laboratory Requests", "fa-flask", "purple", lab, prev_lab)

        pharm = self._count(env, "hospital.pharmacy.dispense",
                            [("state", "!=", "cancelled")] + self._dt_domain("dispense_date", start, end))
        prev_pharm = self._count(env, "hospital.pharmacy.dispense",
                                 [("state", "!=", "cancelled")] + self._dt_domain("dispense_date", prev_start, prev_end))
        add("pharmacy", "Pharmacy Dispenses", "fa-medkit", "amber", pharm, prev_pharm)

        financial = self._financial_kpi_totals(env, start, end)
        prev_financial = self._financial_kpi_totals(env, prev_start, prev_end)
        revenue = financial["revenue"]
        prev_revenue = prev_financial["revenue"]
        add("revenue", "Revenue Today", "fa-money", "emerald", round(revenue, 2), prev_revenue,
            value_fmt=self._fmt_money(env, revenue))
        add("collected", "Collected Today", "fa-credit-card", "teal", round(financial["collected"], 2), prev_financial["collected"],
            value_fmt=self._fmt_money(env, financial["collected"]))
        add("outstanding", "Outstanding", "fa-balance-scale", "amber", round(financial["outstanding"], 2), None,
            value_fmt=self._fmt_money(env, financial["outstanding"]))
        add("advances_held", "Patient Advances Held", "fa-lock", "blue", round(financial["advances_held"], 2), None,
            value_fmt=self._fmt_money(env, financial["advances_held"]))
        return kpis

    # ------------------------------------------------------------------
    # Patient flow trend
    # ------------------------------------------------------------------

    def _patient_flow(self, env, start, end, rng, department_id):
        tz = self._tz()
        start_local = pytz.utc.localize(start).astimezone(tz)
        end_local = pytz.utc.localize(end).astimezone(tz)

        if rng == "today":
            # 12 two-hour buckets.
            n = 12
            labels = [f"{h:02d}:00" for h in range(0, 24, 2)]

            def bucket(dt_local):
                return min(dt_local.hour // 2, n - 1)
        else:
            days = (end_local.date() - start_local.date()).days + 1
            n = days
            labels = [(start_local.date() + timedelta(days=i)).strftime("%d %b") for i in range(n)]
            if n > 14:
                labels = [lbl if i % max(1, n // 7) == 0 else "" for i, lbl in enumerate(labels)]

            def bucket(dt_local):
                return min((dt_local.date() - start_local.date()).days, n - 1)

        outpatients = [0] * n
        inpatients = [0] * n

        if "hospital.appointment" in env:
            dom = [("state", "!=", "cancelled")] + self._dt_domain("appointment_date", start, end)
            if department_id:
                dom.append(("department_id", "=", department_id))
            for rec in env["hospital.appointment"].search_read(dom, ["appointment_date"], limit=4000):
                if rec["appointment_date"]:
                    outpatients[bucket(fields.Datetime.context_timestamp(self, rec["appointment_date"]))] += 1

        if "hospital.admission" in env:
            dom = [("state", "!=", "cancelled")] + self._dt_domain("admission_date", start, end)
            for rec in env["hospital.admission"].search_read(dom, ["admission_date"], limit=4000):
                if rec["admission_date"]:
                    inpatients[bucket(fields.Datetime.context_timestamp(self, rec["admission_date"]))] += 1

        return {
            "labels": labels,
            "outpatients": outpatients,
            "inpatients": inpatients,
            "totals": {
                "outpatients": sum(outpatients),
                "inpatients": sum(inpatients),
                "total": sum(outpatients) + sum(inpatients),
            },
        }

    # ------------------------------------------------------------------
    # Billing & collection
    # ------------------------------------------------------------------

    def _billing_summary(self, env, start, end):
        financial = self._financial_kpi_totals(env, start, end)
        if financial["available"]:
            billed = financial["revenue"]
            collected = financial["collected"]
            pending = financial["outstanding"]
            advances = financial["advances_held"]
            total_ref = max(billed + pending + advances, collected + pending + advances)
            rate = round(collected * 100.0 / billed, 1) if billed else 0.0

            def seg(label, value, color):
                pct = round(value * 100.0 / total_ref, 1) if total_ref else 0.0
                return {"label": label, "value": value, "value_fmt": self._fmt_money(env, value),
                        "pct": pct, "color": color}

            prev_start, prev_end = self._previous_bounds(start, end)
            prev = self._financial_kpi_totals(env, prev_start, prev_end)
            prev_rate = round(prev["collected"] * 100.0 / prev["revenue"], 1) if prev["revenue"] else 0.0
            return {
                "available": True,
                "billed": billed,
                "billed_fmt": self._fmt_money(env, billed),
                "collection_rate": rate,
                "rate_delta": round(rate - prev_rate, 1),
                "segments": [
                    seg("Collected Today", collected, "#047857"),
                    seg("Outstanding", pending, "#F59E0B"),
                    seg("Advances Held", advances, "#1D4ED8"),
                ],
                "action": self._action(env, "account.action_move_out_invoice_type",
                                       "Hospital Invoices", "account.move",
                                       self._hospital_invoice_domain(env) or []),
            }

        if "hospital.patient.bill" not in env:
            return {"available": False}
        Bill = env["hospital.patient.bill"]
        dom = [("state", "!=", "cancelled")] + self._d_domain("bill_date", start, end)
        groups = Bill._read_group(dom, [], ["amount_total:sum", "amount_paid:sum", "amount_due:sum"])
        billed, collected, pending = (groups[0] if groups else (0.0, 0.0, 0.0))
        billed, collected, pending = billed or 0.0, collected or 0.0, pending or 0.0

        cancel_groups = Bill._read_group(
            [("state", "=", "cancelled")] + self._d_domain("bill_date", start, end), [], ["amount_total:sum"])
        cancelled = (cancel_groups[0][0] if cancel_groups and cancel_groups[0] else 0.0) or 0.0

        total_ref = billed + cancelled
        rate = round(collected * 100.0 / billed, 1) if billed else 0.0

        def seg(label, value, color):
            pct = round(value * 100.0 / total_ref, 1) if total_ref else 0.0
            return {"label": label, "value": value, "value_fmt": self._fmt_money(env, value),
                    "pct": pct, "color": color}

        # Yesterday's collection rate for comparison.
        prev_start, prev_end = self._previous_bounds(start, end)
        prev_groups = Bill._read_group(
            [("state", "!=", "cancelled")] + self._d_domain("bill_date", prev_start, prev_end),
            [], ["amount_total:sum", "amount_paid:sum"])
        p_billed, p_collected = (prev_groups[0] if prev_groups else (0.0, 0.0))
        prev_rate = round((p_collected or 0.0) * 100.0 / p_billed, 1) if p_billed else 0.0

        return {
            "available": True,
            "billed": billed,
            "billed_fmt": self._fmt_money(env, billed),
            "collection_rate": rate,
            "rate_delta": round(rate - prev_rate, 1),
            "segments": [
                seg("Collected", collected, "#047857"),
                seg("Pending", pending, "#F59E0B"),
                seg("Cancelled / Write-off", cancelled, "#B91C1C"),
            ],
            "action": self._action(env, "hospital_billing.action_hospital_patient_bill",
                                   "Patient Bills", "hospital.patient.bill"),
        }

    # ------------------------------------------------------------------
    # Department load
    # ------------------------------------------------------------------

    def _department_load(self, env, start, end, department_id):
        """Relative activity per unit: today's record counts, scaled to the
        busiest unit (=100%). This is an activity share, not a staffing
        capacity figure."""
        rows = []
        if "hospital.appointment" in env and "hospital.department" in env:
            dom = [("state", "!=", "cancelled"), ("department_id", "!=", False)]
            dom += self._dt_domain("appointment_date", start, end)
            groups = env["hospital.appointment"]._read_group(dom, ["department_id"], ["__count"])
            for dept, count in groups:
                rows.append({"name": dept.name, "count": count, "dept_id": dept.id})
            rows.sort(key=lambda r: -r["count"])
            rows = rows[:4]

        service_counts = [
            ("Laboratory", self._count(env, "hospital.laboratory.request",
                                       [("state", "!=", "cancelled")] + self._d_domain("request_date", start, end))),
            ("Radiology", self._count(env, "hospital.radiology.request",
                                      [("state", "!=", "cancelled")] + self._d_domain("request_date", start, end))),
            ("Pharmacy", self._count(env, "hospital.pharmacy.dispense",
                                     [("state", "!=", "cancelled")] + self._dt_domain("dispense_date", start, end))),
        ]
        for name, count in service_counts:
            if count is not None:
                rows.append({"name": name, "count": count, "dept_id": False})

        max_count = max((r["count"] for r in rows), default=0)
        colors = ["#047857", "#0E7490", "#F59E0B", "#1D4ED8", "#7C3AED", "#475569", "#B91C1C"]
        for i, row in enumerate(rows):
            row["pct"] = round(row["count"] * 100.0 / max_count, 0) if max_count else 0
            row["color"] = colors[i % len(colors)]
            row["highlight"] = bool(department_id and row.get("dept_id") == department_id)
        return {"rows": rows, "note": "Activity share of the busiest unit in the selected period."}

    # ------------------------------------------------------------------
    # Bed occupancy
    # ------------------------------------------------------------------

    def _bed_occupancy(self, env):
        if "hospital.bed" not in env:
            return {"available": False}
        groups = env["hospital.bed"]._read_group([], ["state"], ["__count"])
        by_state = {state: count for state, count in groups}
        occupied = by_state.get("occupied", 0)
        available = by_state.get("available", 0)
        other = sum(v for k, v in by_state.items() if k not in ("occupied", "available"))
        total = occupied + available + other
        rate = round(occupied * 100.0 / total, 0) if total else 0
        return {
            "available": True,
            "occupied": occupied,
            "free": available,
            "other": other,
            "total": total,
            "rate": rate,
            "segments": [
                {"label": "Occupied", "value": occupied, "color": "#047857"},
                {"label": "Available", "value": available, "color": "#6EE7B7"},
                {"label": "Cleaning / Blocked", "value": other, "color": "#D1D5DB"},
            ],
            "action": self._action(env, "hospital_admission.action_hospital_bed", "Beds", "hospital.bed"),
        }

    # ------------------------------------------------------------------
    # Clinical operations
    # ------------------------------------------------------------------

    def _clinical_operations(self, env, start, end, prev_start, prev_end):
        cards = []

        def add(key, label, icon, color, model, count_dom, prev_dom, spark_field,
                unit, xmlid, is_date=False, spark_dom=None):
            if model not in env:
                return
            count = self._count(env, model, count_dom)
            prev = self._count(env, model, prev_dom)
            cards.append({
                "key": key,
                "label": label,
                "icon": icon,
                "color": color,
                "count": count or 0,
                "unit": unit,
                "trend": self._trend(count, prev),
                "spark": self._daily_series(env, model, spark_field,
                                            extra_domain=spark_dom or [("state", "!=", "cancelled")],
                                            is_date=is_date),
                "action": self._action(env, xmlid, label, model),
            })

        not_cancelled = [("state", "!=", "cancelled")]
        add("laboratory", "Laboratory", "fa-flask", "#7C3AED", "hospital.laboratory.request",
            not_cancelled + self._d_domain("request_date", start, end),
            not_cancelled + self._d_domain("request_date", prev_start, prev_end),
            "request_date", "Requests", "hospital_management.action_hospital_laboratory_request", is_date=True)
        add("radiology", "Radiology", "fa-dot-circle-o", "#1D4ED8", "hospital.radiology.request",
            not_cancelled + self._d_domain("request_date", start, end),
            not_cancelled + self._d_domain("request_date", prev_start, prev_end),
            "request_date", "Exams", "hospital_radiology.action_hospital_radiology_request", is_date=True)
        add("procedures", "Procedures", "fa-stethoscope", "#0E7490", "hospital.procedure.request",
            not_cancelled + self._dt_domain("request_datetime", start, end),
            not_cancelled + self._dt_domain("request_datetime", prev_start, prev_end),
            "request_datetime", "Today", "hospital_procedure.action_hospital_procedure_request")
        add("theatre", "Operation Theatre", "fa-heartbeat", "#047857", "hospital.surgery.request",
            [("state", "in", ["requested", "scheduled", "preop_ready", "in_theatre"])],
            [("state", "in", ["requested", "scheduled", "preop_ready", "in_theatre"])],
            "request_date", "Scheduled", "hospital_operation_theatre.action_hospital_surgery_request")
        # Nursing tasks: rounds + medication administrations in the period.
        if "hospital.nursing.round" in env:
            rounds = self._count(env, "hospital.nursing.round",
                                 not_cancelled + self._dt_domain("round_datetime", start, end)) or 0
            meds = self._count(env, "hospital.medication.administration",
                               self._dt_domain("scheduled_datetime", start, end)) or 0
            prev_rounds = self._count(env, "hospital.nursing.round",
                                      not_cancelled + self._dt_domain("round_datetime", prev_start, prev_end)) or 0
            prev_meds = self._count(env, "hospital.medication.administration",
                                    self._dt_domain("scheduled_datetime", prev_start, prev_end)) or 0
            cards.append({
                "key": "nursing", "label": "Nursing", "icon": "fa-user-md", "color": "#B91C1C",
                "count": rounds + meds, "unit": "Tasks",
                "trend": self._trend(rounds + meds, prev_rounds + prev_meds),
                "spark": self._daily_series(env, "hospital.nursing.round", "round_datetime"),
                "action": self._action(env, "hospital_nursing.action_hospital_nursing_round",
                                       "Nursing Rounds", "hospital.nursing.round"),
            })
        add("pharmacy", "Pharmacy", "fa-medkit", "#F59E0B", "hospital.pharmacy.dispense",
            not_cancelled + self._dt_domain("dispense_date", start, end),
            not_cancelled + self._dt_domain("dispense_date", prev_start, prev_end),
            "dispense_date", "Dispenses", "hospital_pharmacy.action_hospital_pharmacy_dispense")
        return cards

    # ------------------------------------------------------------------
    # Alerts & tasks
    # ------------------------------------------------------------------

    def _alerts(self, env):
        critical = []
        approvals = []
        low_stock = []
        now = fields.Datetime.now()

        def crit(label, detail, icon, action=None):
            critical.append({"label": label, "detail": detail, "icon": icon, "action": action})

        # ICU bed pressure.
        if "hospital.bed" in env:
            icu_total = env["hospital.bed"].search_count([("bed_type", "=", "icu")])
            icu_occ = env["hospital.bed"].search_count([("bed_type", "=", "icu"), ("state", "=", "occupied")])
            if icu_total and icu_occ * 100.0 / icu_total >= 85:
                crit("ICU bed occupancy above 85%", f"{icu_occ}/{icu_total} ICU beds occupied", "fa-bed",
                     self._action(env, "hospital_admission.action_hospital_bed", "ICU Beds",
                                  "hospital.bed", [("bed_type", "=", "icu")]))

        # Fiscal POS failures / exceptions.
        if "hospital.fiscal.transaction" in env:
            dom = [("state", "in", ["failed", "exception"])]
            n = env["hospital.fiscal.transaction"].search_count(dom)
            if n:
                crit("Fiscal POS transactions need review", f"{n} failed / exception transaction(s)",
                     "fa-exclamation-triangle",
                     self._action(env, "hospital_fiscal_bridge.action_hospital_fiscal_transaction",
                                  "Fiscal Exceptions", "hospital.fiscal.transaction", dom))

        # Out-of-stock items.
        if "hospital.inventory.item" in env:
            dom = [("total_available_quantity", "<=", 0)]
            n = env["hospital.inventory.item"].search_count(dom)
            if n:
                crit("Items out of stock", f"{n} inventory item(s) with zero stock", "fa-cube",
                     self._action(env, "hospital_inventory.action_hospital_inventory_item",
                                  "Out of Stock", "hospital.inventory.item", dom))

        # Overdue discharges.
        if "hospital.admission" in env:
            dom = [("state", "=", "admitted"),
                   ("expected_discharge_date", "!=", False),
                   ("expected_discharge_date", "<", fields.Datetime.to_string(now))]
            n = env["hospital.admission"].search_count(dom)
            if n:
                crit("Admissions past expected discharge", f"{n} patient(s) past planned discharge date", "fa-clock-o",
                     self._action(env, "hospital_admission.action_hospital_admission",
                                  "Overdue Discharges", "hospital.admission", dom))

        # Expiring stock.
        if "hospital.inventory.batch" in env:
            soon = (self._local_date(now) + timedelta(days=30)).isoformat()
            dom = [("state", "=", "available"), ("quantity_on_hand", ">", 0),
                   ("expiry_date", "!=", False), ("expiry_date", "<=", soon)]
            n = env["hospital.inventory.batch"].search_count(dom)
            if n:
                crit("Stock batches expiring within 30 days", f"{n} batch(es) close to expiry", "fa-hourglass-half",
                     self._action(env, "hospital_inventory.action_hospital_inventory_batch",
                                  "Expiring Batches", "hospital.inventory.batch", dom))

        def approval(label, model, domain, xmlid, icon):
            if model not in env:
                return
            n = env[model].search_count(domain)
            if n:
                approvals.append({
                    "label": label, "count": n, "icon": icon,
                    "action": self._action(env, xmlid, label, model, domain),
                })

        approval("Insurance claims to process", "hospital.insurance.claim",
                 [("state", "in", ["submitted", "under_review"])],
                 "hospital_insurance.action_hospital_insurance_claim", "fa-shield")
        approval("Draft bills to confirm", "hospital.patient.bill",
                 [("state", "=", "draft")],
                 "hospital_billing.action_hospital_patient_bill", "fa-file-text-o")
        approval("Surgeries awaiting scheduling", "hospital.surgery.request",
                 [("state", "=", "requested")],
                 "hospital_operation_theatre.action_hospital_surgery_request", "fa-heartbeat")
        approval("Stock requests to approve", "hospital.stock.request",
                 [("state", "in", ["draft", "submitted", "requested"])],
                 "hospital_inventory.action_hospital_stock_request", "fa-cubes")

        if "hospital.inventory.item" in env:
            for item in env["hospital.inventory.item"].search_read(
                    [("is_low_stock", "=", True)],
                    ["name", "code", "total_available_quantity", "reorder_level"],
                    limit=4, order="total_available_quantity asc"):
                low_stock.append({
                    "name": item["name"],
                    "qty": round(item["total_available_quantity"] or 0, 1),
                })
            low_stock_action = self._action(
                env, "hospital_inventory.action_hospital_inventory_item",
                "Low Stock Items", "hospital.inventory.item", [("is_low_stock", "=", True)])
            low_stock_count = env["hospital.inventory.item"].search_count([("is_low_stock", "=", True)])
        else:
            low_stock_action = None
            low_stock_count = 0

        return {
            "critical": critical,
            "approvals": approvals,
            "low_stock": low_stock,
            "low_stock_count": low_stock_count,
            "low_stock_action": low_stock_action,
        }

    # ------------------------------------------------------------------
    # Financial & administrative overview
    # ------------------------------------------------------------------

    def _finance_overview(self, env, start, end):
        overview = {}

        # Insurance claims (all open history, not range-bound).
        if "hospital.insurance.claim" in env:
            Claim = env["hospital.insurance.claim"]
            overview["insurance"] = {
                "available": True,
                "submitted": Claim.search_count([("state", "in", ["submitted", "under_review"])]),
                "approved": Claim.search_count([("state", "in", ["approved", "partially_approved", "paid"])]),
                "rejected": Claim.search_count([("state", "=", "rejected")]),
                "pending": Claim.search_count([("state", "=", "draft")]),
                "action": self._action(env, "hospital_insurance.action_hospital_insurance_claim",
                                       "Insurance Claims", "hospital.insurance.claim"),
            }
        else:
            overview["insurance"] = {"available": False}

        # Outstanding hospital invoices from the new encounter-billing engine.
        invoice_domain = self._hospital_invoice_domain(env)
        if invoice_domain is not None:
            Move = env["account.move"]
            dom = invoice_domain + [("amount_residual", ">", 0.005)]
            total_due = sum(Move.search(dom).mapped("amount_residual"))
            patients = len(Move._read_group(dom + [("hospital_patient_id", "!=", False)], ["hospital_patient_id"], [])) if "hospital_patient_id" in Move._fields else 0
            overdue_cutoff = (self._local_date(fields.Datetime.now()) - timedelta(days=30)).isoformat()
            overdue = sum(Move.search(dom + [("date", "<", overdue_cutoff)]).mapped("amount_residual"))
            overview["unpaid"] = {
                "available": True,
                "total_fmt": self._fmt_money(env, total_due),
                "patients": patients,
                "overdue_fmt": self._fmt_money(env, overdue),
                "action": self._action(env, "account.action_move_out_invoice_type",
                                       "Outstanding Hospital Invoices", "account.move", dom),
            }
        elif "hospital.patient.bill" in env:
            Bill = env["hospital.patient.bill"]
            dom = [("state", "in", ["confirmed", "partially_paid"])]
            groups = Bill._read_group(dom, [], ["amount_due:sum"])
            total_due = (groups[0][0] if groups and groups[0] else 0.0) or 0.0
            patients = len(Bill._read_group(dom, ["patient_id"], []))
            overdue_cutoff = (self._local_date(fields.Datetime.now()) - timedelta(days=30)).isoformat()
            overdue_groups = Bill._read_group(dom + [("bill_date", "<", overdue_cutoff)], [], ["amount_due:sum"])
            overdue = (overdue_groups[0][0] if overdue_groups and overdue_groups[0] else 0.0) or 0.0
            overview["unpaid"] = {
                "available": True,
                "total_fmt": self._fmt_money(env, total_due),
                "patients": patients,
                "overdue_fmt": self._fmt_money(env, overdue),
                "action": self._action(env, "hospital_billing.action_hospital_patient_bill",
                                       "Unpaid Bills", "hospital.patient.bill", dom),
            }
        else:
            overview["unpaid"] = {"available": False}

        # Fiscal POS transactions in period.
        if "hospital.fiscal.transaction" in env:
            Fiscal = env["hospital.fiscal.transaction"]
            dom = self._dt_domain("create_date", start, end)
            total = Fiscal.search_count(dom)
            success = Fiscal.search_count(dom + [("state", "=", "paid")])
            failed = Fiscal.search_count(dom + [("state", "in", ["failed", "exception", "expired"])])
            rate = round(success * 100.0 / total, 1) if total else 0.0
            overview["fiscal"] = {
                "available": True,
                "total": total,
                "success": success,
                "failed": failed,
                "rate": rate,
                "action": self._action(env, "hospital_fiscal_bridge.action_hospital_fiscal_transaction",
                                       "Fiscal Transactions", "hospital.fiscal.transaction"),
            }
        else:
            overview["fiscal"] = {"available": False}

        # Inventory alerts.
        if "hospital.inventory.item" in env:
            Item = env["hospital.inventory.item"]
            out_of_stock = Item.search_count([("total_available_quantity", "<=", 0)])
            low = Item.search_count([("is_low_stock", "=", True), ("total_available_quantity", ">", 0)])
            expiring = 0
            if "hospital.inventory.batch" in env:
                soon = (self._local_date(fields.Datetime.now()) + timedelta(days=30)).isoformat()
                expiring = env["hospital.inventory.batch"].search_count(
                    [("state", "=", "available"), ("quantity_on_hand", ">", 0),
                     ("expiry_date", "!=", False), ("expiry_date", "<=", soon)])
            overview["inventory"] = {
                "available": True,
                "out_of_stock": out_of_stock,
                "low_stock": low,
                "expiring": expiring,
                "action": self._action(env, "hospital_inventory.action_hospital_inventory_item",
                                       "Inventory Items", "hospital.inventory.item"),
            }
        else:
            overview["inventory"] = {"available": False}

        return overview

    # ------------------------------------------------------------------
    # Recent activity
    # ------------------------------------------------------------------

    def _recent_activity(self, env):
        entries = []

        def fmt_when(dt):
            if not dt:
                return ""
            local = fields.Datetime.context_timestamp(self, dt)
            today = fields.Datetime.context_timestamp(self, fields.Datetime.now()).date()
            return local.strftime("%H:%M") if local.date() == today else local.strftime("%d %b %H:%M")

        def collect(model, dt_field, build, domain=None, limit=6):
            if model not in env:
                return
            try:
                records = env[model].search(domain or [], order=f"{dt_field} desc, id desc", limit=limit)
                for rec in records:
                    when = rec[dt_field]
                    if not when:
                        continue
                    if not isinstance(when, datetime):
                        when = datetime.combine(when, time.min)
                    entry = build(rec)
                    entry["_sort"] = when
                    entry["time"] = fmt_when(when)
                    entries.append(entry)
            except Exception:  # noqa: BLE001
                _logger.exception("Hospital dashboard: activity source %s failed", model)

        collect("hospital.charge.receipt", "received_at", lambda r: {
            "activity": "Patient Advance Received",
            "reference": f"{r.name} - {r.patient_id.name or ''}".strip(" -"),
            "department": "Billing",
            "user": r.received_by_id.name or "",
            "status": "Accounted" if r.accounting_move_id and r.accounting_move_id.state == "posted" else dict(r._fields["state"].selection).get(r.state, r.state),
            "status_type": "success" if r.state == "confirmed" and r.accounting_move_id and r.accounting_move_id.state == "posted" else "info",
        }, domain=[("state", "=", "confirmed")])
        collect("hospital.patient.bill.payment", "payment_date", lambda r: {
            "activity": "Legacy Payment Received",
            "reference": f"{r.name} - {r.bill_id.patient_id.name or ''}".strip(" -"),
            "department": "Billing",
            "user": r.cashier_id.name or "",
            "status": "Completed", "status_type": "success",
        })
        collect("hospital.admission", "admission_date", lambda r: {
            "activity": "Patient Admitted",
            "reference": f"{r.name} - {r.patient_id.name or ''}".strip(" -"),
            "department": r.ward_id.name or "Admission",
            "user": r.create_uid.name or "",
            "status": dict(r._fields["state"].selection).get(r.state, r.state),
            "status_type": "success" if r.state in ("admitted", "discharged") else "info",
        }, domain=[("state", "!=", "cancelled")])
        collect("hospital.laboratory.result", "create_date", lambda r: {
            "activity": "Laboratory Result Issued",
            "reference": r.name or "",
            "department": "Laboratory",
            "user": r.create_uid.name or "",
            "status": "Completed", "status_type": "success",
        })
        collect("hospital.appointment", "create_date", lambda r: {
            "activity": "Appointment Registered",
            "reference": f"{r.appointment_code or ''} - {r.patient_id.name or ''}".strip(" -"),
            "department": r.department_id.name or "Outpatient Dept.",
            "user": r.create_uid.name or "",
            "status": dict(r._fields["state"].selection).get(r.state, r.state),
            "status_type": "success" if r.state == "done" else "info",
        }, domain=[("state", "!=", "cancelled")])
        collect("hospital.pharmacy.dispense", "dispense_date", lambda r: {
            "activity": "Pharmacy Dispense",
            "reference": f"{r.name or ''} - {r.patient_id.name or ''}".strip(" -"),
            "department": "Pharmacy",
            "user": r.create_uid.name or "",
            "status": dict(r._fields["state"].selection).get(r.state, r.state),
            "status_type": "success" if r.state == "dispensed" else "info",
        }, domain=[("state", "!=", "cancelled")])
        collect("hospital.fiscal.transaction", "fiscal_paid_at", lambda r: {
            "activity": "Payment Received (POS)",
            "reference": r.name or "",
            "department": "Billing",
            "user": r.write_uid.name or "",
            "status": "Completed", "status_type": "success",
        }, domain=[("state", "=", "paid")])

        entries.sort(key=lambda e: e["_sort"], reverse=True)
        for e in entries:
            e.pop("_sort", None)
        return entries[:9]

    # ------------------------------------------------------------------
    # Quick actions
    # ------------------------------------------------------------------

    def _quick_actions(self, env):
        specs = [
            ("New Patient", "fa-user-plus", "hospital_management.action_hospital_patient",
             "hospital.patient", "form"),
            ("New Appointment", "fa-calendar-plus-o", "hospital_management.action_hospital_appointment",
             "hospital.appointment", "form"),
            ("New Admission", "fa-bed", "hospital_admission.action_hospital_admission",
             "hospital.admission", "form"),
            ("New Bill", "fa-file-text-o", "hospital_billing.action_hospital_patient_bill",
             "hospital.patient.bill", "form"),
            ("New Claim", "fa-shield", "hospital_insurance.action_hospital_insurance_claim",
             "hospital.insurance.claim", "form"),
            ("Fiscal Transactions", "fa-calculator", "hospital_fiscal_bridge.action_hospital_fiscal_transaction",
             "hospital.fiscal.transaction", None),
            ("Inventory", "fa-cubes", "hospital_inventory.action_hospital_inventory_item",
             "hospital.inventory.item", None),
        ]
        actions = []
        for label, icon, xmlid, model, mode in specs:
            if model not in env:
                continue
            action = self._action(env, xmlid, label, model)
            if not action:
                continue
            if mode == "form":
                action["views"] = [[False, "form"]]
                action["view_mode"] = "form"
                action.pop("res_id", None)
            actions.append({"label": label, "icon": icon, "action": action})
        return actions

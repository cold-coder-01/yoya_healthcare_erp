"""The revenue analytics service: the ONLY place a figure is computed.

Every public method
  1. checks the caller's REAL group membership (never a context flag),
  2. parses and bounds the filters (company within the caller's allowed
     companies, a date range of at most MAX_SPAN_DAYS),
  3. aggregates the read-only facts with explicit company and date scope.

Aggregation runs on the SQL views with the cursor, i.e. without record rules.
That is safe ONLY because of steps 1 and 2: the group check comes first and
the company is always one the caller may use. hospital.charge.line itself has
no company record rule, so nothing here may ever drop the company predicate.

THE MEASURES, NEVER NETTED INTO EACH OTHER
-----------------------------------------
    recognized revenue   posted invoice provenance, dated by account.move.date
                         gross (invoices) - credits (credit notes) = net
    delivered value      delivered billable care at net unit price, dated by
                         when it was delivered (local date). NOT revenue.
    unbilled delivered   delivered value not yet carried by a POSTED invoice,
                         as of date_to. NOT revenue.
    cash received        confirmed receipts. NOT revenue.
    advance refunds      posted inpatient refund entries. Never reduce revenue.

V1 headline scope is SELF-PAY. Sponsored revenue is counted separately and
never mixed into a headline, ranking or share.

LOCAL DATES. Datetimes are stored in UTC; every datetime is converted to the
analytics time zone (Africa/Addis_Ababa by default) BEFORE it is cast to a
date, so care delivered at 01:30 EAT belongs to that EAT day, not to the UTC
day before it. account.move.date is already a date and is used as is.
"""
from datetime import date, datetime, timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError

from .revenue_fact import CATEGORY_SELECTION, EXCLUDED_CATEGORIES

G_FINANCE = "hospital_revenue_analytics.group_hospital_revenue_analytics"
G_EXECUTIVE = "hospital_revenue_analytics.group_hospital_executive"

TZ_PARAM = "hospital_revenue_analytics.timezone"
DEFAULT_TZ = "Africa/Addis_Ababa"

MAX_SPAN_DAYS = 1096
GRAIN_MAX_DAYS = {"day": 92, "week": 731, "month": 1096}
QTY_TOLERANCE = 1e-6

REPORT_CATEGORIES = tuple(key for key, _label in CATEGORY_SELECTION if key not in EXCLUDED_CATEGORIES)
CATEGORY_LABELS = dict(CATEGORY_SELECTION)
# Categories whose quantities share one unit across services. Pharmacy mixes
# tablets, bottles and vials; "other" mixes anything -- their category
# quantity would be a meaningless sum, so it is not reported.
HOMOGENEOUS_QUANTITY_CATEGORIES = (
    "consultation", "laboratory", "radiology", "admission_fee", "bed_stay", "procedure",
)
AGING_BUCKETS = (("0_7", 0, 7), ("8_30", 8, 30), ("31_90", 31, 90), ("over_90", 91, None))
LEGACY_ACCOUNTING_SCAN_LIMIT = 300

LEVEL_FINANCE = "finance"
LEVEL_EXECUTIVE = "executive"


class RevenueAnalyticsError(Exception):
    """A refused request: a stable code, an operator sentence, an HTTP status."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class HospitalRevenueAnalytics(models.AbstractModel):
    _name = "hospital.revenue.analytics"
    _description = "Hospital Revenue Analytics Service"

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------
    @api.model
    def access_level(self):
        """'finance', 'executive' or None for the REAL user. Finance is the
        wider of the two, so a user holding both is Finance."""
        user = self.env.user
        if user.has_group(G_FINANCE):
            return LEVEL_FINANCE
        if user.has_group(G_EXECUTIVE):
            return LEVEL_EXECUTIVE
        return None

    @api.model
    def _require(self, level):
        current = self.access_level()
        allowed = (
            current == LEVEL_FINANCE
            if level == LEVEL_FINANCE
            else current in (LEVEL_FINANCE, LEVEL_EXECUTIVE)
        )
        if not allowed:
            raise AccessError(
                "Revenue analytics requires the %s role."
                % ("Hospital Finance Analytics" if level == LEVEL_FINANCE
                   else "Hospital Finance Analytics or Hospital Executive")
            )
        return current

    # ------------------------------------------------------------------
    # Filters
    # ------------------------------------------------------------------
    @api.model
    def _timezone_name(self):
        name = (
            self.env["ir.config_parameter"].sudo().get_param(TZ_PARAM) or DEFAULT_TZ
        ).strip()
        return name if name in pytz.all_timezones_set else DEFAULT_TZ

    @api.model
    def _local_today(self):
        tz = pytz.timezone(self._timezone_name())
        return datetime.now(pytz.utc).astimezone(tz).date()

    @api.model
    def _parse_date(self, value, name):
        if value in (None, False, ""):
            return None
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        try:
            return date.fromisoformat(str(value).strip())
        except ValueError:
            raise RevenueAnalyticsError(
                "invalid_date", "'%s' must be a date in YYYY-MM-DD form." % name
            ) from None

    @api.model
    def _parse_id(self, value, name):
        if value in (None, False, ""):
            return None
        try:
            parsed = int(str(value).strip())
        except ValueError:
            raise RevenueAnalyticsError("invalid_filter", "'%s' must be an id." % name) from None
        if parsed <= 0:
            raise RevenueAnalyticsError("invalid_filter", "'%s' must be a positive id." % name)
        return parsed

    @api.model
    def _parse_company(self, value):
        allowed = self.env.user.company_ids
        company_id = self._parse_id(value, "company")
        if company_id is None:
            company = self.env.company
            if company not in allowed:
                company = allowed[:1]
            return company
        company = allowed.filtered(lambda record: record.id == company_id)
        if not company:
            raise RevenueAnalyticsError(
                "company_not_allowed", "You do not have access to that company.", 403
            )
        return company

    @api.model
    def parse_filters(self, raw=None):
        raw = raw or {}
        today = self._local_today()
        date_to = self._parse_date(raw.get("date_to"), "date_to") or today
        date_from = self._parse_date(raw.get("date_from"), "date_from") or date_to.replace(day=1)
        if date_from > date_to:
            raise RevenueAnalyticsError("invalid_date_range", "'date_from' is after 'date_to'.")
        if (date_to - date_from).days + 1 > MAX_SPAN_DAYS:
            raise RevenueAnalyticsError(
                "date_range_too_long",
                "The date range may cover at most %s days." % MAX_SPAN_DAYS,
            )
        department = raw.get("department")
        if department in (None, False, ""):
            department = None
        elif str(department).strip() == "unassigned":
            department = "unassigned"
        else:
            department = self._parse_id(department, "department")
        category = (raw.get("category") or "").strip() or None
        if category and category not in REPORT_CATEGORIES:
            raise RevenueAnalyticsError(
                "invalid_category",
                "'category' must be one of: %s." % ", ".join(REPORT_CATEGORIES),
            )
        basis = (raw.get("basis") or "recognized").strip()
        if basis not in ("recognized", "delivered"):
            raise RevenueAnalyticsError(
                "invalid_basis", "'basis' must be 'recognized' or 'delivered'."
            )
        company = self._parse_company(raw.get("company"))
        tz = pytz.timezone(self._timezone_name())

        def utc_midnight(day):
            local = tz.localize(datetime.combine(day, datetime.min.time()))
            return local.astimezone(pytz.utc).replace(tzinfo=None)

        return {
            "date_from": date_from,
            "date_to": date_to,
            # [from_utc, to_utc) is the local range as stored UTC timestamps.
            "from_utc": utc_midnight(date_from),
            "to_utc": utc_midnight(date_to + timedelta(days=1)),
            "company": company,
            "currency": company.currency_id,
            "tz": self._timezone_name(),
            "doctor": self._parse_id(raw.get("doctor"), "doctor"),
            "department": department,
            "category": category,
            "basis": basis,
        }

    @api.model
    def _scope(self, f):
        return {
            "company": {"id": f["company"].id, "name": f["company"].name},
            "currency": f["currency"].name,
            "date_from": f["date_from"],
            "date_to": f["date_to"],
            "timezone": f["tz"],
            "basis": f["basis"],
            "payer_scope": "self_pay",
            "payer_scope_label": "Self-pay recognized revenue (payer / sponsored excluded)",
            "filters": {
                "doctor": f["doctor"],
                "department": f["department"],
                "category": f["category"],
            },
        }

    # ------------------------------------------------------------------
    # SQL building blocks
    # ------------------------------------------------------------------
    @staticmethod
    def _local_day(column):
        """A UTC timestamp column as a LOCAL calendar date."""
        return "((%s AT TIME ZONE 'UTC') AT TIME ZONE %%(tz)s)::date" % column

    def _round(self, f, amount):
        return f["currency"].round(amount or 0.0)

    def _params(self, f, **extra):
        params = {
            "company": f["company"].id,
            "date_from": f["date_from"],
            "date_to": f["date_to"],
            "from_utc": f["from_utc"],
            "to_utc": f["to_utc"],
            "tz": f["tz"],
            "doctor": f["doctor"],
            "department": f["department"] if f["department"] != "unassigned" else None,
            "category": f["category"],
            "scope": "self_pay",
        }
        params.update(extra)
        return params

    # The per-request working sets. Every aggregate of a request reads these,
    # so the (comparatively expensive) attribution runs ONCE per request rather
    # than once per figure. Temporary, transaction-scoped, never shared.
    RECOGNIZED_SET = "revenue_analytics_recognized"
    DELIVERED_SET = "revenue_analytics_delivered"

    @staticmethod
    def _dimension_filtered(f):
        return bool(f["doctor"] or f["department"])

    def _prepare(self, f, attributed_delivered=False):
        """Materialize this request's company + period rows of both bases.

        recognized: posted provenance rows whose ACCOUNTING DATE is in range.
        delivered:  live charges with delivered billable quantity whose LOCAL
                    service date is in range. Pre-filtered on base columns:
                    a non-stay charge's service_at IS its operational_at, so
                    its UTC window is exact and index-backed; stay charges are
                    found through the source_model index and dated by their
                    period start.
        Excluded categories and sponsored rows are kept: data quality and the
        reconciliation count them. Every aggregate filters them explicitly.

        The delivered set reads the CHEAP class view unless a doctor or
        department is needed (a dimension filter, or a doctor / department
        breakdown on the delivered basis) -- only then does it pay for the
        per-charge attribution."""
        self.env.flush_all()
        cr = self.env.cr
        params = self._params(f)
        cr.execute("DROP TABLE IF EXISTS %s" % self.RECOGNIZED_SET)
        cr.execute(
            "CREATE TEMPORARY TABLE %s ON COMMIT DROP AS "
            "SELECT r.* FROM hospital_revenue_fact r "
            "WHERE r.company_id = %%(company)s "
            "AND r.move_date BETWEEN %%(date_from)s AND %%(date_to)s" % self.RECOGNIZED_SET,
            params,
        )
        cr.execute("DROP TABLE IF EXISTS %s" % self.DELIVERED_SET)
        source = (
            "hospital_revenue_charge_fact"
            if attributed_delivered or self._dimension_filtered(f)
            else "hospital_revenue_charge_class"
        )
        select = (
            "SELECT r.*, %s AS service_day FROM %s r "
            "WHERE r.company_id = %%(company)s AND r.is_live AND r.qty_billable > 0 AND "
            % (self._local_day("r.service_at"), source)
        )
        cr.execute(
            "CREATE TEMPORARY TABLE %s ON COMMIT DROP AS "
            "%s NOT r.is_stay_source "
            "AND r.operational_at >= %%(from_utc)s AND r.operational_at < %%(to_utc)s "
            "UNION ALL "
            "%s r.is_stay_source AND r.source_model = 'hospital.admission' "
            "AND %s BETWEEN %%(date_from)s AND %%(date_to)s"
            % (self.DELIVERED_SET, select, select, self._local_day("r.service_at")),
            params,
        )

    def _basis(self, f, basis=None, scope="self_pay", dims=True, excluded=False):
        """(table, value, quantity, day, where[]) for one basis.

        recognized: posted provenance rows, dated by the accounting date.
        delivered:  live charges with delivered billable quantity, dated by
                    the LOCAL date the care was delivered.
        """
        basis = basis or f["basis"]
        if basis == "recognized":
            table, value, qty, day = self.RECOGNIZED_SET, "r.revenue", "r.quantity", "r.move_date"
            where = []
        else:
            table, value, qty = self.DELIVERED_SET, "r.delivered_value", "r.qty_billable"
            day = "r.service_day"
            where = ["r.is_live", "r.qty_billable > 0"]
        where += [
            "r.company_id = %(company)s",
            "%s BETWEEN %%(date_from)s AND %%(date_to)s" % day,
        ]
        if not excluded:
            where.append("r.excluded_reason IS NULL")
        if scope:
            where.append("r.payer_scope = %(scope)s")
        if dims:
            where += self._dimension_where(f)
        return table, value, qty, day, where

    def _dimension_where(self, f):
        where = []
        if f["doctor"]:
            # The doctor filter selects that doctor's EPISODE-attributed revenue.
            where.append("r.episode_doctor_id = %(doctor)s")
        if f["department"] == "unassigned":
            where.append("r.episode_department_id IS NULL")
        elif f["department"]:
            where.append("r.episode_department_id = %(department)s")
        if f["category"]:
            where.append("r.category = %(category)s")
        return where

    def _fetch(self, sql, params):
        self.env.flush_all()
        self.env.cr.execute(sql, params)
        return self.env.cr.dictfetchall()

    def _totals(self, f, basis=None, scope="self_pay", dims=True, extra_where=None):
        basis = basis or f["basis"]
        table, value, _qty, _day, where = self._basis(f, basis, scope, dims)
        where += list(extra_where or [])
        if basis == "recognized":
            measures = (
                "COALESCE(SUM(r.revenue) FILTER (WHERE r.allocation_type = 'invoice'), 0) AS gross, "
                "COALESCE(-SUM(r.revenue) FILTER (WHERE r.allocation_type = 'credit'), 0) AS credits, "
                "COUNT(DISTINCT r.charge_id) FILTER (WHERE r.allocation_type = 'invoice') AS orders"
            )
        else:
            measures = "COALESCE(SUM(r.delivered_value), 0) AS gross, 0 AS credits, COUNT(DISTINCT r.charge_id) AS orders"
        row = self._fetch(
            "SELECT COALESCE(SUM(%s), 0) AS value, %s, "
            "COUNT(DISTINCT r.patient_id) AS patients, "
            "COUNT(DISTINCT r.encounter_id) AS encounters, "
            "%s AS admissions "
            "FROM %s r WHERE %s" % (
                value, measures,
                "COUNT(DISTINCT r.admission_id)" if basis == "recognized" else "NULL",
                table, " AND ".join(where),
            ),
            self._params(f),
        )[0]
        return {
            "value": self._round(f, row["value"]),
            "gross": self._round(f, row["gross"]),
            "credits": self._round(f, row["credits"]),
            "patients": row["patients"],
            "encounters": row["encounters"],
            "admissions": row["admissions"],
            "orders": row["orders"],
        }

    def _grouped(self, f, key, basis=None, dims=True, extra_where=None, with_qty=False):
        basis = basis or f["basis"]
        table, value, qty, _day, where = self._basis(f, basis, "self_pay", dims)
        where += list(extra_where or [])
        orders = (
            "COUNT(DISTINCT r.charge_id) FILTER (WHERE r.allocation_type = 'invoice')"
            if basis == "recognized" else "COUNT(DISTINCT r.charge_id)"
        )
        rows = self._fetch(
            "SELECT %s AS key, COALESCE(SUM(%s), 0) AS value, "
            "COUNT(DISTINCT r.patient_id) AS patients, %s AS orders%s "
            "FROM %s r WHERE %s GROUP BY 1"
            % (key, value, orders,
               (", COALESCE(SUM(%s), 0) AS quantity" % qty) if with_qty else "",
               table, " AND ".join(where)),
            self._params(f),
        )
        for row in rows:
            row["value"] = self._round(f, row["value"])
        return rows

    @staticmethod
    def _share(value, total):
        return round(value / total, 6) if total else 0.0

    # ------------------------------------------------------------------
    # Doctors
    # ------------------------------------------------------------------
    def _consultation_activity(self, f):
        """Completed consultations by consulting physician, dated by the LOCAL
        completion date. Doctor patient volume is clinical activity, never
        revenue rows."""
        rows = self._fetch(
            "SELECT con.doctor_id AS doctor_id, COUNT(*) AS consultations, "
            "COUNT(DISTINCT con.patient_id) AS patients "
            "FROM hospital_consultation con "
            "WHERE con.company_id = %%(company)s AND con.state = 'completed' "
            "AND con.active AND con.doctor_id IS NOT NULL AND con.completed_at IS NOT NULL "
            "AND %s BETWEEN %%(date_from)s AND %%(date_to)s "
            "GROUP BY con.doctor_id" % self._local_day("con.completed_at"),
            self._params(f),
        )
        return {row["doctor_id"]: row for row in rows}

    def _consultation_patients(self, f):
        return self._fetch(
            "SELECT COUNT(DISTINCT con.patient_id) AS patients, COUNT(*) AS consultations "
            "FROM hospital_consultation con "
            "WHERE con.company_id = %%(company)s AND con.state = 'completed' "
            "AND con.active AND con.completed_at IS NOT NULL "
            "AND %s BETWEEN %%(date_from)s AND %%(date_to)s" % self._local_day("con.completed_at"),
            self._params(f),
        )[0]

    def _doctor_rows(self, f):
        """One row per doctor with activity or revenue, plus an Unassigned row
        for episode revenue with no doctor. Doctor and department filters are
        ROW filters here; the category filter narrows the revenue columns."""
        revenue_f = dict(f, doctor=None, department=None)
        activity = self._consultation_activity(f)
        episode = {
            row["key"]: row
            for row in self._grouped(revenue_f, "r.episode_doctor_id")
        }
        direct = {
            row["key"]: row
            for row in self._grouped(
                revenue_f, "r.direct_doctor_id", extra_where=["r.direct_doctor_id IS NOT NULL"]
            )
        }
        ids = {key for key in set(activity) | set(episode) | set(direct) if key}
        doctors = self.env["hospital.doctor"].sudo().with_context(active_test=False).browse(sorted(ids))
        names = {doctor.id: (doctor.name, doctor.department_id) for doctor in doctors}

        rows = []
        for doctor_id in sorted(ids):
            name, department = names.get(doctor_id, ("Doctor %s" % doctor_id, False))
            if f["doctor"] and doctor_id != f["doctor"]:
                continue
            if f["department"] == "unassigned" and department:
                continue
            if isinstance(f["department"], int) and (
                (department.id if department else None) != f["department"]
            ):
                continue
            ep = episode.get(doctor_id) or {"value": 0.0, "patients": 0}
            rows.append({
                "doctor_id": doctor_id,
                "doctor_name": name,
                "department_name": department.name if department else None,
                "patients_treated": activity.get(doctor_id, {}).get("patients", 0),
                "completed_consultations": activity.get(doctor_id, {}).get("consultations", 0),
                "direct_revenue": (direct.get(doctor_id) or {}).get("value", 0.0),
                "episode_revenue": ep["value"],
                "episode_patients": ep["patients"],
                "avg_episode_revenue_per_patient": self._round(
                    f, ep["value"] / ep["patients"] if ep["patients"] else 0.0
                ),
            })
        unassigned = episode.get(None)
        if unassigned and not f["doctor"] and not f["department"]:
            rows.append({
                "doctor_id": None,
                "doctor_name": "Unassigned",
                "department_name": None,
                "patients_treated": 0,
                "completed_consultations": 0,
                "direct_revenue": 0.0,
                "episode_revenue": unassigned["value"],
                "episode_patients": unassigned["patients"],
                "avg_episode_revenue_per_patient": self._round(
                    f, unassigned["value"] / unassigned["patients"] if unassigned["patients"] else 0.0
                ),
            })
        return rows

    # ------------------------------------------------------------------
    # Services and departments
    # ------------------------------------------------------------------
    def _service_rows(self, f, group="category"):
        if group not in ("category", "service"):
            raise RevenueAnalyticsError("invalid_group", "'group' must be 'category' or 'service'.")
        if group == "category":
            raw = self._grouped(f, "r.category", with_qty=True)
            total = sum(row["value"] for row in raw)
            rows = []
            for row in raw:
                rows.append({
                    "category": row["key"],
                    "label": CATEGORY_LABELS.get(row["key"], row["key"]),
                    "orders": row["orders"],
                    "quantity": (
                        round(row["quantity"], 3)
                        if row["key"] in HOMOGENEOUS_QUANTITY_CATEGORIES else None
                    ),
                    "value": row["value"],
                    "share": self._share(row["value"], total),
                })
        else:
            raw = self._grouped(
                f, "r.service_id || ':' || r.category || ':' || COALESCE(r.uom_id, 0)", with_qty=True
            )
            total = sum(row["value"] for row in raw)
            keys = [tuple(int(part) if part.isdigit() else part for part in row["key"].split(":"))
                    if row["key"] else (None, "other", 0) for row in raw]
            services = self.env["hospital.billing.service"].sudo().with_context(active_test=False).browse(
                sorted({key[0] for key in keys if key[0]})
            )
            uoms = self.env["uom.uom"].sudo().with_context(active_test=False).browse(
                sorted({key[2] for key in keys if key[2]})
            )
            service_names = {service.id: service.name for service in services}
            uom_names = {uom.id: uom.name for uom in uoms}
            rows = []
            for row, (service_id, category, uom_id) in zip(raw, keys):
                rows.append({
                    "service_id": service_id,
                    "label": service_names.get(service_id, "(no service)"),
                    "category": category,
                    "category_label": CATEGORY_LABELS.get(category, category),
                    "uom": uom_names.get(uom_id),
                    "orders": row["orders"],
                    "quantity": round(row["quantity"], 3),
                    "value": row["value"],
                    "share": self._share(row["value"], total),
                })
        rows.sort(key=lambda row: (-row["value"], row["label"] or ""))
        return rows, self._round(f, total)

    def _department_rows(self, f):
        raw = self._grouped(f, "r.episode_department_id")
        total = sum(row["value"] for row in raw)
        departments = self.env["hospital.department"].sudo().with_context(active_test=False).browse(
            sorted(row["key"] for row in raw if row["key"])
        )
        names = {department.id: department.name for department in departments}
        rows = [{
            "department_id": row["key"],
            "department_name": names.get(row["key"], "Unassigned") if row["key"] else "Unassigned",
            "patients": row["patients"],
            "value": row["value"],
            "avg_revenue_per_patient": self._round(
                f, row["value"] / row["patients"] if row["patients"] else 0.0
            ),
            "share": self._share(row["value"], total),
        } for row in raw]
        rows.sort(key=lambda row: (-row["value"], row["department_name"]))
        return rows, self._round(f, total)

    # ------------------------------------------------------------------
    # Trend
    # ------------------------------------------------------------------
    @staticmethod
    def _auto_grain(f):
        span = (f["date_to"] - f["date_from"]).days + 1
        return "day" if span <= 62 else "week" if span <= 366 else "month"

    @staticmethod
    def _bucket_start(day, grain):
        if grain == "week":
            return day - timedelta(days=day.weekday())
        if grain == "month":
            return day.replace(day=1)
        return day

    def _buckets(self, f, grain):
        buckets = []
        current = self._bucket_start(f["date_from"], grain)
        while current <= f["date_to"]:
            buckets.append(current)
            if grain == "day":
                current += timedelta(days=1)
            elif grain == "week":
                current += timedelta(days=7)
            else:
                current = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        return buckets

    def _series(self, f, basis, grain):
        table, value, _qty, day, where = self._basis(f, basis)
        rows = self._fetch(
            "SELECT date_trunc(%%(grain)s, (%s)::timestamp)::date AS bucket, COALESCE(SUM(%s), 0) AS value "
            "FROM %s r WHERE %s GROUP BY 1" % (day, value, table, " AND ".join(where)),
            self._params(f, grain=grain),
        )
        return {row["bucket"]: self._round(f, row["value"]) for row in rows}

    def _trend(self, f, grain=None):
        grain = grain or self._auto_grain(f)
        if grain not in GRAIN_MAX_DAYS:
            raise RevenueAnalyticsError("invalid_grain", "'grain' must be day, week or month.")
        if (f["date_to"] - f["date_from"]).days + 1 > GRAIN_MAX_DAYS[grain]:
            raise RevenueAnalyticsError(
                "grain_range_too_long",
                "A %s trend may cover at most %s days." % (grain, GRAIN_MAX_DAYS[grain]),
            )
        recognized = self._series(f, "recognized", grain)
        delivered = self._series(f, "delivered", grain)
        return {
            "grain": grain,
            "points": [
                {
                    "bucket": bucket,
                    "recognized": recognized.get(bucket, 0.0),
                    "delivered": delivered.get(bucket, 0.0),
                }
                for bucket in self._buckets(f, grain)
            ],
        }

    # ------------------------------------------------------------------
    # Unbilled delivered value (as of date_to)
    # ------------------------------------------------------------------
    def _unbilled(self, f):
        """Delivered, eligible care NOT YET carried by a POSTED invoice, as of
        date_to: delivered by then (local date), invoices posted by then.

        unbilled qty = max(0, eligible delivered qty - posted invoiced qty
                              + posted credited qty IF re-invoicing after the
                                credit was authorized)
        A credit with no re-invoicing authorization is a decision not to bill,
        so it is not backlog. A quantity on a DRAFT invoice is still backlog
        (reported as awaiting posting): a draft is not revenue."""
        where = [
            "r.excluded_reason IS NULL",
            "r.payer_scope = %(scope)s",
            "%s <= %%(date_to)s" % self._local_day("r.service_at"),
        ] + self._dimension_where(f)
        # SET-BASED, on the cheap class view unless a doctor / department is
        # filtered. (A per-charge lateral lookup was measured at 8.3 s for a
        # large backlog; the set-based join at 1.4 s.) A non-stay charge
        # delivered after date_to cannot be backlog as of date_to, which the
        # operational_at bound expresses on an indexed base column.
        rows = self._fetch(
            """
            WITH invoiced AS (
                -- ONE aggregate over the provenance, so the backlog joins a
                -- single hashed set. (Two separate aggregates were measured:
                -- the planner misjudged the small draft one and rescanned it
                -- per charge, 8.9 s.)
                SELECT a.charge_id,
                       COALESCE(SUM(a.quantity) FILTER (
                           WHERE m.state = 'posted' AND m.date <= %%(date_to)s
                             AND a.allocation_type = 'invoice'), 0) AS inv,
                       COALESCE(SUM(a.quantity) FILTER (
                           WHERE m.state = 'posted' AND m.date <= %%(date_to)s
                             AND a.allocation_type = 'credit'), 0) AS cred,
                       COALESCE(SUM(a.quantity) FILTER (
                           WHERE m.state = 'draft' AND a.allocation_type = 'invoice'), 0) AS draft
                  FROM hospital_charge_invoice_allocation a
                  JOIN account_move m ON m.id = a.move_id
                 WHERE a.company_id = %%(company)s AND m.state IN ('posted', 'draft')
                 GROUP BY a.charge_id
            ), backlog AS (
                SELECT r.price_unit_net, r.category, %(day)s AS day,
                       GREATEST(r.qty_billable - COALESCE(p.inv, 0)
                                + CASE WHEN r.allow_reinvoice_after_credit
                                       THEN COALESCE(p.cred, 0) ELSE 0 END, 0) AS qty,
                       COALESCE(p.draft, 0) AS draft_qty
                  FROM %(source)s r
                  LEFT JOIN invoiced p ON p.charge_id = r.id
                 WHERE r.company_id = %%(company)s
                   AND r.is_live
                   AND r.qty_billable > 0
                   AND r.qty_billable - COALESCE(p.inv, 0)
                       + CASE WHEN r.allow_reinvoice_after_credit
                              THEN COALESCE(p.cred, 0) ELSE 0 END > %(tolerance)s
                   AND (r.is_stay_source OR r.operational_at < %%(to_utc)s)
                   AND %(where)s
            )
            SELECT category,
                   CASE WHEN %%(date_to)s - day <= 7 THEN '0_7'
                        WHEN %%(date_to)s - day <= 30 THEN '8_30'
                        WHEN %%(date_to)s - day <= 90 THEN '31_90'
                        ELSE 'over_90' END AS age,
                   COUNT(*) AS charges,
                   SUM(qty * price_unit_net) AS value,
                   SUM(LEAST(draft_qty, qty) * price_unit_net) AS draft_value,
                   MIN(day) AS oldest
              FROM backlog
             GROUP BY 1, 2
            """ % {
                "day": self._local_day("r.service_at"),
                "where": " AND ".join(where),
                "tolerance": QTY_TOLERANCE,
                "source": (
                    "hospital_revenue_charge_fact" if self._dimension_filtered(f)
                    else "hospital_revenue_charge_class"
                ),
            },
            self._params(f),
        )
        by_category, by_age = {}, {key: {"charges": 0, "value": 0.0} for key, _lo, _hi in AGING_BUCKETS}
        total_value = draft_value = 0.0
        charges = 0
        oldest = None
        for row in rows:
            entry = by_category.setdefault(row["category"], {"charges": 0, "value": 0.0, "oldest": None})
            entry["charges"] += row["charges"]
            entry["value"] += row["value"] or 0.0
            entry["oldest"] = min(filter(None, (entry["oldest"], row["oldest"])), default=None)
            by_age[row["age"]]["charges"] += row["charges"]
            by_age[row["age"]]["value"] += row["value"] or 0.0
            total_value += row["value"] or 0.0
            draft_value += row["draft_value"] or 0.0
            charges += row["charges"]
            oldest = min(filter(None, (oldest, row["oldest"])), default=None)
        return {
            "as_of": f["date_to"],
            "value": self._round(f, total_value),
            "charges": charges,
            "awaiting_posting_value": self._round(f, draft_value),
            "oldest_delivered_on": oldest,
            "by_category": sorted(
                (
                    {
                        "category": key,
                        "label": CATEGORY_LABELS.get(key, key),
                        "charges": entry["charges"],
                        "value": self._round(f, entry["value"]),
                        "oldest_delivered_on": entry["oldest"],
                    }
                    for key, entry in by_category.items()
                ),
                key=lambda row: -row["value"],
            ),
            "aging": [
                {"bucket": key, "charges": by_age[key]["charges"],
                 "value": self._round(f, by_age[key]["value"])}
                for key, _lo, _hi in AGING_BUCKETS
            ],
        }

    # ------------------------------------------------------------------
    # Cash, refunds, credits
    # ------------------------------------------------------------------
    def _cash_indicators(self, f, totals):
        cash = self._fetch(
            "SELECT COALESCE(SUM(rc.amount), 0) AS amount, COUNT(*) AS receipts "
            "FROM hospital_charge_receipt rc "
            "LEFT JOIN hospital_billing_account ba ON ba.id = rc.billing_account_id "
            "WHERE rc.company_id = %%(company)s AND rc.state = 'confirmed' "
            "AND COALESCE(ba.payer_type, 'self_pay') = 'self_pay' "
            "AND %s BETWEEN %%(date_from)s AND %%(date_to)s" % self._local_day("rc.received_at"),
            self._params(f),
        )[0]
        refunds = self._fetch(
            "SELECT COALESCE(SUM(op.amount), 0) AS amount, COUNT(*) AS refunds "
            "FROM hospital_admission_accounting_operation op "
            "JOIN account_move mv ON mv.id = op.move_id AND mv.state = 'posted' "
            "WHERE op.company_id = %(company)s AND op.kind IN ('refund', 'legacy_refund') "
            "AND COALESCE(op.accounting_date, mv.date) BETWEEN %(date_from)s AND %(date_to)s",
            self._params(f),
        )[0]
        cash_amount = self._round(f, cash["amount"])
        refund_amount = self._round(f, refunds["amount"])
        return {
            "credit_notes": totals["credits"],
            "credit_note_rate": self._share(totals["credits"], totals["gross"]),
            "cash_received": cash_amount,
            "receipts": cash["receipts"],
            "advance_refunds": refund_amount,
            "advance_refund_count": refunds["refunds"],
            "advance_refund_rate": self._share(refund_amount, cash_amount),
        }

    # ------------------------------------------------------------------
    # GL reconciliation
    # ------------------------------------------------------------------
    def _reconciliation(self, f):
        """Posted GL revenue on the company's configured revenue accounts,
        split into what the invoice provenance explains and what it does not.

            gl_total = attributed (provenance on these accounts)
                     + unattributed (revenue lines with NO provenance:
                       legacy patient bills, manual journals)
            difference must be zero to currency precision.
        """
        accounts = [
            row["account_id"] for row in self._fetch(
                "SELECT DISTINCT revenue_account_id AS account_id "
                "FROM hospital_billing_accounting_config "
                "WHERE company_id = %(company)s AND active AND revenue_account_id IS NOT NULL",
                self._params(f),
            )
        ]
        params = self._params(f, accounts=accounts or [0])
        gl = self._fetch(
            "SELECT COALESCE(-SUM(l.balance), 0) AS amount FROM account_move_line l "
            "JOIN account_move m ON m.id = l.move_id AND m.state = 'posted' "
            "WHERE l.company_id = %(company)s AND l.account_id = ANY(%(accounts)s) "
            "AND l.date BETWEEN %(date_from)s AND %(date_to)s",
            params,
        )[0]["amount"]
        unattributed = self._fetch(
            "SELECT COALESCE(-SUM(l.balance), 0) AS amount, COUNT(*) AS lines, "
            "COUNT(DISTINCT l.move_id) AS moves FROM account_move_line l "
            "JOIN account_move m ON m.id = l.move_id AND m.state = 'posted' "
            "WHERE l.company_id = %(company)s AND l.account_id = ANY(%(accounts)s) "
            "AND l.date BETWEEN %(date_from)s AND %(date_to)s "
            "AND NOT EXISTS (SELECT 1 FROM hospital_charge_invoice_allocation a "
            "WHERE a.move_line_id = l.id)",
            params,
        )[0]
        attributed = self._fetch(
            "SELECT "
            "COALESCE(SUM(r.revenue) FILTER (WHERE r.excluded_reason IS NULL "
            "AND r.payer_scope = 'self_pay'), 0) AS self_pay, "
            "COALESCE(SUM(r.revenue) FILTER (WHERE r.excluded_reason IS NULL "
            "AND r.payer_scope = 'sponsored'), 0) AS sponsored, "
            "COALESCE(SUM(r.revenue) FILTER (WHERE r.excluded_reason IS NOT NULL), 0) AS excluded, "
            "COALESCE(SUM(r.revenue), 0) AS total "
            "FROM " + self.RECOGNIZED_SET + " r WHERE r.company_id = %(company)s "
            "AND r.account_id = ANY(%(accounts)s) "
            "AND r.move_date BETWEEN %(date_from)s AND %(date_to)s",
            params,
        )[0]
        gl_total = self._round(f, gl)
        attributed_total = self._round(f, attributed["total"])
        unattributed_amount = self._round(f, unattributed["amount"])
        return {
            "revenue_account_count": len(accounts),
            "gl_revenue_total": gl_total,
            "attributed_total": attributed_total,
            "attributed_self_pay": self._round(f, attributed["self_pay"]),
            "attributed_sponsored": self._round(f, attributed["sponsored"]),
            "attributed_excluded_categories": self._round(f, attributed["excluded"]),
            "unattributed_amount": unattributed_amount,
            "unattributed_lines": unattributed["lines"],
            "unattributed_moves": unattributed["moves"],
            "difference": self._round(f, gl_total - attributed_total - unattributed_amount),
        }

    # ------------------------------------------------------------------
    # Data quality
    # ------------------------------------------------------------------
    def _recognized_flag(self, f, condition, scope="self_pay", count="r.charge_id", excluded=False):
        table, _value, _qty, _day, where = self._basis(
            f, "recognized", scope=scope, dims=False, excluded=excluded
        )
        row = self._fetch(
            "SELECT COALESCE(SUM(r.revenue), 0) AS amount, COUNT(DISTINCT %s) AS records "
            "FROM %s r WHERE %s AND %s" % (count, table, " AND ".join(where), condition),
            self._params(f),
        )[0]
        return {"amount": self._round(f, row["amount"]), "count": row["records"]}

    def _charge_gap(self, f, condition):
        row = self._fetch(
            "SELECT COUNT(*) AS charges, "
            "COUNT(*) FILTER (WHERE COALESCE(c.amount_received, 0) - COALESCE(c.amount_refunded, 0) > 0.005) AS funded, "
            "COALESCE(SUM(GREATEST(COALESCE(c.amount_received, 0) - COALESCE(c.amount_refunded, 0), 0)), 0) AS cash_held, "
            "COUNT(*) FILTER (WHERE c.qty_delivered > 0) AS delivered "
            "FROM hospital_charge_line c "
            "LEFT JOIN hospital_billing_service s ON s.id = c.service_id "
            "WHERE c.company_id = %%(company)s AND c.charge_state NOT IN ('cancelled', 'reversed') "
            "AND %s BETWEEN %%(date_from)s AND %%(date_to)s AND %s"
            % (self._local_day("c.create_date"), condition),
            self._params(f),
        )[0]
        row["cash_held"] = self._round(f, row["cash_held"])
        return row

    def _legacy_accounting(self, f):
        Admission = self.env["hospital.admission"].sudo().with_context(active_test=False)
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT id FROM hospital_admission WHERE company_id = %%(company)s "
            "AND state = 'discharged' AND discharge_date IS NOT NULL "
            "AND %s BETWEEN %%(date_from)s AND %%(date_to)s ORDER BY id"
            % self._local_day("discharge_date"),
            self._params(f),
        )
        ids = [row[0] for row in self.env.cr.fetchall()]
        scanned = Admission.browse(ids[:LEGACY_ACCOUNTING_SCAN_LIMIT])
        statuses = {}
        visitless = 0
        for admission in scanned:
            if not admission.encounter_id:
                visitless += 1
                continue
            try:
                status = admission._accounting_status()["status"]
            except Exception:  # noqa: BLE001 -- a broken stay must not hide the others
                status = "unavailable"
            statuses[status] = statuses.get(status, 0) + 1
        return {
            "discharged": len(ids),
            "scanned": len(scanned),
            "truncated": len(ids) > LEGACY_ACCOUNTING_SCAN_LIMIT,
            "visitless_legacy": visitless,
            "legacy_pending": statuses.get("legacy_pending", 0),
            "not_posted": statuses.get("not_posted", 0),
            "posted": statuses.get("posted", 0),
            "outside_scope": statuses.get("outside_scope", 0),
            "statuses": statuses,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @api.model
    def session(self):
        level = self.access_level()
        today = self._local_today()
        companies = self.env.user.company_ids
        return {
            "access_level": level,
            "capabilities": {
                "analytics": level is not None,
                "finance": level == LEVEL_FINANCE,
                "executive": level in (LEVEL_FINANCE, LEVEL_EXECUTIVE),
                "data_quality": level == LEVEL_FINANCE,
                "unbilled_detail": level == LEVEL_FINANCE,
                "patient_drilldown": False,
            },
            "companies": [{"id": company.id, "name": company.name} for company in companies]
            if level else [],
            "default_company": self.env.company.id if level else None,
            "timezone": self._timezone_name(),
            "default_range": {"date_from": today.replace(day=1), "date_to": today},
            "categories": [
                {"key": key, "label": CATEGORY_LABELS[key]} for key in REPORT_CATEGORIES
            ],
        }

    @api.model
    def summary(self, raw=None):
        level = self._require(LEVEL_EXECUTIVE)
        f = self.parse_filters(raw)
        self._prepare(f, attributed_delivered=f["basis"] == "delivered")
        recognized = self._totals(f, "recognized")
        delivered = self._totals(f, "delivered")
        doctors = self._doctor_rows(f)
        named = [row for row in doctors if row["doctor_id"]]
        departments, _total = self._department_rows(dict(f, basis="recognized"))
        categories, _total = self._service_rows(dict(f, basis="recognized"), "category")
        sponsored = self._totals(f, "recognized", scope=None, extra_where=["r.payer_scope = 'sponsored'"])
        consultations = self._consultation_patients(f)
        unbilled = self._unbilled(f)

        def top_doctor(measure):
            candidates = [row for row in named if (row[measure] or 0) > 0]
            if not candidates:
                return None
            best = max(candidates, key=lambda row: (row[measure], -row["doctor_id"]))
            return {"id": best["doctor_id"], "name": best["doctor_name"], "value": best[measure]}

        assigned_departments = [row for row in departments if row["department_id"] and row["value"] > 0]
        top_department = max(assigned_departments, key=lambda row: row["value"], default=None)
        top_category = max((row for row in categories if row["value"] > 0),
                           key=lambda row: row["value"], default=None)
        return {
            "scope": self._scope(dict(f, basis="recognized")),
            "access_level": level,
            "revenue": {
                "gross": recognized["gross"],
                "credits": recognized["credits"],
                "net": recognized["value"],
            },
            "patients": {
                "unique": recognized["patients"],
                "avg_revenue_per_patient": self._round(
                    f, recognized["value"] / recognized["patients"] if recognized["patients"] else 0.0
                ),
                "encounters": recognized["encounters"],
                "admissions": recognized["admissions"],
                "completed_consultations": consultations["consultations"],
                "consulted_patients": consultations["patients"],
            },
            "delivered_value": delivered["value"],
            "unbilled": {
                "value": unbilled["value"],
                "charges": unbilled["charges"],
                "as_of": unbilled["as_of"],
            },
            "top": {
                "doctor_by_patients": top_doctor("patients_treated"),
                "doctor_by_direct_revenue": top_doctor("direct_revenue"),
                "doctor_by_episode_revenue": top_doctor("episode_revenue"),
                "department": (
                    {"id": top_department["department_id"], "name": top_department["department_name"],
                     "value": top_department["value"]} if top_department else None
                ),
                "category": (
                    {"key": top_category["category"], "name": top_category["label"],
                     "value": top_category["value"]} if top_category else None
                ),
            },
            "indicators": self._cash_indicators(f, recognized),
            "excluded_scope": {
                "sponsored_revenue": sponsored["value"],
                "sponsored_encounters": sponsored["encounters"],
            },
            "trend": self._trend(dict(f, basis="recognized")),
        }

    @api.model
    def trend(self, raw=None, grain=None):
        self._require(LEVEL_EXECUTIVE)
        f = self.parse_filters(raw)
        self._prepare(f)
        return {"scope": self._scope(f), **self._trend(f, (grain or "").strip() or None)}

    @api.model
    def doctors(self, raw=None, sort=None):
        self._require(LEVEL_EXECUTIVE)
        f = self.parse_filters(raw)
        sort = (sort or "episode").strip()
        measure = {
            "patients": "patients_treated",
            "direct": "direct_revenue",
            "episode": "episode_revenue",
        }.get(sort)
        if not measure:
            raise RevenueAnalyticsError("invalid_sort", "'sort' must be patients, direct or episode.")
        self._prepare(f, attributed_delivered=f["basis"] == "delivered")
        rows = self._doctor_rows(f)
        rows.sort(key=lambda row: (row["doctor_id"] is None, -(row[measure] or 0), row["doctor_name"]))
        return {
            "scope": self._scope(f),
            "sort": sort,
            "rows": rows,
            "totals": {
                "direct_revenue": self._round(f, sum(row["direct_revenue"] for row in rows)),
                "episode_revenue": self._round(f, sum(row["episode_revenue"] for row in rows)),
            },
        }

    @api.model
    def services(self, raw=None, group=None):
        self._require(LEVEL_EXECUTIVE)
        f = self.parse_filters(raw)
        self._prepare(f)
        rows, total = self._service_rows(f, (group or "category").strip())
        return {"scope": self._scope(f), "group": (group or "category").strip(), "rows": rows, "total": total}

    @api.model
    def departments(self, raw=None):
        self._require(LEVEL_EXECUTIVE)
        f = self.parse_filters(raw)
        self._prepare(f, attributed_delivered=f["basis"] == "delivered")
        rows, total = self._department_rows(f)
        return {"scope": self._scope(f), "rows": rows, "total": total}

    @api.model
    def unbilled(self, raw=None):
        self._require(LEVEL_FINANCE)
        f = self.parse_filters(raw)
        return {"scope": self._scope(f), **self._unbilled(f)}

    @api.model
    def data_quality(self, raw=None):
        self._require(LEVEL_FINANCE)
        f = self.parse_filters(raw)
        self._prepare(f)
        unbilled = self._unbilled(f)
        return {
            "scope": self._scope(dict(f, basis="recognized")),
            "unbilled": {"value": unbilled["value"], "charges": unbilled["charges"],
                         "awaiting_posting_value": unbilled["awaiting_posting_value"]},
            "missing_doctor": self._recognized_flag(
                f, "r.episode_doctor_id IS NULL", count="r.encounter_id"),
            "admission_doctor_fallback": self._recognized_flag(
                f, "r.episode_doctor_fallback", count="r.admission_id"),
            "doctor_mismatch": self._recognized_flag(
                f, "r.episode_doctor_mismatch", count="r.admission_id"),
            "missing_department": self._recognized_flag(
                f, "r.episode_department_id IS NULL", count="r.encounter_id"),
            "ward_department_fallback": self._recognized_flag(
                f, "r.episode_department_fallback", count="r.admission_id"),
            "sponsored_excluded": self._recognized_flag(
                f, "r.payer_scope = 'sponsored'", scope=None, count="r.encounter_id"),
            "excluded_categories_recognized": self._recognized_flag(
                f, "r.excluded_reason IS NOT NULL", scope=None, excluded=True),
            "registration_card": self._charge_gap(f, "s.service_type = 'registration'"),
            "consultation_gap": self._charge_gap(
                f,
                "s.service_type = 'consultation' AND c.source_model = 'hospital.appointment' "
                "AND COALESCE(c.qty_delivered, 0) <= 0",
            ),
            "legacy_manual_revenue": self._reconciliation(f),
            "legacy_accounting": self._legacy_accounting(f),
        }

from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

# License statuses that block confirming an employee on duty when the
# duty role requires a license.
BLOCKING_LICENSE_STATUSES = (
    "missing",
    "expired",
    "suspended",
    "pending_verification",
)


class HospitalHrDutySchedule(models.Model):
    _name = "hospital.hr.duty.schedule"
    _description = "Hospital HR Duty Schedule"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_from desc, name desc"

    name = fields.Char(
        default=lambda self: _("New"),
        copy=False,
        readonly=True,
    )
    date_from = fields.Date(required=True, tracking=True)
    date_to = fields.Date(required=True, tracking=True)
    department_id = fields.Many2one(
        "hospital.department",
        string="Clinical Department",
        tracking=True,
        help="Clinical/service unit this roster covers.",
    )
    responsible_id = fields.Many2one(
        "hr.employee",
        string="Responsible",
        tracking=True,
        help="Head nurse, HR officer, unit lead or department coordinator.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "hospital.hr.duty.line",
        "schedule_id",
        string="Duty Lines",
    )
    line_count = fields.Integer(compute="_compute_line_count")
    notes = fields.Text()
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    @api.depends("line_ids")
    def _compute_line_count(self):
        for schedule in self:
            schedule.line_count = len(schedule.line_ids)

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for schedule in self:
            if schedule.date_from > schedule.date_to:
                raise ValidationError(
                    _("The schedule start date must be before its end date.")
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.hr.duty.schedule")
                    or _("New")
                )
        return super().create(vals_list)

    def action_confirm(self):
        for schedule in self:
            if schedule.state != "draft":
                raise UserError(_("Only draft schedules can be confirmed."))
            lines = schedule.line_ids.filtered(
                lambda line: line.state in ("draft", "scheduled")
            )
            warnings = lines._check_duty_confirmation()
            lines.write({"state": "confirmed"})
            schedule.state = "confirmed"
            for warning in warnings:
                schedule.message_post(body=warning)
        return True

    def action_done(self):
        for schedule in self:
            if schedule.state != "confirmed":
                raise UserError(_("Only confirmed schedules can be marked done."))
            schedule.state = "done"
        return True

    def action_cancel(self):
        for schedule in self:
            if schedule.state not in ("draft", "confirmed"):
                raise UserError(
                    _("Only draft or confirmed schedules can be cancelled.")
                )
            schedule.line_ids.filtered(
                lambda line: line.state not in ("completed", "missed", "cancelled")
            ).write({"state": "cancelled"})
            schedule.state = "cancelled"
        return True

    def action_reset_to_draft(self):
        if not self.env.user.has_group("hospital_hr.group_hospital_hr_manager"):
            raise UserError(
                _("Only Hospital HR Managers can reset a duty schedule to draft.")
            )
        for schedule in self:
            schedule.line_ids.filtered(
                lambda line: line.state not in ("completed", "missed")
            ).write({"state": "draft"})
            schedule.state = "draft"
        return True


class HospitalHrDutyLine(models.Model):
    _name = "hospital.hr.duty.line"
    _description = "Hospital HR Duty Line"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, shift_template_id, employee_id"

    name = fields.Char(compute="_compute_name", store=True)
    schedule_id = fields.Many2one(
        "hospital.hr.duty.schedule",
        string="Duty Schedule",
        required=True,
        index=True,
        ondelete="cascade",
    )
    date = fields.Date(
        required=True,
        default=lambda self: fields.Date.context_today(self),
        index=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        required=True,
        index=True,
        domain="[('can_be_assigned_to_duty', '=', True)]",
        tracking=True,
    )
    hospital_role_id = fields.Many2one(
        "hospital.hr.role",
        string="Duty Role",
        help="Defaults from the employee's hospital role; override when the "
        "employee covers a different role for this shift.",
    )
    department_id = fields.Many2one(
        "hospital.department",
        string="Clinical Department",
        help="Stored on the line for reporting; defaults from the schedule.",
    )
    shift_template_id = fields.Many2one(
        "hospital.hr.shift.template",
        string="Shift",
        required=True,
    )
    shift_type = fields.Selection(
        related="shift_template_id.shift_type",
        store=True,
    )
    # Snapshot of the template timing at scheduling time: recomputed only when
    # the line's shift template is changed, not when the template itself is
    # edited later.
    start_time = fields.Float(
        compute="_compute_shift_times",
        store=True,
        readonly=False,
    )
    end_time = fields.Float(
        compute="_compute_shift_times",
        store=True,
        readonly=False,
    )
    duration_hours = fields.Float(
        compute="_compute_duration_hours",
        store=True,
    )
    start_datetime = fields.Datetime(
        compute="_compute_datetimes",
        store=True,
    )
    end_datetime = fields.Datetime(
        compute="_compute_datetimes",
        store=True,
    )
    duty_area = fields.Char(
        help="e.g. Ward B, ICU, Triage, Front Desk, OT Room 1, Pharmacy Counter.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("scheduled", "Scheduled"),
            ("confirmed", "Confirmed"),
            ("on_duty", "On Duty"),
            ("completed", "Completed"),
            ("missed", "Missed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    remarks = fields.Char()
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    attendance_log_ids = fields.One2many(
        "hospital.hr.attendance.log",
        "duty_line_id",
        string="Duty Logs",
    )
    attendance_log_count = fields.Integer(
        compute="_compute_attendance_summary",
    )
    current_attendance_log_id = fields.Many2one(
        "hospital.hr.attendance.log",
        compute="_compute_attendance_summary",
        string="Open Duty Log",
    )
    actual_check_in = fields.Datetime(
        compute="_compute_attendance_summary",
        string="Actual Check In",
    )
    actual_check_out = fields.Datetime(
        compute="_compute_attendance_summary",
        string="Actual Check Out",
    )
    actual_worked_hours = fields.Float(
        compute="_compute_attendance_summary",
        digits=(16, 2),
    )
    lateness_summary = fields.Char(
        compute="_compute_attendance_summary",
    )

    @api.depends("employee_id.name", "shift_template_id.name", "date")
    def _compute_name(self):
        for line in self:
            parts = [
                line.employee_id.name,
                line.shift_template_id.name,
                str(line.date or ""),
            ]
            line.name = " - ".join(part for part in parts if part)

    @api.depends("shift_template_id")
    def _compute_shift_times(self):
        for line in self:
            if line.shift_template_id:
                line.start_time = line.shift_template_id.start_time
                line.end_time = line.shift_template_id.end_time

    @api.depends("start_time", "end_time")
    def _compute_duration_hours(self):
        for line in self:
            if line.end_time > line.start_time:
                line.duration_hours = line.end_time - line.start_time
            else:
                line.duration_hours = 24.0 - line.start_time + line.end_time

    def _get_duty_interval(self):
        """Naive local (start, end) datetimes of the duty, overnight-aware."""
        self.ensure_one()
        start = datetime.combine(self.date, time.min) + timedelta(
            hours=self.start_time
        )
        end_offset = (
            self.end_time
            if self.end_time > self.start_time
            else self.end_time + 24.0
        )
        end = datetime.combine(self.date, time.min) + timedelta(hours=end_offset)
        return start, end

    def _get_tz(self):
        tz_name = self.env.user.tz or self.env.context.get("tz") or "UTC"
        try:
            return pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            return pytz.utc

    @api.depends("date", "start_time", "end_time")
    def _compute_datetimes(self):
        tz = self._get_tz()
        for line in self:
            if not line.date:
                line.start_datetime = False
                line.end_datetime = False
                continue
            start, end = line._get_duty_interval()
            line.start_datetime = (
                tz.localize(start).astimezone(pytz.utc).replace(tzinfo=None)
            )
            line.end_datetime = (
                tz.localize(end).astimezone(pytz.utc).replace(tzinfo=None)
            )

    @api.onchange("employee_id")
    def _onchange_employee_id(self):
        if self.employee_id and self.employee_id.hospital_role_id:
            self.hospital_role_id = self.employee_id.hospital_role_id

    @api.onchange("schedule_id")
    def _onchange_schedule_id(self):
        if self.schedule_id:
            if not self.department_id:
                self.department_id = self.schedule_id.department_id
            if not self.date or not (
                self.schedule_id.date_from
                <= self.date
                <= self.schedule_id.date_to
            ):
                self.date = self.schedule_id.date_from

    @api.constrains("date", "schedule_id")
    def _check_date_in_schedule(self):
        for line in self:
            schedule = line.schedule_id
            if schedule.date_from and schedule.date_to and not (
                schedule.date_from <= line.date <= schedule.date_to
            ):
                raise ValidationError(
                    _(
                        "Duty date %(date)s is outside the schedule period "
                        "(%(date_from)s - %(date_to)s).",
                        date=line.date,
                        date_from=schedule.date_from,
                        date_to=schedule.date_to,
                    )
                )

    @api.constrains("employee_id", "date", "start_time", "end_time", "state")
    def _check_overlap(self):
        for line in self:
            if line.state == "cancelled" or not line.date:
                continue
            start, end = line._get_duty_interval()
            neighbours = self.search(
                [
                    ("id", "!=", line.id),
                    ("employee_id", "=", line.employee_id.id),
                    ("state", "!=", "cancelled"),
                    ("date", ">=", line.date - timedelta(days=1)),
                    ("date", "<=", line.date + timedelta(days=1)),
                ]
            )
            for other in neighbours:
                other_start, other_end = other._get_duty_interval()
                if start < other_end and other_start < end:
                    raise ValidationError(
                        _(
                            "%(employee)s is already on duty from %(start)s to "
                            "%(end)s (%(duty)s), which overlaps this assignment.",
                            employee=line.employee_id.name,
                            start=other_start,
                            end=other_end,
                            duty=other.display_name,
                        )
                    )

    @api.depends(
        "attendance_log_ids.status",
        "attendance_log_ids.check_in",
        "attendance_log_ids.check_out",
        "attendance_log_ids.worked_hours",
        "attendance_log_ids.is_late",
        "attendance_log_ids.late_minutes",
        "attendance_log_ids.early_checkout",
        "attendance_log_ids.early_checkout_minutes",
    )
    def _compute_attendance_summary(self):
        for line in self:
            logs = line.attendance_log_ids.filtered(
                lambda log: log.status != "cancelled"
            )
            closed = logs.filtered(lambda log: log.status in ("closed", "corrected"))
            check_ins = [log.check_in for log in logs if log.check_in]
            check_outs = [log.check_out for log in closed if log.check_out]
            line.attendance_log_count = len(logs)
            line.current_attendance_log_id = logs.filtered(
                lambda log: log.status == "open"
            )[:1]
            line.actual_check_in = min(check_ins) if check_ins else False
            line.actual_check_out = max(check_outs) if check_outs else False
            line.actual_worked_hours = round(
                sum(closed.mapped("worked_hours")), 2
            )
            parts = []
            late_log = logs.filtered("is_late")[:1]
            if late_log:
                parts.append(_("Late by %s min") % late_log.late_minutes)
            early_log = closed.filtered("early_checkout")[-1:]
            if early_log:
                parts.append(
                    _("Early checkout by %s min")
                    % early_log.early_checkout_minutes
                )
            if parts:
                line.lateness_summary = " / ".join(parts)
            elif logs and line.state == "completed":
                line.lateness_summary = _("On time")
            else:
                line.lateness_summary = False

    def action_check_in(self):
        now = fields.Datetime.now()
        for line in self:
            if line.state not in ("scheduled", "confirmed"):
                raise ValidationError(
                    _(
                        "Check-in is only possible for scheduled or confirmed "
                        "duty lines (%(duty)s is %(state)s).",
                        duty=line.display_name,
                        state=line.state,
                    )
                )
            if line.current_attendance_log_id:
                raise ValidationError(
                    _(
                        "There is already an open duty log for %(duty)s.",
                        duty=line.display_name,
                    )
                )
            self.env["hospital.hr.attendance.log"].create(
                {
                    "employee_id": line.employee_id.id,
                    "duty_line_id": line.id,
                    "check_in": now,
                    "source": "duty_schedule",
                    "status": "open",
                }
            )
            line.state = "on_duty"
            line.message_post(
                body=_(
                    "%(employee)s checked in.", employee=line.employee_id.name
                )
            )
        return True

    def action_check_out(self):
        now = fields.Datetime.now()
        for line in self:
            if line.state != "on_duty":
                raise ValidationError(
                    _(
                        "Check-out is only possible while on duty "
                        "(%(duty)s is %(state)s).",
                        duty=line.display_name,
                        state=line.state,
                    )
                )
            log = line.current_attendance_log_id
            if not log:
                raise ValidationError(
                    _(
                        "No open duty log found for %(duty)s.",
                        duty=line.display_name,
                    )
                )
            log.write({"check_out": now, "status": "closed"})
            line.state = "completed"
            line.message_post(
                body=_(
                    "%(employee)s checked out (%(hours).2f hours).",
                    employee=line.employee_id.name,
                    hours=log.worked_hours,
                )
            )
        return True

    def action_mark_missed(self):
        for line in self:
            if line.state not in ("scheduled", "confirmed"):
                raise ValidationError(
                    _(
                        "Only scheduled or confirmed duty lines can be marked "
                        "missed (%(duty)s is %(state)s).",
                        duty=line.display_name,
                        state=line.state,
                    )
                )
            line.state = "missed"
            line.message_post(body=_("Duty marked as missed."))
        return True

    def action_reset_to_scheduled(self):
        if not self.env.user.has_group("hospital_hr.group_hospital_hr_manager"):
            raise UserError(
                _("Only Hospital HR Managers can reset a duty line to scheduled.")
            )
        for line in self:
            if line.state not in ("missed", "cancelled", "on_duty", "completed"):
                raise ValidationError(
                    _(
                        "%(duty)s cannot be reset from state %(state)s.",
                        duty=line.display_name,
                        state=line.state,
                    )
                )
            line.state = "scheduled"
            line.message_post(
                body=_("Duty line reset to scheduled. Existing duty logs were kept.")
            )
        return True

    def action_cancel_line(self):
        is_manager = self.env.user.has_group(
            "hospital_hr.group_hospital_hr_manager"
        )
        for line in self:
            if line.state == "cancelled":
                continue
            if line.state == "completed" and not is_manager:
                raise UserError(
                    _(
                        "Only Hospital HR Managers can cancel a completed "
                        "duty line."
                    )
                )
            line.state = "cancelled"
            line.message_post(
                body=_("Duty line cancelled. Existing duty logs were kept.")
            )
        return True

    def action_view_attendance_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Duty Logs"),
            "res_model": "hospital.hr.attendance.log",
            "view_mode": "list,form",
            "domain": [("duty_line_id", "=", self.id)],
            "context": {
                "default_duty_line_id": self.id,
                "default_employee_id": self.employee_id.id,
            },
        }

    def _check_duty_confirmation(self):
        """Validate lines before confirmation.

        Raises on hard blocks (duty-ineligible employee, missing/expired/
        suspended/unverified license for a license-requiring duty role);
        returns a list of warning strings for soft issues (expiring license).
        """
        warnings = []
        for line in self:
            employee = line.employee_id
            if not employee.can_be_assigned_to_duty:
                raise ValidationError(
                    _(
                        "%(employee)s is not eligible for duty assignment "
                        "(Duty Eligible is unchecked on the employee).",
                        employee=employee.name,
                    )
                )
            role = line.hospital_role_id or employee.hospital_role_id
            if role.requires_license:
                license_status = employee.license_status
                if license_status in BLOCKING_LICENSE_STATUSES:
                    status_label = dict(
                        employee._fields["license_status"].selection
                    ).get(license_status, license_status)
                    raise ValidationError(
                        _(
                            "%(employee)s cannot be confirmed for %(role)s duty: "
                            "license status is '%(status)s'.",
                            employee=employee.name,
                            role=role.name,
                            status=status_label,
                        )
                    )
                if license_status == "expiring_soon":
                    warnings.append(
                        _(
                            "Warning: the license of %(employee)s expires soon "
                            "(%(date)s) — duty %(duty)s was confirmed anyway.",
                            employee=employee.name,
                            date=employee.license_expiry_date,
                            duty=line.display_name,
                        )
                    )
        return warnings

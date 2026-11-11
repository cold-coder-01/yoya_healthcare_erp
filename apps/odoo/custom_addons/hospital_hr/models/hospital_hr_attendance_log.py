from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

DEFAULT_LATE_GRACE_MINUTES = 15


class HospitalHrAttendanceLog(models.Model):
    _name = "hospital.hr.attendance.log"
    _description = "Hospital HR Duty Log"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "check_in desc, employee_id"

    name = fields.Char(compute="_compute_name", store=True)
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        required=True,
        index=True,
        tracking=True,
    )
    duty_line_id = fields.Many2one(
        "hospital.hr.duty.line",
        string="Duty Line",
        index=True,
        ondelete="set null",
        help="Roster assignment this log belongs to. Empty for ad-hoc/manual logs.",
    )
    schedule_id = fields.Many2one(
        related="duty_line_id.schedule_id",
        store=True,
        string="Duty Schedule",
    )
    department_id = fields.Many2one(
        "hospital.department",
        string="Clinical Department",
        compute="_compute_context_fields",
        store=True,
        readonly=False,
    )
    hospital_role_id = fields.Many2one(
        "hospital.hr.role",
        string="Hospital Role",
        compute="_compute_context_fields",
        store=True,
        readonly=False,
    )
    shift_template_id = fields.Many2one(
        related="duty_line_id.shift_template_id",
        store=True,
        string="Shift",
    )
    check_in = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        tracking=True,
    )
    check_out = fields.Datetime(tracking=True)
    worked_hours = fields.Float(
        compute="_compute_worked_hours",
        store=True,
        digits=(16, 2),
    )
    source = fields.Selection(
        [
            ("manual", "Manual"),
            ("duty_schedule", "Duty Schedule"),
            ("hr_attendance", "Odoo Attendance"),
            ("biometric", "Biometric"),
            ("mobile", "Mobile"),
        ],
        required=True,
        default="manual",
        help="Odoo Attendance / Biometric / Mobile are reserved for future "
        "integrations and are not produced by this module yet.",
    )
    status = fields.Selection(
        [
            ("open", "Open"),
            ("closed", "Closed"),
            ("corrected", "Corrected"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="open",
        tracking=True,
        help="Open: checked in, not yet checked out. Closed: checked out. "
        "Corrected: manually adjusted by HR. Cancelled: voided log.",
    )
    is_late = fields.Boolean(
        compute="_compute_lateness",
        store=True,
        string="Late",
    )
    late_minutes = fields.Integer(
        compute="_compute_lateness",
        store=True,
    )
    early_checkout = fields.Boolean(
        compute="_compute_early_checkout",
        store=True,
    )
    early_checkout_minutes = fields.Integer(
        compute="_compute_early_checkout",
        store=True,
    )
    notes = fields.Text()
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)

    @api.model
    def _get_late_grace_minutes(self):
        param = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(
                "hospital_hr.late_grace_minutes",
                DEFAULT_LATE_GRACE_MINUTES,
            )
        )
        try:
            return int(param)
        except (TypeError, ValueError):
            return DEFAULT_LATE_GRACE_MINUTES

    @api.depends("employee_id.name", "check_in")
    def _compute_name(self):
        for log in self:
            when = ""
            if log.check_in:
                when = fields.Datetime.context_timestamp(
                    log, log.check_in
                ).strftime("%Y-%m-%d %H:%M")
            parts = [log.employee_id.name, when]
            log.name = " - ".join(part for part in parts if part)

    @api.depends(
        "duty_line_id",
        "employee_id",
        "duty_line_id.department_id",
        "duty_line_id.hospital_role_id",
        "employee_id.hospital_department_id",
        "employee_id.hospital_role_id",
    )
    def _compute_context_fields(self):
        for log in self:
            if log.duty_line_id:
                log.department_id = log.duty_line_id.department_id
                log.hospital_role_id = (
                    log.duty_line_id.hospital_role_id
                    or log.employee_id.hospital_role_id
                )
            else:
                log.department_id = log.employee_id.hospital_department_id
                log.hospital_role_id = log.employee_id.hospital_role_id

    @api.depends("check_in", "check_out")
    def _compute_worked_hours(self):
        for log in self:
            if log.check_in and log.check_out:
                delta = log.check_out - log.check_in
                log.worked_hours = round(delta.total_seconds() / 3600.0, 2)
            else:
                log.worked_hours = 0.0

    @api.depends("check_in", "duty_line_id.start_datetime")
    def _compute_lateness(self):
        grace = self._get_late_grace_minutes()
        for log in self:
            log.is_late = False
            log.late_minutes = 0
            start = log.duty_line_id.start_datetime
            if start and log.check_in:
                minutes = (log.check_in - start).total_seconds() / 60.0
                if minutes > grace:
                    log.is_late = True
                    log.late_minutes = int(round(minutes))

    @api.depends("check_out", "duty_line_id.end_datetime")
    def _compute_early_checkout(self):
        grace = self._get_late_grace_minutes()
        for log in self:
            log.early_checkout = False
            log.early_checkout_minutes = 0
            end = log.duty_line_id.end_datetime
            if end and log.check_out:
                minutes = (end - log.check_out).total_seconds() / 60.0
                if minutes > grace:
                    log.early_checkout = True
                    log.early_checkout_minutes = int(round(minutes))

    @api.constrains("check_in", "check_out")
    def _check_times(self):
        for log in self:
            if log.check_out and log.check_out <= log.check_in:
                raise ValidationError(
                    _("The check-out time must be later than the check-in time.")
                )

    @api.constrains("duty_line_id", "status")
    def _check_single_open_log(self):
        for log in self:
            if log.status != "open" or not log.duty_line_id:
                continue
            duplicate = self.search_count(
                [
                    ("id", "!=", log.id),
                    ("duty_line_id", "=", log.duty_line_id.id),
                    ("status", "=", "open"),
                ]
            )
            if duplicate:
                raise ValidationError(
                    _(
                        "There is already an open duty log for %(duty)s. "
                        "Check out first.",
                        duty=log.duty_line_id.display_name,
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("check_out") and vals.get("status", "open") == "open":
                vals["status"] = "closed"
        return super().create(vals_list)

    def write(self, vals):
        result = super().write(vals)
        if vals.get("check_out") and "status" not in vals:
            self.filtered(lambda log: log.status == "open").write(
                {"status": "closed"}
            )
        return result

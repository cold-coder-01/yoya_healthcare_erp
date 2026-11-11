from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalPatientCoverage(models.Model):
    _name = "hospital.patient.coverage"
    _description = "Patient Insurance / Corporate Coverage"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name desc"

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    provider_id = fields.Many2one(
        "hospital.insurance.provider",
        string="Payer",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    coverage_type = fields.Selection(
        [
            ("insurance_policy", "Insurance Policy"),
            ("employee_benefit", "Employee Benefit"),
            ("corporate_credit", "Corporate Credit"),
            ("government_support", "Government Support"),
            ("ngo_support", "NGO Support"),
            ("donor_support", "Donor Support"),
            ("other", "Other"),
        ],
        default="insurance_policy",
        tracking=True,
    )
    policy_number = fields.Char()
    membership_number = fields.Char()
    employee_id_number = fields.Char(string="Employee ID Number")
    principal_member_name = fields.Char()
    relationship_to_principal = fields.Selection(
        [
            ("self", "Self"),
            ("spouse", "Spouse"),
            ("child", "Child"),
            ("parent", "Parent"),
            ("employee", "Employee"),
            ("dependent", "Dependent"),
            ("other", "Other"),
        ],
        default="self",
    )
    coverage_percent = fields.Float(string="Coverage %", default=0.0)
    annual_limit = fields.Monetary()
    remaining_limit = fields.Monetary()
    start_date = fields.Date()
    end_date = fields.Date()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("expired", "Expired"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    is_active_coverage = fields.Boolean(
        string="Currently Valid",
        compute="_compute_is_active_coverage",
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("state", "start_date", "end_date")
    def _compute_is_active_coverage(self):
        today = fields.Date.context_today(self)
        for coverage in self:
            valid = coverage.state == "active"
            if valid and coverage.start_date and coverage.start_date > today:
                valid = False
            if valid and coverage.end_date and coverage.end_date < today:
                valid = False
            coverage.is_active_coverage = valid

    @api.onchange("provider_id")
    def _onchange_provider_id(self):
        if self.provider_id and not self.coverage_percent:
            self.coverage_percent = self.provider_id.default_coverage_percent

    @api.constrains("coverage_percent")
    def _check_coverage_percent(self):
        for coverage in self:
            if not (0.0 <= coverage.coverage_percent <= 100.0):
                raise ValidationError("Coverage percent must be between 0 and 100.")

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for coverage in self:
            if (
                coverage.start_date
                and coverage.end_date
                and coverage.end_date < coverage.start_date
            ):
                raise ValidationError("End date cannot be before start date.")

    @api.constrains("remaining_limit")
    def _check_remaining_limit(self):
        for coverage in self:
            if coverage.remaining_limit < 0:
                raise ValidationError("Remaining limit cannot be negative.")

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.patient.coverage") or "New"
                )
        return super().create(vals_list)

    def action_activate(self):
        for coverage in self:
            if coverage.state != "draft":
                raise UserError("Only draft coverages can be activated.")
            coverage.state = "active"

    def action_suspend(self):
        for coverage in self:
            if coverage.state != "active":
                raise UserError("Only active coverages can be suspended.")
            coverage.state = "suspended"

    def action_expire(self):
        for coverage in self:
            if coverage.state not in ("active", "suspended"):
                raise UserError(
                    "Only active or suspended coverages can be expired."
                )
            coverage.state = "expired"

    def action_cancel(self):
        for coverage in self:
            if coverage.state not in ("draft", "active", "suspended"):
                raise UserError(
                    "Only draft, active, or suspended coverages can be cancelled."
                )
            coverage.state = "cancelled"

    def action_reset_to_draft(self):
        for coverage in self:
            if coverage.state not in ("suspended", "cancelled"):
                raise UserError(
                    "Only suspended or cancelled coverages can be reset to draft."
                )
            coverage.state = "draft"

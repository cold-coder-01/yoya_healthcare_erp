from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HospitalInsuranceProvider(models.Model):
    _name = "hospital.insurance.provider"
    _description = "Insurance / Corporate Payer"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(tracking=True)
    payer_type = fields.Selection(
        [
            ("insurance", "Insurance Company"),
            ("corporate", "Corporate Employer"),
            ("government_program", "Government Program"),
            ("ngo", "NGO"),
            ("donor", "Donor"),
            ("credit_agreement", "Credit Agreement"),
            ("other", "Other"),
        ],
        default="insurance",
        required=True,
        tracking=True,
    )
    partner_id = fields.Many2one("res.partner", string="Related Contact")
    tin_number = fields.Char(string="TIN Number")
    license_number = fields.Char()
    contact_person = fields.Char()
    phone = fields.Char()
    email = fields.Char()
    address = fields.Text()
    default_coverage_percent = fields.Float(
        string="Default Coverage %",
        default=0.0,
    )
    requires_guarantee_letter = fields.Boolean(default=True)
    requires_pre_authorization = fields.Boolean(default=False)
    payment_terms_days = fields.Integer(string="Payment Terms (Days)", default=30)
    credit_limit = fields.Monetary()
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    @api.depends("name", "code")
    def _compute_display_name(self):
        for provider in self:
            if provider.code:
                provider.display_name = f"[{provider.code}] {provider.name}"
            else:
                provider.display_name = provider.name or ""

    @api.constrains("default_coverage_percent")
    def _check_default_coverage_percent(self):
        for provider in self:
            if not (0.0 <= provider.default_coverage_percent <= 100.0):
                raise ValidationError(
                    "Default coverage percent must be between 0 and 100."
                )

    @api.constrains("payment_terms_days")
    def _check_payment_terms_days(self):
        for provider in self:
            if provider.payment_terms_days < 0:
                raise ValidationError("Payment terms (days) cannot be negative.")

    @api.constrains("credit_limit")
    def _check_credit_limit(self):
        for provider in self:
            if provider.credit_limit < 0:
                raise ValidationError("Credit limit cannot be negative.")

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalBillingService(models.Model):
    _name = "hospital.billing.service"
    _description = "Hospital Billing Service"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char()
    service_type = fields.Selection(
        [
            ("consultation", "Consultation"),
            ("laboratory", "Laboratory"),
            ("radiology", "Radiology"),
            ("pharmacy", "Pharmacy"),
            ("procedure", "Procedure"),
            ("admission", "Admission"),
            ("nursing", "Nursing"),
            ("other", "Other"),
        ],
        required=True,
    )
    default_price = fields.Float(string="Default Price", digits=(16, 2))
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    description = fields.Text()
    active = fields.Boolean(default=True)

    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    uom_id = fields.Many2one("uom.uom", string="Unit of Measure")

    # --- Tax / fiscal configuration -----------------------------------
    tax_treatment = fields.Selection(
        [
            ("exempt", "Exempt"),
            ("standard", "Standard Rated"),
            ("zero_rated", "Zero Rated"),
            ("out_of_scope", "Out of Scope"),
        ],
        default="exempt",
        required=True,
        help="Snapshotted onto each charge line at creation time.",
    )
    tax_rate = fields.Float(string="Tax Rate (%)", default=0.0)
    tax_category = fields.Char()
    exemption_code = fields.Char()
    legal_reference = fields.Char()
    fiscal_item_code = fields.Char(help="Code reported to the fiscal device / tax authority.")

    effective_date_start = fields.Date()
    effective_date_end = fields.Date()

    # --- Billing behaviour --------------------------------------------
    is_default_consultation = fields.Boolean(
        string="Default Consultation Service",
        help="Used by the billing engine to charge an appointment consultation. "
        "At most one per company.",
    )
    fixed_fee = fields.Boolean(help="Quantity is always 1 and the price cannot be overridden per charge.")
    prepayment_required = fields.Boolean()
    coverage_auth_required = fields.Boolean(
        string="Authorization Required",
        help="Charges for this service start in Pending authorization.",
    )
    qty_overdelivery_policy = fields.Selection(
        [
            ("block", "Block"),
            ("warn", "Warn"),
            ("allow", "Allow"),
        ],
        default="block",
        string="Over-delivery Policy",
    )
    discount_policy = fields.Selection(
        [
            ("none", "No Discount"),
            ("limited", "Limited"),
            ("free", "Unrestricted"),
        ],
        default="none",
    )
    max_discount_percent = fields.Float(string="Max Discount (%)", default=0.0)

    @api.depends("code", "name")
    def _compute_display_name(self):
        for service in self:
            if service.code:
                service.display_name = f"[{service.code}] {service.name}"
            else:
                service.display_name = service.name or "New Service"

    @api.constrains("tax_treatment", "tax_rate")
    def _check_tax_rate(self):
        for service in self:
            if service.tax_rate < 0:
                raise ValidationError(f"Service {service.name}: tax rate cannot be negative.")
            if service.tax_treatment != "standard" and service.tax_rate:
                raise ValidationError(
                    f"Service {service.name}: a tax rate may only be set when the tax treatment "
                    "is Standard Rated."
                )

    @api.constrains("effective_date_start", "effective_date_end")
    def _check_effective_dates(self):
        for service in self:
            start, end = service.effective_date_start, service.effective_date_end
            if start and end and end < start:
                raise ValidationError(f"Service {service.name}: the end date precedes the start date.")

    @api.constrains("discount_policy", "max_discount_percent")
    def _check_discount_policy(self):
        for service in self:
            if not 0.0 <= service.max_discount_percent <= 100.0:
                raise ValidationError(
                    f"Service {service.name}: the maximum discount must be between 0 and 100 percent."
                )
            if service.discount_policy == "limited" and not service.max_discount_percent:
                raise ValidationError(
                    f"Service {service.name}: a limited discount policy requires a maximum discount percentage."
                )

    @api.onchange("tax_treatment")
    def _onchange_tax_treatment(self):
        if self.tax_treatment != "standard":
            self.tax_rate = 0.0

    @api.constrains("is_default_consultation", "service_type", "company_id", "active")
    def _check_default_consultation(self):
        for service in self:
            if not service.is_default_consultation:
                continue
            if service.service_type != "consultation":
                raise ValidationError(
                    f"Service {service.name}: only a consultation-type service can be the "
                    "default consultation service."
                )
            clash = self.search(
                [
                    ("is_default_consultation", "=", True),
                    ("service_type", "=", "consultation"),
                    ("company_id", "=", service.company_id.id),
                    ("id", "!=", service.id),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    f"'{clash.display_name}' is already the default consultation service for this "
                    "company. Only one default is allowed."
                )

    @api.model
    def get_default_consultation_service(self, company=None):
        """Resolve the consultation service to charge. Never guesses.

        Explicit configuration wins. If none is flagged, fall back only when the
        choice is UNAMBIGUOUS (exactly one active consultation service); otherwise
        raise, so a mis-billed consultation is impossible.
        """
        company = company or self.env.company
        domain = [("service_type", "=", "consultation"), ("company_id", "in", [company.id, False])]
        flagged = self.search(domain + [("is_default_consultation", "=", True)], limit=2)
        if len(flagged) == 1:
            return flagged
        if len(flagged) > 1:
            raise UserError(
                "More than one default consultation service is configured. "
                "Exactly one must be flagged as the default."
            )
        candidates = self.search(domain, limit=2)
        if len(candidates) == 1:
            return candidates
        if not candidates:
            raise UserError(
                "No consultation billing service is configured. Create one and flag it as the "
                "default consultation service."
            )
        raise UserError(
            "Several consultation services exist and none is flagged as the default. "
            "Flag exactly one so consultations are billed deterministically."
        )

    def is_effective_on(self, target_date=None):
        """True when the service price/tax configuration is valid on the given date."""
        self.ensure_one()
        target_date = target_date or fields.Date.context_today(self)
        if self.effective_date_start and target_date < self.effective_date_start:
            return False
        if self.effective_date_end and target_date > self.effective_date_end:
            return False
        return True

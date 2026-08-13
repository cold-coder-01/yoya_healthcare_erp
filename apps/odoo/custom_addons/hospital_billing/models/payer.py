"""Canonical payer master for the LIVE encounter billing chain.

WHY THIS LIVES IN hospital_billing AND NOT IN hospital_insurance
---------------------------------------------------------------
hospital_insurance DEPENDS ON hospital_billing. The charge line, the billing
account and the billing engine all live here, so for a future allocate_payer()
to resolve a payer from inside this module it would have to import a model
declared downstream -- a dependency cycle Odoo cannot load.

hospital.insurance.provider is therefore deliberately NOT reused. It is
referenced only as untyped provenance (see legacy_provider_id), which creates no
module dependency of any kind.

WHAT IS DELIBERATELY ABSENT
---------------------------
default_coverage_percent, credit_limit, payment_terms_days,
requires_guarantee_letter, requires_pre_authorization.

Every one of those is a TERM OF AN AGREEMENT, not an attribute of the payer's
identity. Keeping them here would make them unversionable: amending a contract
would silently rewrite the terms of every historical visit that used it. They
live on hospital.payer.agreement, which is versioned and frozen once active.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# Copied value-for-value from hospital.insurance.provider.payer_type so that a
# later, manually-reviewed migration is a straight key map with no translation
# table and no lossy guesswork.
PAYER_TYPES = [
    ("insurance", "Insurance Company"),
    ("corporate", "Corporate Employer"),
    ("government_program", "Government Program"),
    ("ngo", "NGO"),
    ("donor", "Donor"),
    ("credit_agreement", "Credit Agreement"),
    ("other", "Other"),
]


class HospitalPayer(models.Model):
    _name = "hospital.payer"
    _description = "Hospital Payer (Insurance / Corporate / Government / NGO / Donor)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(tracking=True)
    payer_type = fields.Selection(
        PAYER_TYPES,
        required=True,
        default="insurance",
        tracking=True,
    )

    # REQUIRED, and that is the point.
    #
    # hospital_billing_accounting._resolve_invoice_partner() already routes a
    # non-self-pay billing account's receivable to account.payer_id. A payer with
    # no commercial partner is a payer the hospital can never invoice, so the
    # gap is refused at creation rather than discovered at posting time.
    partner_id = fields.Many2one(
        "res.partner",
        string="Commercial Partner",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
        help="The res.partner that carries this payer's receivable. Invoices for "
        "the payer share are raised against it.",
    )

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )

    tin_number = fields.Char(string="TIN Number")
    license_number = fields.Char()
    contact_person = fields.Char()
    phone = fields.Char()
    email = fields.Char()
    address = fields.Text()

    # PROVENANCE ONLY -- deliberately NOT a Many2one.
    #
    # A Many2one to hospital.insurance.provider would add hospital_insurance to
    # this module's dependency closure and invert the graph. This is a plain
    # integer holding the legacy row id, resolved (if ever needed) by a reporting
    # script that may legitimately import both modules. Nothing in hospital_billing
    # ever dereferences it.
    legacy_provider_id = fields.Integer(
        string="Legacy Provider ID",
        readonly=True,
        copy=False,
        index=True,
        help="Row id of the originating hospital.insurance.provider record, when "
        "this payer was produced by the manual legacy migration. Untyped on "
        "purpose: hospital_billing must not depend on hospital_insurance. "
        "Provenance only -- never read by billing logic.",
    )

    agreement_ids = fields.One2many(
        "hospital.payer.agreement",
        "payer_id",
        string="Agreements",
    )
    agreement_count = fields.Integer(compute="_compute_agreement_count")

    active = fields.Boolean(default=True)
    notes = fields.Text()

    _sql_constraints = [
        (
            "payer_partner_company_unique",
            "unique(partner_id, company_id)",
            "This commercial partner is already registered as a payer for this company.",
        ),
        # PostgreSQL treats NULLs as DISTINCT in a UNIQUE constraint on every
        # supported version, so any number of payers may have no code. The blank
        # normalisation in create()/write() is what makes that true in practice:
        # without it two payers could both store '' and collide.
        (
            "payer_code_company_unique",
            "unique(code, company_id)",
            "A payer with this code already exists for this company.",
        ),
    ]

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("name", "code")
    def _compute_display_name(self):
        for payer in self:
            if payer.code:
                payer.display_name = "[%s] %s" % (payer.code, payer.name or "")
            else:
                payer.display_name = payer.name or ""

    @api.depends("agreement_ids")
    def _compute_agreement_count(self):
        for payer in self:
            payer.agreement_count = len(payer.agreement_ids)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("partner_id", "company_id")
    def _check_partner_company(self):
        for payer in self:
            partner_company = payer.partner_id.company_id
            if partner_company and partner_company != payer.company_id:
                raise ValidationError(
                    "Payer %s belongs to company %s but its commercial partner "
                    "belongs to %s. An invoice raised against it would cross "
                    "companies."
                    % (
                        payer.name,
                        payer.company_id.display_name,
                        partner_company.display_name,
                    )
                )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @staticmethod
    def _normalise_code(vals):
        """Blank -> NULL, so the unique(code, company_id) constraint means
        'unique when present' rather than 'at most one payer without a code'."""
        if "code" in vals:
            vals["code"] = (vals.get("code") or "").strip() or False
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._normalise_code(vals)
        return super().create(vals_list)

    def write(self, vals):
        return super().write(self._normalise_code(dict(vals)))

    def unlink(self):
        for payer in self:
            if payer.agreement_ids:
                raise UserError(
                    "Payer %s has %d agreement(s) and cannot be deleted. Archive "
                    "it instead so its history stays readable."
                    % (payer.display_name, len(payer.agreement_ids))
                )
        return super().unlink()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_view_agreements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Payer Agreements",
            "res_model": "hospital.payer.agreement",
            "view_mode": "list,form",
            "domain": [("payer_id", "=", self.id)],
            "context": {
                "default_payer_id": self.id,
                "default_company_id": self.company_id.id,
            },
        }

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HospitalInsuranceAccountingConfig(models.Model):
    _name = "hospital.insurance.accounting.config"
    _description = "Hospital Insurance Accounting Configuration"
    _order = "company_id, provider_type"

    # Provider type values mirror hospital.insurance.provider.payer_type.
    PROVIDER_TYPES = [
        ("insurance", "Insurance Company"),
        ("corporate", "Corporate Employer"),
        ("government_program", "Government Program"),
        ("ngo", "NGO"),
        ("donor", "Donor"),
        ("credit_agreement", "Credit Agreement"),
        ("other", "Other"),
    ]

    # Maps provider_type -> the account field that holds its payer receivable.
    _PROVIDER_ACCOUNT_FIELDS = {
        "insurance": "insurance_receivable_account_id",
        "corporate": "corporate_receivable_account_id",
        "government_program": "government_receivable_account_id",
        "ngo": "ngo_receivable_account_id",
        "donor": "donor_receivable_account_id",
        "credit_agreement": "credit_receivable_account_id",
        "other": "other_receivable_account_id",
    }

    name = fields.Char(required=True, default="Insurance Accounting Configuration")
    provider_type = fields.Selection(
        PROVIDER_TYPES,
        required=True,
        help="Payer type (mirrors the provider payer_type) this configuration "
        "applies to.",
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)

    # Credit side: the generic patient receivable the payer share is moved out of.
    patient_receivable_account_id = fields.Many2one(
        "account.account",
        string="Patient Receivable Account",
        required=True,
    )

    # Debit side candidates: dedicated payer receivable accounts.
    insurance_receivable_account_id = fields.Many2one(
        "account.account", string="Insurance Receivable Account"
    )
    corporate_receivable_account_id = fields.Many2one(
        "account.account", string="Corporate Receivable Account"
    )
    government_receivable_account_id = fields.Many2one(
        "account.account", string="Government Program Receivable Account"
    )
    ngo_receivable_account_id = fields.Many2one(
        "account.account", string="NGO Receivable Account"
    )
    donor_receivable_account_id = fields.Many2one(
        "account.account", string="Donor Receivable Account"
    )
    credit_receivable_account_id = fields.Many2one(
        "account.account", string="Credit Agreement Receivable Account"
    )
    other_receivable_account_id = fields.Many2one(
        "account.account", string="Other Receivable Account"
    )

    writeoff_expense_account_id = fields.Many2one(
        "account.account",
        string="Write-off Expense Account",
        help="Expense account used when a rejected claim amount is absorbed "
        "(written off) by the hospital.",
    )

    journal_id = fields.Many2one(
        "account.journal",
        string="Reclassification Journal",
        required=True,
        help="Journal used when posting the receivable reclassification entry.",
    )
    notes = fields.Text()

    _sql_constraints = [
        (
            "unique_company_provider_type",
            "unique(company_id, provider_type)",
            "An insurance accounting configuration already exists for this "
            "company and provider type.",
        ),
    ]

    @api.depends("company_id", "provider_type")
    def _compute_display_name(self):
        type_labels = dict(self.PROVIDER_TYPES)
        for config in self:
            company = config.company_id.name or ""
            provider = type_labels.get(
                config.provider_type, config.provider_type or ""
            )
            config.display_name = f"{company} / {provider}".strip(" /")

    @api.constrains("company_id", "provider_type", "active")
    def _check_unique_active_config(self):
        for config in self:
            if not config.active:
                continue
            domain = [
                ("company_id", "=", config.company_id.id),
                ("provider_type", "=", config.provider_type),
                ("active", "=", True),
                ("id", "!=", config.id),
            ]
            if self.search_count(domain):
                raise ValidationError(
                    "Only one active insurance accounting configuration is "
                    "allowed per company and provider type."
                )

    @api.constrains("provider_type")
    def _check_payer_account_configured(self):
        for config in self:
            field_name = self._PROVIDER_ACCOUNT_FIELDS.get(config.provider_type)
            if field_name and not config[field_name]:
                label = dict(self.PROVIDER_TYPES).get(
                    config.provider_type, config.provider_type
                )
                raise ValidationError(
                    "A payer receivable account must be configured for provider "
                    "type '%s'." % label
                )

    @api.constrains(
        "company_id",
        "patient_receivable_account_id",
        "insurance_receivable_account_id",
        "corporate_receivable_account_id",
        "government_receivable_account_id",
        "ngo_receivable_account_id",
        "donor_receivable_account_id",
        "credit_receivable_account_id",
        "other_receivable_account_id",
        "writeoff_expense_account_id",
        "journal_id",
    )
    def _check_company_consistency(self):
        account_fields = [
            "patient_receivable_account_id",
            "writeoff_expense_account_id",
        ] + list(self._PROVIDER_ACCOUNT_FIELDS.values())
        for config in self:
            company = config.company_id
            for field_name in account_fields:
                account = config[field_name]
                # account.account is multi-company in Odoo 18 (company_ids).
                # An empty company_ids means the account is shared across all
                # companies, so only flag a genuine cross-company mismatch.
                if account and account.company_ids and company not in account.company_ids:
                    raise ValidationError(
                        "Account '%s' does not belong to company '%s'."
                        % (account.display_name, company.name)
                    )
            journal = config.journal_id
            if journal and journal.company_id and journal.company_id != company:
                raise ValidationError(
                    "Journal '%s' does not belong to company '%s'."
                    % (journal.display_name, company.name)
                )

    @api.constrains(
        "patient_receivable_account_id",
        "insurance_receivable_account_id",
        "corporate_receivable_account_id",
        "government_receivable_account_id",
        "ngo_receivable_account_id",
        "donor_receivable_account_id",
        "credit_receivable_account_id",
        "other_receivable_account_id",
    )
    def _check_receivable_account_type(self):
        """Warn (block) when a configured account is clearly not an asset/receivable.

        Only enforced when the account_type field is available and populated so
        the module stays tolerant of custom charts of accounts.
        """
        allowed_prefixes = ("asset", "liability")
        account_fields = [
            "patient_receivable_account_id",
        ] + list(self._PROVIDER_ACCOUNT_FIELDS.values())
        for config in self:
            for field_name in account_fields:
                account = config[field_name]
                if not account:
                    continue
                account_type = getattr(account, "account_type", False)
                if account_type and not account_type.startswith(allowed_prefixes):
                    raise ValidationError(
                        "Account '%s' should be a receivable / asset type "
                        "account." % account.display_name
                    )

    @api.constrains("writeoff_expense_account_id")
    def _check_writeoff_account_type(self):
        for config in self:
            account = config.writeoff_expense_account_id
            if not account:
                continue
            account_type = getattr(account, "account_type", False)
            if account_type and not account_type.startswith("expense"):
                raise ValidationError(
                    "Account '%s' should be an expense type account for "
                    "claim write-offs." % account.display_name
                )

    def get_payer_receivable_account(self, provider_type):
        """Return the configured payer receivable account for a provider type."""
        self.ensure_one()
        field_name = self._PROVIDER_ACCOUNT_FIELDS.get(provider_type)
        if not field_name:
            return self.env["account.account"]
        return self[field_name]

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
CONFIG_GROUPS = (G_ACCOUNTANT, G_MANAGER, G_ADMIN)


class HospitalBillingAccountingConfig(models.Model):
    _name = "hospital.billing.accounting.config"
    _description = "Hospital Billing Accounting Configuration"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "company_id, source_type"

    # Source type values mirror hospital.patient.bill.line.source_type.
    # "surgery" is contributed by the hospital_operation_theatre bridge
    # (Task 32B) so theatre charges can be mapped to a Surgery Revenue account.
    SOURCE_TYPES = [
        ("consultation", "Consultation"),
        ("laboratory", "Laboratory"),
        ("radiology", "Radiology"),
        ("pharmacy", "Pharmacy"),
        ("admission", "Admission"),
        ("procedure", "Procedure"),
        ("surgery", "Surgery"),
        ("other", "Other"),
    ]

    name = fields.Char(required=True, default="Accounting Configuration", tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
    )
    active = fields.Boolean(default=True, tracking=True)
    source_type = fields.Selection(
        SOURCE_TYPES,
        required=True,
        tracking=True,
        help="Hospital bill line source type this configuration applies to.",
    )

    # Accounts used for bill posting
    receivable_account_id = fields.Many2one(
        "account.account",
        string="Patient Receivable Account",
        required=True,
        check_company=True,
        tracking=True,
    )
    revenue_account_id = fields.Many2one(
        "account.account",
        string="Revenue Account",
        required=True,
        check_company=True,
        tracking=True,
    )

    # Accounts used for payment posting (per payment method)
    cash_account_id = fields.Many2one(
        "account.account", string="Cash Account", check_company=True, tracking=True
    )
    bank_account_id = fields.Many2one(
        "account.account", string="Bank Account", check_company=True, tracking=True
    )
    mobile_money_account_id = fields.Many2one(
        "account.account",
        string="Mobile Money Account",
        check_company=True,
        tracking=True,
    )

    journal_id = fields.Many2one(
        "account.journal",
        string="Bill Journal",
        required=True,
        check_company=True,
        tracking=True,
        help="Journal used when posting the bill journal entry.",
    )
    invoice_journal_id = fields.Many2one(
        "account.journal",
        string="Customer Invoice Journal",
        check_company=True,
        tracking=True,
        domain="[('type', '=', 'sale')]",
        help="Sale journal used only for standard Odoo customer invoices. The legacy "
        "manual-entry journal is not reused.",
    )
    payment_journal_id = fields.Many2one(
        "account.journal",
        string="Payment Journal",
        help="Journal used when posting payments. Falls back to Bill Journal if empty.",
        check_company=True,
        tracking=True,
    )
    patient_advance_liability_account_id = fields.Many2one(
        "account.account",
        string="Patient Advance Liability",
        check_company=True,
        tracking=True,
        domain="[('account_type', '=', 'liability_current')]",
        help="Reconciliable current liability credited when a patient advance receipt "
        "is accounted. Task 32B-1 does not post that entry.",
    )
    patient_credit_liability_account_id = fields.Many2one(
        "account.account",
        string="Patient Credit / Refundable Advance Liability",
        check_company=True,
        tracking=True,
        domain="[('account_type', '=', 'liability_current')]",
        help="Separate reconciliable current liability for patient credits and amounts "
        "approved as refundable; ordinary unapplied advances remain in Patient Advances.",
    )
    advance_receipt_journal_id = fields.Many2one(
        "account.journal",
        string="Advance Receipt Journal",
        check_company=True,
        tracking=True,
        help="Journal reserved for future Dr liquidity / Cr Patient Advances entries.",
    )
    advance_application_journal_id = fields.Many2one(
        "account.journal",
        string="Advance Application / Clearing Journal",
        check_company=True,
        tracking=True,
        help="Journal reserved for future Dr Patient Advances / Cr Receivable application.",
    )
    advance_refund_journal_id = fields.Many2one(
        "account.journal",
        string="Advance Refund Journal",
        check_company=True,
        tracking=True,
        help="Journal reserved for future approved advance or patient-credit refunds.",
    )
    advance_refund_clearing_account_id = fields.Many2one(
        "account.account",
        string="Advance Refund Clearing Account",
        check_company=True,
        tracking=True,
        help="Optional reconciliable clearing account when an external refund provider "
        "requires an intermediate settlement step. Direct refunds leave this empty.",
    )
    inventory_valuation_journal_id = fields.Many2one(
        "account.journal",
        string="Inventory Valuation Journal",
        check_company=True,
        tracking=True,
        domain="[('type', '=', 'general')]",
        help="Journal used for explicit Dr COGS / Cr inventory valuation entries.",
    )
    inventory_asset_account_id = fields.Many2one(
        "account.account",
        string="Inventory Asset Account",
        check_company=True,
        tracking=True,
        domain="[('account_type', 'in', ('asset_current', 'asset_non_current'))]",
        help="Asset account credited when consumed pharmacy stock is valued.",
    )
    cogs_account_id = fields.Many2one(
        "account.account",
        string="COGS / Expense Account",
        check_company=True,
        tracking=True,
        domain="[('account_type', 'in', ('expense', 'expense_depreciation', 'expense_direct_cost'))]",
        help="Expense account debited for consumed pharmacy inventory cost.",
    )
    advance_configuration_complete = fields.Boolean(
        compute="_compute_advance_configuration_status",
        string="Advance Configuration Complete",
    )
    advance_configuration_message = fields.Text(
        compute="_compute_advance_configuration_status",
        string="Advance Configuration Validation",
    )
    notes = fields.Text()

    _sql_constraints = [
        (
            "unique_company_source_type",
            "unique(company_id, source_type)",
            "An accounting configuration already exists for this company and source type.",
        ),
    ]

    @api.depends("company_id", "source_type")
    def _compute_display_name(self):
        source_labels = dict(self.SOURCE_TYPES)
        for config in self:
            company = config.company_id.name or ""
            source = source_labels.get(config.source_type, config.source_type or "")
            config.display_name = f"{company} / {source}".strip(" /")

    def _advance_configuration_errors(self):
        self.ensure_one()
        errors = []
        mandatory = (
            ("cash_account_id", "Cash account"),
            ("bank_account_id", "Bank/card account"),
            ("mobile_money_account_id", "Mobile-money account"),
            ("patient_advance_liability_account_id", "Patient Advance liability"),
            ("patient_credit_liability_account_id", "Patient Credit liability"),
            ("advance_receipt_journal_id", "Advance Receipt journal"),
            ("advance_application_journal_id", "Advance Application journal"),
            ("advance_refund_journal_id", "Advance Refund journal"),
        )
        for field_name, label in mandatory:
            if not self[field_name]:
                errors.append(f"{label} is missing")

        for field_name, label in (
            ("patient_advance_liability_account_id", "Patient Advance liability"),
            ("patient_credit_liability_account_id", "Patient Credit liability"),
        ):
            account = self[field_name]
            if account and account.account_type != "liability_current":
                errors.append(f"{label} must use account type Current Liabilities")
            if account and not account.reconcile:
                errors.append(f"{label} must allow reconciliation")

        clearing = self.advance_refund_clearing_account_id
        if clearing and clearing.account_type not in ("asset_current", "liability_current"):
            errors.append("Advance Refund clearing must be a current asset or liability")
        if clearing and not clearing.reconcile:
            errors.append("Advance Refund clearing must allow reconciliation")

        for field_name, label in (
            ("advance_receipt_journal_id", "Advance Receipt journal"),
            ("advance_application_journal_id", "Advance Application journal"),
            ("advance_refund_journal_id", "Advance Refund journal"),
        ):
            journal = self[field_name]
            if journal and journal.type not in ("general", "cash", "bank"):
                errors.append(f"{label} must be a general, cash or bank journal")
        return errors

    @api.depends(
        "cash_account_id",
        "bank_account_id",
        "mobile_money_account_id",
        "patient_advance_liability_account_id",
        "patient_advance_liability_account_id.account_type",
        "patient_advance_liability_account_id.reconcile",
        "patient_credit_liability_account_id",
        "patient_credit_liability_account_id.account_type",
        "patient_credit_liability_account_id.reconcile",
        "advance_receipt_journal_id",
        "advance_receipt_journal_id.type",
        "advance_application_journal_id",
        "advance_application_journal_id.type",
        "advance_refund_journal_id",
        "advance_refund_journal_id.type",
        "advance_refund_clearing_account_id",
        "advance_refund_clearing_account_id.account_type",
        "advance_refund_clearing_account_id.reconcile",
    )
    def _compute_advance_configuration_status(self):
        for config in self:
            errors = config._advance_configuration_errors()
            config.advance_configuration_complete = not errors
            config.advance_configuration_message = (
                "Configuration complete. No accounting entry is posted by Task 32B-1."
                if not errors
                else "\n".join(f"- {error}" for error in errors)
            )

    def _assert_advance_configuration(self):
        """Future 32B-3 posting entry point: fail closed on incomplete mappings."""
        self.ensure_one()
        errors = self._advance_configuration_errors()
        if errors:
            raise UserError(
                "Patient-advance accounting configuration is incomplete:\n- "
                + "\n- ".join(errors)
            )
        return True

    def _invoice_configuration_errors(self):
        self.ensure_one()
        errors = []
        if not self.receivable_account_id:
            errors.append("Patient receivable account is missing")
        elif self.receivable_account_id.account_type != "asset_receivable":
            errors.append("Patient receivable must use account type Receivable")
        elif not self.receivable_account_id.reconcile:
            errors.append("Patient receivable must allow reconciliation")
        if not self.revenue_account_id:
            errors.append("Revenue account is missing")
        elif self.revenue_account_id.account_type not in ("income", "income_other"):
            errors.append("Revenue account must use an Income account type")
        if not self.invoice_journal_id:
            errors.append("Customer invoice journal is missing")
        elif self.invoice_journal_id.type != "sale":
            errors.append("Customer invoice journal must be a Sales journal")
        return errors

    def _assert_invoice_configuration(self):
        self.ensure_one()
        errors = self._invoice_configuration_errors()
        if errors:
            raise UserError(
                "Delivered-charge invoice configuration is incomplete for %s:\n- %s"
                % (self.display_name, "\n- ".join(errors))
            )
        return True

    def _inventory_valuation_configuration_errors(self):
        self.ensure_one()
        errors = []
        if not self.inventory_valuation_journal_id:
            errors.append("Inventory valuation journal is missing")
        elif self.inventory_valuation_journal_id.type != "general":
            errors.append("Inventory valuation journal must be a general journal")
        if not self.inventory_asset_account_id:
            errors.append("Inventory asset account is missing")
        elif self.inventory_asset_account_id.account_type not in ("asset_current", "asset_non_current"):
            errors.append("Inventory asset account must be an asset account")
        if not self.cogs_account_id:
            errors.append("COGS / expense account is missing")
        elif self.cogs_account_id.account_type not in ("expense", "expense_depreciation", "expense_direct_cost"):
            errors.append("COGS account must be an expense account")
        return errors

    def _assert_inventory_valuation_configuration(self):
        self.ensure_one()
        errors = self._inventory_valuation_configuration_errors()
        if errors:
            raise UserError(
                "Inventory valuation accounting configuration is incomplete for %s:\n- %s"
                % (self.display_name, "\n- ".join(errors))
            )
        return True

    def action_validate_advance_configuration(self):
        for config in self:
            config._assert_advance_configuration()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Patient-advance configuration",
                "message": "Configuration is complete. No accounting entry was created.",
                "type": "success",
                "sticky": False,
            },
        }

    def _assert_configuration_group(self, action, admin_only=False):
        if self.env.su:
            return
        groups = (G_ADMIN,) if admin_only else CONFIG_GROUPS
        if not any(self.env.user.has_group(group) for group in groups):
            raise AccessError(
                f"You are not authorized to {action} hospital accounting mappings."
            )

    @api.model_create_multi
    def create(self, vals_list):
        self._assert_configuration_group("create")
        return super().create(vals_list)

    def write(self, vals):
        self._assert_configuration_group("change")
        return super().write(vals)

    def unlink(self):
        self._assert_configuration_group("delete", admin_only=True)
        return super().unlink()

    @api.constrains("company_id", "source_type", "active")
    def _check_unique_active_config(self):
        for config in self:
            if not config.active:
                continue
            domain = [
                ("company_id", "=", config.company_id.id),
                ("source_type", "=", config.source_type),
                ("active", "=", True),
                ("id", "!=", config.id),
            ]
            if self.search_count(domain):
                raise ValidationError(
                    "Only one active accounting configuration is allowed per "
                    "company and source type."
                )

    @api.constrains(
        "company_id",
        "receivable_account_id",
        "revenue_account_id",
        "cash_account_id",
        "bank_account_id",
        "mobile_money_account_id",
        "patient_advance_liability_account_id",
        "patient_credit_liability_account_id",
        "advance_refund_clearing_account_id",
        "inventory_asset_account_id",
        "cogs_account_id",
        "journal_id",
        "invoice_journal_id",
        "payment_journal_id",
        "advance_receipt_journal_id",
        "advance_application_journal_id",
        "advance_refund_journal_id",
        "inventory_valuation_journal_id",
    )
    def _check_mapping_companies(self):
        account_fields = (
            "receivable_account_id",
            "revenue_account_id",
            "cash_account_id",
            "bank_account_id",
            "mobile_money_account_id",
            "patient_advance_liability_account_id",
            "patient_credit_liability_account_id",
            "advance_refund_clearing_account_id",
            "inventory_asset_account_id",
            "cogs_account_id",
        )
        journal_fields = (
            "journal_id",
            "invoice_journal_id",
            "payment_journal_id",
            "advance_receipt_journal_id",
            "advance_application_journal_id",
            "advance_refund_journal_id",
            "inventory_valuation_journal_id",
        )
        for config in self:
            for field_name in account_fields:
                account = config[field_name]
                if account and config.company_id not in account.company_ids:
                    raise ValidationError(
                        f"{account.display_name} is not available to {config.company_id.name}."
                    )
            for field_name in journal_fields:
                journal = config[field_name]
                if journal and journal.company_id != config.company_id:
                    raise ValidationError(
                        f"{journal.display_name} belongs to another company."
                    )

import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.hospital_billing.models.charge_line import (
    ALLOCATION_REASON_CTX,
    ALLOCATION_REFERENCE_CTX,
)

from .invoice_batch import assert_invoice_authorized


AMOUNT_TOLERANCE = 0.005
CONTROLLED_CTX = "hospital_advance_accounting_controlled"


class HospitalChargeReceiptAccounting(models.Model):
    _inherit = "hospital.charge.receipt"

    payment_method = fields.Selection(
        selection_add=[("cheque", "Cheque")],
        ondelete={"cheque": "set default"},
        default="cash",
    )
    payment_origin = fields.Selection(
        [("manual", "Manual"), ("external_fiscal_device", "External Fiscal Device")],
        default="manual",
        readonly=True,
        copy=False,
        index=True,
    )
    payment_provider = fields.Char(readonly=True, copy=False, index=True)
    external_device_identity = fields.Char(readonly=True, copy=False)
    external_transaction_reference = fields.Char(readonly=True, copy=False, index=True)
    fiscal_receipt_number = fields.Char(readonly=True, copy=False, index=True)
    accounting_move_id = fields.Many2one(
        "account.move", string="Receipt Accounting Entry", readonly=True, copy=False, ondelete="restrict"
    )
    accounting_posted_at = fields.Datetime(readonly=True, copy=False)
    accounting_posted_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    advance_application_ids = fields.Many2many(
        "hospital.patient.advance.application",
        "hospital_advance_application_receipt_rel",
        "receipt_id",
        "application_id",
        string="Advance Applications",
        readonly=True,
        copy=False,
    )

    @api.constrains(
        "company_id",
        "payment_provider",
        "payment_reference",
        "external_transaction_reference",
        "fiscal_receipt_number",
    )
    def _check_duplicate_external_references(self):
        for receipt in self:
            company = receipt.company_id or self.env.company
            provider = receipt.payment_provider or receipt.payment_method or "manual"
            for field_name, value in (
                ("payment_reference", receipt.payment_reference),
                ("external_transaction_reference", receipt.external_transaction_reference),
                ("fiscal_receipt_number", receipt.fiscal_receipt_number),
            ):
                value = (value or "").strip()
                if not value:
                    continue
                duplicate = self.search(
                    [
                        ("id", "!=", receipt.id),
                        ("company_id", "=", company.id),
                        ("payment_provider", "=", provider),
                        (field_name, "=", value),
                    ],
                    limit=1,
                )
                if duplicate:
                    raise ValidationError(
                        "Receipt reference %s is already used by %s for provider %s."
                        % (value, duplicate.name, provider)
                    )

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("hospital_external_receipt_idempotent") and len(vals_list) == 1:
            vals = vals_list[0]
            provider = vals.get("payment_provider") or vals.get("payment_method") or "manual"
            ref_field = (
                "external_transaction_reference"
                if vals.get("external_transaction_reference")
                else "fiscal_receipt_number"
                if vals.get("fiscal_receipt_number")
                else False
            )
            if ref_field:
                existing = self.sudo().search(
                    [
                        ("company_id", "=", vals.get("company_id") or self.env.company.id),
                        ("payment_provider", "=", provider),
                        (ref_field, "=", vals[ref_field]),
                    ],
                    limit=1,
                )
                if existing:
                    return existing
        for vals in vals_list:
            vals.setdefault("payment_provider", vals.get("payment_method") or "manual")
        return super().create(vals_list)

    def write(self, vals):
        protected = {
            "accounting_move_id",
            "accounting_posted",
            "accounting_reference",
            "accounting_posted_at",
            "accounting_posted_by_id",
            "advance_application_ids",
            "payment_origin",
            "payment_provider",
            "external_device_identity",
            "external_transaction_reference",
            "fiscal_receipt_number",
        }
        if protected & set(vals) and not (self.env.su and self.env.context.get(CONTROLLED_CTX)):
            raise AccessError("Receipt accounting and external-payment provenance are controlled fields.")
        if "state" in vals:
            for receipt in self:
                if receipt.accounting_move_id:
                    raise UserError("Posted receipt %s cannot have its state changed." % receipt.name)
        return super().write(vals)

    def unlink(self):
        if any(receipt.accounting_move_id for receipt in self):
            raise UserError("Posted receipts cannot be deleted.")
        return super().unlink()

    def _get_advance_accounting_config(self):
        self.ensure_one()
        source_type = "other"
        charge = self.allocation_ids[:1].charge_line_id or self.charge_id
        if charge and charge.service_id and charge.service_id.service_type:
            source_type = charge.service_id.service_type
        config = self.env["hospital.billing.accounting.config"].search(
            [("company_id", "=", (self.company_id or self.env.company).id), ("source_type", "=", source_type), ("active", "=", True)],
            limit=1,
        )
        if not config:
            raise UserError("No patient-advance accounting configuration exists for %s." % source_type)
        config._assert_advance_configuration()
        return config

    def _resolve_liquidity_account(self, config):
        self.ensure_one()
        if self.payment_method == "cash":
            account = config.cash_account_id
        elif self.payment_method in ("mobile_money", "fiscal_terminal"):
            account = config.mobile_money_account_id
        else:
            account = config.bank_account_id
        if not account:
            raise UserError("No liquidity account is configured for %s." % self.payment_method)
        return account

    def action_post_receipt_accounting(self):
        assert_invoice_authorized(self.env, "post patient receipt accounting")
        result = True
        for receipt in self:
            receipt_sudo = receipt.sudo()
            if receipt_sudo.accounting_move_id:
                result = receipt_sudo.accounting_move_id.with_user(self.env.user)
                continue
            if receipt_sudo.state != "confirmed":
                raise UserError("Only confirmed receipts can be posted to accounting.")
            if receipt_sudo.amount <= AMOUNT_TOLERANCE:
                raise UserError("Receipt amount must be greater than zero.")
            config = receipt_sudo._get_advance_accounting_config()
            company = receipt_sudo.company_id or self.env.company
            currency = receipt_sudo.currency_id or company.currency_id
            partner = receipt_sudo.patient_id.accounting_partner_id.commercial_partner_id
            if not partner:
                raise UserError("Patient %s has no accounting partner." % receipt_sudo.patient_id.display_name)
            amount = currency.round(receipt_sudo.amount)
            liquidity = receipt_sudo._resolve_liquidity_account(config)
            move = self.env["account.move"].sudo().create(
                {
                    "move_type": "entry",
                    "date": fields.Date.context_today(receipt_sudo),
                    "journal_id": config.advance_receipt_journal_id.id,
                    "company_id": company.id,
                    "currency_id": currency.id,
                    "ref": "Patient Advance Receipt - %s" % receipt_sudo.name,
                    "line_ids": [
                        (0, 0, {"name": "Patient advance received - %s" % receipt_sudo.name, "account_id": liquidity.id, "partner_id": partner.id, "debit": amount, "credit": 0.0}),
                        (0, 0, {"name": "Patient advance liability - %s" % receipt_sudo.name, "account_id": config.patient_advance_liability_account_id.id, "partner_id": partner.id, "debit": 0.0, "credit": amount}),
                    ],
                }
            )
            move.action_post()
            receipt_sudo.with_context(**{CONTROLLED_CTX: True}).write(
                {
                    "accounting_move_id": move.id,
                    "accounting_posted": True,
                    "accounting_reference": move.name,
                    "accounting_posted_at": fields.Datetime.now(),
                    "accounting_posted_by_id": self.env.user.id,
                }
            )
            result = move.with_user(self.env.user)
        return result


class HospitalChargePaymentWizardAccounting(models.TransientModel):
    _inherit = "hospital.charge.payment.wizard"

    payment_origin = fields.Selection(
        [("manual", "Manual"), ("external_fiscal_device", "External Fiscal Device")], default="manual", required=True
    )
    payment_provider = fields.Char(default="manual")
    external_device_identity = fields.Char()
    external_transaction_reference = fields.Char()
    fiscal_receipt_number = fields.Char()

    def action_confirm(self):
        action = super().action_confirm()
        if self.receipt_id:
            self.receipt_id.sudo().with_context(**{CONTROLLED_CTX: True}).write(
                {
                    "payment_origin": self.payment_origin,
                    "payment_provider": self.payment_provider or self.payment_method or "manual",
                    "external_device_identity": self.external_device_identity,
                    "external_transaction_reference": self.external_transaction_reference,
                    "fiscal_receipt_number": self.fiscal_receipt_number,
                }
            )
            self.receipt_id.with_user(self.env.user).action_post_receipt_accounting()
        return action


class HospitalPatientAdvanceApplication(models.Model):
    _name = "hospital.patient.advance.application"
    _description = "Hospital Patient Advance Application"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default="New")
    request_token = fields.Char(required=True, readonly=True, copy=False, index=True)
    billing_account_id = fields.Many2one("hospital.billing.account", required=True, readonly=True, ondelete="restrict", index=True)
    invoice_id = fields.Many2one("account.move", required=True, readonly=True, ondelete="restrict", index=True)
    patient_id = fields.Many2one("hospital.patient", required=True, readonly=True, ondelete="restrict", index=True)
    partner_id = fields.Many2one("res.partner", required=True, readonly=True, ondelete="restrict", index=True)
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    currency_id = fields.Many2one("res.currency", required=True, readonly=True)
    amount = fields.Monetary(required=True, readonly=True, currency_field="currency_id")
    move_id = fields.Many2one("account.move", string="Application Entry", readonly=True, copy=False, ondelete="restrict")
    receipt_ids = fields.Many2many(
        "hospital.charge.receipt",
        "hospital_advance_application_receipt_rel",
        "application_id",
        "receipt_id",
        string="Source Receipts",
        readonly=True,
    )
    state = fields.Selection([("draft", "Draft"), ("posted", "Posted"), ("failed", "Failed")], default="draft", readonly=True, copy=False, index=True)
    error_summary = fields.Char(readonly=True, copy=False)
    applied_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    applied_at = fields.Datetime(readonly=True, copy=False)

    _sql_constraints = [
        ("application_token_company_unique", "unique(company_id, request_token)", "This advance application request token already exists for this company."),
        ("application_move_unique", "unique(move_id)", "This accounting entry is already linked to an advance application."),
        ("application_amount_positive", "CHECK(amount > 0)", "The application amount must be positive."),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not (self.env.su and self.env.context.get(CONTROLLED_CTX)):
            raise AccessError("Advance applications are created only by the controlled accounting service.")
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("hospital.patient.advance.application.sequence") or "New"
        return super().create(vals_list)

    def write(self, vals):
        if not (self.env.su and self.env.context.get(CONTROLLED_CTX)):
            raise AccessError("Advance applications are maintained only by the controlled accounting service.")
        return super().write(vals)

    def unlink(self):
        raise UserError("Advance application records are immutable and cannot be deleted.")


class HospitalBillingEngineAdvanceAccounting(models.AbstractModel):
    _inherit = "hospital.billing.engine"

    @api.model
    def _posted_invoice_receivable_lines(self, invoice):
        return invoice.line_ids.filtered(lambda line: line.account_id.account_type == "asset_receivable" and not line.reconciled)

    @api.model
    def _available_receipt_liability_lines(self, account, config, partner, source_receipts=None):
        if source_receipts is None:
            receipts = self.env["hospital.charge.receipt"].sudo().search(
                [
                    ("billing_account_id", "=", account.id),
                    ("patient_id", "=", account.patient_id.id),
                    ("company_id", "=", account.company_id.id),
                    ("currency_id", "=", account.currency_id.id),
                    ("accounting_move_id.state", "=", "posted"),
                ],
                order="received_at, id",
            )
        else:
            receipts = source_receipts.sudo().sorted(lambda receipt: (receipt.received_at or fields.Datetime.to_datetime("1970-01-01 00:00:00"), receipt.id))
            for receipt in receipts:
                if receipt.billing_account_id != account or receipt.patient_id != account.patient_id:
                    raise UserError("Receipt %s is not linked to the target billing account and patient." % receipt.name)
                if receipt.company_id != account.company_id or receipt.currency_id != account.currency_id:
                    raise UserError("Receipt %s belongs to another company or currency." % receipt.name)
                if receipt.state != "confirmed" or receipt.accounting_move_id.state != "posted":
                    raise UserError("Receipt %s is not a confirmed posted patient advance." % receipt.name)
        lines = self.env["account.move.line"]
        for receipt in receipts:
            lines |= receipt.accounting_move_id.line_ids.filtered(
                lambda line: line.account_id == config.patient_advance_liability_account_id
                and line.partner_id == partner
                and not line.reconciled
                and line.amount_residual < -AMOUNT_TOLERANCE
            )
        return lines.sorted(lambda line: (line.date, line.id))

    @api.model
    def _get_any_invoice_config(self, account):
        charge = account.charge_line_ids[:1]
        source_type = charge.service_id.service_type if charge and charge.service_id.service_type else "other"
        config = self.env["hospital.billing.accounting.config"].search(
            [("company_id", "=", account.company_id.id), ("source_type", "=", source_type), ("active", "=", True)], limit=1
        )
        if not config:
            raise UserError("No patient-advance accounting configuration exists for %s." % source_type)
        config._assert_advance_configuration()
        return config

    @api.model
    def apply_patient_advance_to_invoice(self, invoice, amount=None, request_token=None, source_receipts=None):
        assert_invoice_authorized(self.env, "apply patient advances to invoices")
        if isinstance(invoice, int):
            invoice = self.env["account.move"].browse(invoice)
        invoice.ensure_one()
        if invoice.state != "posted" or invoice.move_type != "out_invoice" or not invoice.hospital_managed_invoice:
            raise UserError("Patient advances may be applied only to posted hospital customer invoices.")
        account = invoice.hospital_billing_account_id
        if not account or invoice.hospital_patient_id != account.patient_id:
            raise UserError("Invoice is not linked to a consistent hospital billing account.")
        if invoice.company_id != account.company_id or invoice.currency_id != account.currency_id:
            raise UserError("Invoice company/currency differs from its billing account.")
        partner = account.patient_id.accounting_partner_id.commercial_partner_id
        if not partner or invoice.partner_id.commercial_partner_id != partner:
            raise UserError("Patient advance cannot be applied to another patient's invoice.")
        token = (request_token or uuid.uuid4().hex).strip()
        if not token:
            raise UserError("Advance application request token cannot be empty.")
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ["hospital.advance.application:%s:%s" % (invoice.company_id.id, token)])
        Application = self.env["hospital.patient.advance.application"].with_context(active_test=False)
        existing = Application.search([("company_id", "=", invoice.company_id.id), ("request_token", "=", token)], limit=1)
        if existing:
            if existing.invoice_id != invoice:
                raise UserError("The retry token belongs to another invoice application.")
            return existing
        self.env.cr.execute("SELECT id FROM account_move WHERE id = %s FOR UPDATE", [invoice.id])
        config = self._get_any_invoice_config(account)
        invoice.invalidate_recordset()
        residual = invoice.amount_residual
        if invoice.currency_id.compare_amounts(residual, 0.0) <= 0:
            raise UserError("The invoice has no patient receivable residual to settle.")
        receipt_lines = self._available_receipt_liability_lines(account, config, partner, source_receipts=source_receipts)
        available = invoice.currency_id.round(sum(-line.amount_residual for line in receipt_lines))
        if invoice.currency_id.compare_amounts(available, 0.0) <= 0:
            raise UserError("No posted patient advance is available for this billing account.")
        requested = invoice.currency_id.round(amount if amount is not None else residual)
        if invoice.currency_id.compare_amounts(requested, 0.0) <= 0:
            raise UserError("Application amount must be greater than zero.")
        apply_amount = min(requested, invoice.currency_id.round(residual), available)
        receivable_lines = self._posted_invoice_receivable_lines(invoice)
        if not receivable_lines:
            raise UserError("The invoice has no open receivable line to reconcile.")
        application = False
        try:
            with self.env.cr.savepoint(flush=True):
                application = Application.sudo().with_context(**{CONTROLLED_CTX: True}).create(
                    {"request_token": token, "billing_account_id": account.id, "invoice_id": invoice.id, "patient_id": account.patient_id.id, "partner_id": partner.id, "company_id": invoice.company_id.id, "currency_id": invoice.currency_id.id, "amount": apply_amount, "applied_by_id": self.env.user.id}
                )
                move = self.env["account.move"].sudo().create(
                    {
                        "move_type": "entry",
                        "date": fields.Date.context_today(invoice),
                        "journal_id": config.advance_application_journal_id.id,
                        "company_id": invoice.company_id.id,
                        "currency_id": invoice.currency_id.id,
                        "ref": "Patient Advance Application - %s" % invoice.name,
                        "line_ids": [
                            (0, 0, {"name": "Apply patient advance - %s" % invoice.name, "account_id": config.patient_advance_liability_account_id.id, "partner_id": partner.id, "debit": apply_amount, "credit": 0.0}),
                            (0, 0, {"name": "Clear patient receivable - %s" % invoice.name, "account_id": receivable_lines[0].account_id.id, "partner_id": partner.id, "debit": 0.0, "credit": apply_amount}),
                        ],
                    }
                )
                move.action_post()
                receivable_credit = move.line_ids.filtered(lambda line: line.account_id == receivable_lines[0].account_id and line.credit > 0)
                (receivable_lines + receivable_credit).reconcile()
                liability_debit = move.line_ids.filtered(lambda line: line.account_id == config.patient_advance_liability_account_id and line.debit > 0)
                remaining = apply_amount
                used_receipts = self.env["hospital.charge.receipt"]
                for line in receipt_lines:
                    if remaining <= AMOUNT_TOLERANCE:
                        break
                    used_receipts |= self.env["hospital.charge.receipt"].sudo().search([("accounting_move_id", "=", line.move_id.id)], limit=1)
                    remaining -= min(remaining, -line.amount_residual)
                (receipt_lines + liability_debit).reconcile()
                application.sudo().with_context(**{CONTROLLED_CTX: True}).write(
                    {"move_id": move.id, "state": "posted", "applied_at": fields.Datetime.now(), "receipt_ids": [(6, 0, used_receipts.ids)]}
                )
                invoice.invalidate_recordset(["amount_residual"])
                self._sync_charge_application_amounts(invoice)
        except Exception:
            raise
        return application.with_user(self.env.user)

    @api.model
    def _sync_charge_application_amounts(self, invoice):
        allocations = invoice.hospital_charge_allocation_ids.filtered(lambda alloc: alloc.allocation_type == "invoice" and alloc.state in ("draft", "posted"))
        if not allocations:
            return
        settled = invoice.currency_id.round(invoice.amount_total - invoice.amount_residual)
        total_snapshot = sum(alloc.amount_untaxed_snapshot for alloc in allocations) or invoice.amount_total
        for alloc in allocations:
            share = alloc.amount_untaxed_snapshot / total_snapshot if total_snapshot else 0.0
            amount = invoice.currency_id.round(settled * share)
            alloc.charge_id.with_user(self.env.user).with_context(
                **{
                    ALLOCATION_REASON_CTX: "32B-3 advance application sync",
                    ALLOCATION_REFERENCE_CTX: invoice.name,
                }
            ).write({"amount_applied_to_invoice": amount})



class HospitalBillingAccountAdvanceApplication(models.Model):
    _inherit = "hospital.billing.account"

    def _eligible_advance_application_invoices(self):
        self.ensure_one()
        return self.env["account.move"].sudo().search(
            [
                ("hospital_billing_account_id", "=", self.id),
                ("hospital_patient_id", "=", self.patient_id.id),
                ("hospital_managed_invoice", "=", True),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("payment_state", "!=", "reversed"),
                ("company_id", "=", self.company_id.id),
                ("currency_id", "=", self.currency_id.id),
                ("amount_residual", ">", AMOUNT_TOLERANCE),
            ],
            order="invoice_date asc, date asc, id asc",
        )

    def action_apply_patient_advances(self):
        assert_invoice_authorized(self.env, "apply patient advances from billing account")
        applications = self.env["hospital.patient.advance.application"]
        engine = self.env["hospital.billing.engine"].with_user(self.env.user)
        for account in self:
            account_sudo = account.sudo()
            if account_sudo.company_id not in self.env.companies:
                raise UserError("You cannot settle a billing account outside your allowed companies.")
            self.env.cr.execute("SELECT id FROM hospital_billing_account WHERE id = %s FOR UPDATE", [account_sudo.id])
            partner = account_sudo.patient_id.accounting_partner_id.commercial_partner_id
            if not partner:
                raise UserError("Patient %s has no accounting partner." % account_sudo.patient_id.display_name)
            config = engine.sudo()._get_any_invoice_config(account_sudo)
            available_lines = engine.sudo()._available_receipt_liability_lines(account_sudo, config, partner)
            available = account_sudo.currency_id.round(sum(-line.amount_residual for line in available_lines))
            if account_sudo.currency_id.compare_amounts(available, 0.0) <= 0:
                raise UserError("No posted unapplied patient advance is available for billing account %s." % account_sudo.name)
            invoices = account_sudo._eligible_advance_application_invoices()
            if not invoices:
                raise UserError("No posted patient invoice has an open receivable for billing account %s." % account_sudo.name)
            for invoice in invoices:
                available_lines = engine.sudo()._available_receipt_liability_lines(account_sudo, config, partner)
                available = account_sudo.currency_id.round(sum(-line.amount_residual for line in available_lines))
                if account_sudo.currency_id.compare_amounts(available, 0.0) <= 0:
                    break
                token = "billing-account-advance:%s:%s" % (account_sudo.id, invoice.id)
                applications |= engine.apply_patient_advance_to_invoice(invoice, request_token=token)
        if not applications:
            raise UserError("No patient advance could be applied.")
        if len(applications) == 1:
            return {
                "type": "ir.actions.act_window",
                "name": "Patient Advance Application",
                "res_model": "hospital.patient.advance.application",
                "view_mode": "form",
                "res_id": applications.id,
            }
        return {
            "type": "ir.actions.act_window",
            "name": "Patient Advance Applications",
            "res_model": "hospital.patient.advance.application",
            "view_mode": "list,form",
            "domain": [("id", "in", applications.ids)],
        }
class AccountMoveAdvanceApplication(models.Model):
    _inherit = "account.move"

    hospital_advance_application_ids = fields.One2many("hospital.patient.advance.application", "invoice_id", string="Patient Advance Applications")

    def action_apply_patient_advance(self):
        self.ensure_one()
        return self.env["hospital.billing.engine"].apply_patient_advance_to_invoice(self)



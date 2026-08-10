import json
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare


G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
INVOICE_GROUPS = (G_ACCOUNTANT, G_MANAGER, G_ADMIN)
QTY_TOLERANCE = 1e-6
CONTROLLED_PATIENT_ACCOUNTING_CTX = "hospital_controlled_patient_accounting_partner"


def assert_invoice_authorized(env, action):
    if env.su:
        return
    if not any(env.user.has_group(group) for group in INVOICE_GROUPS):
        raise AccessError(
            "You are not authorized to %s. Hospital Accountant, Manager or "
            "System Administrator access is required." % action
        )


class HospitalInvoiceBatch(models.Model):
    _name = "hospital.invoice.batch"
    _description = "Hospital Encounter Invoice Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "requested_at desc, id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default="New")
    request_token = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        index=True,
        tracking=True,
        help="Stable immutable retry token created before invoice construction.",
    )
    encounter_id = fields.Many2one(
        "hospital.encounter", required=True, readonly=True, ondelete="restrict", index=True
    )
    billing_account_id = fields.Many2one(
        "hospital.billing.account",
        required=True,
        readonly=True,
        ondelete="restrict",
        index=True,
    )
    patient_id = fields.Many2one(
        "hospital.patient", required=True, readonly=True, ondelete="restrict", index=True
    )
    commercial_partner_id = fields.Many2one(
        "res.partner", required=True, readonly=True, ondelete="restrict", index=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, ondelete="restrict", index=True
    )
    currency_id = fields.Many2one(
        "res.currency", required=True, readonly=True, ondelete="restrict"
    )
    invoice_id = fields.Many2one(
        "account.move", readonly=True, copy=False, ondelete="restrict", index=True
    )
    allocation_ids = fields.One2many(
        "hospital.charge.invoice.allocation", "batch_id", readonly=True
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("processing", "Processing"),
            ("completed", "Completed"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="draft",
        readonly=True,
        copy=False,
        index=True,
        tracking=True,
    )
    requested_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user
    )
    requested_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now, index=True
    )
    retry_count = fields.Integer(readonly=True, copy=False, default=0)
    error_summary = fields.Char(
        readonly=True,
        copy=False,
        help="Sanitized construction error; no clinical detail or traceback is stored.",
    )
    completed_at = fields.Datetime(readonly=True, copy=False)
    active = fields.Boolean(default=True, readonly=True)

    _sql_constraints = [
        (
            "invoice_batch_company_token_unique",
            "unique(company_id, request_token)",
            "An invoice batch with this retry token already exists for the company.",
        ),
        (
            "invoice_batch_invoice_unique",
            "unique(invoice_id)",
            "An invoice can belong to only one hospital invoice batch.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(
                "Invoice batches can only be created by the controlled billing engine."
            )
        sequence = self.env["ir.sequence"]
        clean = []
        for vals in vals_list:
            vals = dict(vals)
            vals["request_token"] = (vals.get("request_token") or uuid.uuid4().hex).strip()
            if not vals["request_token"]:
                raise ValidationError("Invoice batch request token cannot be empty.")
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.invoice.batch.sequence") or "New"
                )
            clean.append(vals)
        return super().create(clean)

    def write(self, vals):
        if not self.env.su:
            raise AccessError(
                "Invoice batch state and accounting links are controlled by the "
                "billing engine and cannot be changed through RPC."
            )
        immutable = {
            "request_token",
            "encounter_id",
            "billing_account_id",
            "patient_id",
            "commercial_partner_id",
            "company_id",
            "currency_id",
            "requested_by_id",
            "requested_at",
        }
        if immutable & set(vals) and any(batch.id for batch in self):
            raise UserError("Invoice batch identity fields are immutable.")
        return super().write(vals)

    def unlink(self):
        raise UserError("Invoice batches are permanent audit records and cannot be deleted.")

    @api.constrains(
        "encounter_id",
        "billing_account_id",
        "patient_id",
        "commercial_partner_id",
        "company_id",
        "currency_id",
        "invoice_id",
    )
    def _check_batch_consistency(self):
        for batch in self:
            account = batch.billing_account_id
            if account.encounter_id != batch.encounter_id:
                raise ValidationError("Invoice batch encounter and billing account differ.")
            if account.patient_id != batch.patient_id:
                raise ValidationError("Invoice batch patient and billing account differ.")
            if account.company_id != batch.company_id:
                raise ValidationError("Invoice batch company and billing account differ.")
            if account.currency_id != batch.currency_id:
                raise ValidationError("Invoice batch currency and billing account differ.")
            if batch.commercial_partner_id != batch.commercial_partner_id.commercial_partner_id:
                raise ValidationError("Invoice batch must use a commercial partner.")
            move = batch.invoice_id
            if move and (
                move.company_id != batch.company_id
                or move.currency_id != batch.currency_id
                or move.partner_id.commercial_partner_id != batch.commercial_partner_id
            ):
                raise ValidationError("Invoice batch and accounting invoice differ.")


class HospitalChargeInvoiceAllocation(models.Model):
    _name = "hospital.charge.invoice.allocation"
    _description = "Hospital Charge Invoice Quantity Provenance"
    _order = "id"

    charge_id = fields.Many2one(
        "hospital.charge.line", required=True, readonly=True, ondelete="restrict", index=True
    )
    batch_id = fields.Many2one(
        "hospital.invoice.batch", required=True, readonly=True, ondelete="restrict", index=True
    )
    move_id = fields.Many2one(
        "account.move", required=True, readonly=True, ondelete="restrict", index=True
    )
    move_line_id = fields.Many2one(
        "account.move.line", required=True, readonly=True, ondelete="restrict", index=True
    )
    allocation_type = fields.Selection(
        [("invoice", "Invoice"), ("credit", "Credit Note")],
        required=True,
        readonly=True,
        index=True,
    )
    quantity = fields.Float(required=True, readonly=True, digits=(16, 3))
    unit_price_snapshot = fields.Float(required=True, readonly=True, digits=(16, 2))
    discount_snapshot = fields.Float(required=True, readonly=True, digits=(16, 2))
    amount_untaxed_snapshot = fields.Monetary(
        required=True, readonly=True, currency_field="currency_id"
    )
    tax_fingerprint = fields.Char(required=True, readonly=True)
    product_id = fields.Many2one(
        "product.product", readonly=True, ondelete="restrict"
    )
    uom_id = fields.Many2one("uom.uom", readonly=True, ondelete="restrict")
    income_account_id = fields.Many2one(
        "account.account", required=True, readonly=True, ondelete="restrict"
    )
    analytic_distribution_snapshot = fields.Json(readonly=True)
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, ondelete="restrict", index=True
    )
    currency_id = fields.Many2one(
        "res.currency", required=True, readonly=True, ondelete="restrict"
    )
    idempotency_key = fields.Char(required=True, readonly=True, copy=False, index=True)
    state = fields.Selection(
        [("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")],
        compute="_compute_state",
        store=True,
        readonly=True,
        index=True,
    )
    original_allocation_id = fields.Many2one(
        "hospital.charge.invoice.allocation",
        readonly=True,
        copy=False,
        ondelete="restrict",
        index=True,
    )
    source_model = fields.Char(readonly=True, index=True)
    source_res_id = fields.Integer(readonly=True, index=True)
    source_line_id = fields.Integer(readonly=True)
    source_event = fields.Char(readonly=True)
    source_key = fields.Char(readonly=True, index=True)

    _sql_constraints = [
        (
            "charge_invoice_allocation_idempotency_unique",
            "unique(company_id, idempotency_key)",
            "This charge quantity allocation already exists.",
        ),
        (
            "charge_invoice_allocation_batch_move_unique",
            "unique(batch_id, charge_id, move_id, allocation_type)",
            "The charge is already allocated to this document in this batch.",
        ),
        (
            "charge_credit_original_move_unique",
            "unique(original_allocation_id, move_id)",
            "This original allocation is already credited by this credit note.",
        ),
        (
            "charge_invoice_allocation_quantity_positive",
            "check(quantity > 0)",
            "Invoice provenance quantity must be positive.",
        ),
    ]

    @api.depends("move_id.state")
    def _compute_state(self):
        for allocation in self:
            allocation.state = (
                "posted"
                if allocation.move_id.state == "posted"
                else "cancelled"
                if allocation.move_id.state == "cancel"
                else "draft"
            )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(
                "Invoice provenance can only be created by the controlled billing engine."
            )
        return super().create(vals_list)

    def write(self, vals):
        if any(allocation.move_id.state == "posted" for allocation in self):
            raise UserError("Posted invoice provenance is immutable.")
        if not self.env.su:
            raise AccessError("Invoice provenance cannot be changed through RPC.")
        immutable = set(self._fields) - {"state", "write_uid", "write_date"}
        if immutable & set(vals):
            raise UserError("Invoice provenance identity and quantity are immutable.")
        return super().write(vals)

    def unlink(self):
        if any(allocation.move_id.state == "posted" for allocation in self):
            raise UserError("Posted invoice provenance cannot be deleted.")
        if not self.env.su:
            raise AccessError("Invoice provenance cannot be deleted through RPC.")
        return super().unlink()

    @api.constrains(
        "charge_id",
        "batch_id",
        "move_id",
        "move_line_id",
        "allocation_type",
        "quantity",
        "company_id",
        "currency_id",
        "original_allocation_id",
    )
    def _check_allocation_consistency(self):
        for allocation in self:
            charge = allocation.charge_id
            if allocation.quantity <= QTY_TOLERANCE:
                raise ValidationError("Invoice provenance quantity must be positive.")
            if allocation.move_line_id.move_id != allocation.move_id:
                raise ValidationError("Provenance invoice line belongs to another document.")
            if allocation.batch_id.billing_account_id != charge.billing_account_id:
                raise ValidationError("Provenance charge belongs to another billing account.")
            if charge.company_id != allocation.company_id or allocation.move_id.company_id != allocation.company_id:
                raise ValidationError("Provenance cannot cross companies.")
            if charge.currency_id != allocation.currency_id or allocation.move_id.currency_id != allocation.currency_id:
                raise ValidationError("Provenance cannot cross currencies.")
            if allocation.allocation_type == "credit":
                original = allocation.original_allocation_id
                if not original or original.allocation_type != "invoice":
                    raise ValidationError(
                        "Credit provenance requires its original invoice allocation."
                    )
                if original.charge_id != charge:
                    raise ValidationError("Credit provenance charge differs from its original.")
            elif allocation.original_allocation_id:
                raise ValidationError("Invoice provenance cannot have an original credit link.")


class HospitalBillingServiceInvoiceConfig(models.Model):
    _inherit = "hospital.billing.service"

    invoice_product_id = fields.Many2one(
        "product.product",
        string="Invoice Product",
        check_company=True,
        domain="[('sale_ok', '=', True)]",
        help="Explicit standard Odoo product used on customer invoice lines.",
    )
    invoice_tax_ids = fields.Many2many(
        "account.tax",
        "hospital_billing_service_invoice_tax_rel",
        "service_id",
        "tax_id",
        string="Authoritative Invoice Taxes",
        check_company=True,
        domain="[('type_tax_use', '=', 'sale'), ('active', '=', True)]",
    )


class HospitalPatientAccountingPartner(models.Model):
    _inherit = "hospital.patient"

    accounting_partner_id = fields.Many2one(
        "res.partner",
        string="Accounting Customer",
        ondelete="restrict",
        help="Explicit customer/commercial partner for standard Odoo invoices. "
        "Invoice generation blocks when this mapping is absent.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get("accounting_partner_id") for vals in vals_list):
            assert_invoice_authorized(self.env, "assign a patient accounting customer")
        patients = super().create(vals_list)
        for patient in patients:
            patient.sudo()._ensure_accounting_partner()
        return patients

    def write(self, vals):
        if "accounting_partner_id" in vals and not (
            self.env.su and self.env.context.get(CONTROLLED_PATIENT_ACCOUNTING_CTX)
        ):
            assert_invoice_authorized(self.env, "change a patient accounting customer")
        return super().write(vals)

    def _accounting_partner_vals(self):
        self.ensure_one()
        return {
            "name": self.name,
            "type": "contact",
            "company_type": "person",
            "customer_rank": 1,
            "phone": self.phone or False,
            "mobile": self.mobile or False,
            "email": self.email or False,
            "street": self.address or False,
            "city": self.city or False,
            "zip": self.zip_code or False,
        }

    def _ensure_accounting_partner(self):
        for patient in self:
            if patient.accounting_partner_id:
                continue
            partner = self.env["res.partner"].sudo().create(patient._accounting_partner_vals())
            patient.sudo().with_context(**{CONTROLLED_PATIENT_ACCOUNTING_CTX: True}).write(
                {"accounting_partner_id": partner.id}
            )
        return self.mapped("accounting_partner_id")

    def action_create_link_accounting_customer(self):
        assert_invoice_authorized(self.env, "create or link a patient accounting customer")
        partners = self.sudo()._ensure_accounting_partner()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Accounting Customer Ready",
                "message": "Accounting customer linked: %s" % ", ".join(partners.mapped("display_name")),
                "type": "success",
                "sticky": False,
            },
        }


class HospitalBillingAccountPatientAccountingPartner(models.Model):
    _inherit = "hospital.billing.account"

    def action_create_link_patient_accounting_customer(self):
        assert_invoice_authorized(self.env, "create or link a patient accounting customer")
        patients = self.sudo().mapped("patient_id")
        patients.action_create_link_accounting_customer()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Accounting Customer Ready",
                "message": "Accounting customer linked for %d patient(s)." % len(patients),
                "type": "success",
                "sticky": False,
            },
        }


class HospitalChargeLineInvoiceProvenance(models.Model):
    _inherit = "hospital.charge.line"

    invoice_allocation_ids = fields.One2many(
        "hospital.charge.invoice.allocation", "charge_id", readonly=True
    )
    qty_invoiced = fields.Float(
        compute="_compute_invoice_provenance",
        store=True,
        compute_sudo=True,
        digits=(16, 3),
        readonly=True,
    )
    qty_credited = fields.Float(
        compute="_compute_invoice_provenance",
        store=True,
        compute_sudo=True,
        digits=(16, 3),
        readonly=True,
    )
    invoice_state = fields.Selection(
        selection_add=[
            ("partially_credited", "Partially Credited"),
            ("credited", "Credited"),
        ],
        compute="_compute_invoice_provenance",
        store=True,
        compute_sudo=True,
        readonly=True,
    )
    invoiced_at = fields.Datetime(
        compute="_compute_invoice_provenance", store=True, compute_sudo=True, readonly=True
    )
    allow_reinvoice_after_credit = fields.Boolean(
        readonly=True,
        copy=False,
        help="Explicit accounting authorization to make credited quantity eligible again.",
    )
    reinvoice_authorization_reason = fields.Char(readonly=True, copy=False)
    reinvoice_authorized_by_id = fields.Many2one(
        "res.users", readonly=True, copy=False
    )
    reinvoice_authorized_at = fields.Datetime(readonly=True, copy=False)
    qty_invoice_eligible = fields.Float(
        compute="_compute_invoice_eligible_quantity",
        store=True,
        compute_sudo=True,
        digits=(16, 3),
        help="Delivered quantity still eligible after active invoice reservations and "
        "the credited-quantity reinvoicing policy.",
    )
    invoice_analytic_distribution = fields.Json(
        string="Invoice Analytic Distribution",
        help="Optional analytic snapshot used for invoice grouping and provenance.",
    )

    @api.depends(
        "invoice_allocation_ids.quantity",
        "invoice_allocation_ids.allocation_type",
        "invoice_allocation_ids.state",
        "invoice_allocation_ids.create_date",
        "qty_billable",
    )
    def _compute_invoice_provenance(self):
        for charge in self:
            invoices = charge.invoice_allocation_ids.filtered(
                lambda allocation: allocation.allocation_type == "invoice"
                and allocation.state in ("draft", "posted")
            )
            credits = charge.invoice_allocation_ids.filtered(
                lambda allocation: allocation.allocation_type == "credit"
                and allocation.state == "posted"
            )
            gross = sum(invoices.mapped("quantity"))
            credited = sum(credits.mapped("quantity"))
            net = max(0.0, gross - credited)
            charge.qty_invoiced = gross
            charge.qty_credited = credited
            charge.invoiced_at = min(invoices.mapped("create_date")) if invoices else False
            if gross <= QTY_TOLERANCE:
                charge.invoice_state = "not_invoiced"
            elif credited > QTY_TOLERANCE:
                charge.invoice_state = (
                    "credited" if net <= QTY_TOLERANCE else "partially_credited"
                )
            elif gross + QTY_TOLERANCE < charge.qty_billable:
                charge.invoice_state = "partially_invoiced"
            else:
                charge.invoice_state = "invoiced"

    @api.depends(
        "qty_billable",
        "qty_invoiced",
        "qty_credited",
        "allow_reinvoice_after_credit",
        "charge_state",
        "authorization_state",
    )
    def _compute_invoice_eligible_quantity(self):
        for charge in self:
            if (
                charge.charge_state in ("cancelled", "reversed")
                or charge.authorization_state in ("rejected", "bypassed")
            ):
                charge.qty_invoice_eligible = 0.0
                continue
            reusable_credit = (
                charge.qty_credited if charge.allow_reinvoice_after_credit else 0.0
            )
            charge.qty_invoice_eligible = max(
                0.0, charge.qty_billable - charge.qty_invoiced + reusable_credit
            )

    @api.depends("qty_invoiced", "qty_credited", "qty_invoice_eligible")
    def _compute_qty_to_invoice(self):
        for charge in self:
            charge.net_invoiced_qty = charge.qty_invoiced - charge.qty_credited
            charge.qty_to_invoice = charge.qty_invoice_eligible

    def action_authorize_reinvoice_after_credit(self, reason):
        assert_invoice_authorized(self.env, "authorize reinvoicing credited quantity")
        if not (reason or "").strip():
            raise UserError("Reinvoicing credited quantity requires a documented reason.")
        for charge in self:
            if charge.qty_credited <= QTY_TOLERANCE:
                raise UserError(
                    "Charge %s has no posted credited quantity." % charge.display_name
                )
        return self.sudo().write(
            {
                "allow_reinvoice_after_credit": True,
                "reinvoice_authorization_reason": reason.strip(),
                "reinvoice_authorized_by_id": self.env.user.id,
                "reinvoice_authorized_at": fields.Datetime.now(),
            }
        )


class HospitalBillingAccountInvoices(models.Model):
    _inherit = "hospital.billing.account"

    invoice_batch_ids = fields.One2many(
        "hospital.invoice.batch", "billing_account_id", readonly=True
    )
    invoice_batch_count = fields.Integer(compute="_compute_invoice_batch_count")

    @api.depends("invoice_batch_ids")
    def _compute_invoice_batch_count(self):
        for account in self:
            account.invoice_batch_count = len(account.invoice_batch_ids)

    def action_create_invoice_batch(self):
        self.ensure_one()
        invoice = self.env["hospital.billing.engine"].create_invoice(self)
        if not invoice:
            batch = self.invoice_batch_ids.sorted("id", reverse=True)[:1]
            if not batch:
                raise UserError("Invoice construction failed before a batch was created.")
            return {
                "type": "ir.actions.act_window",
                "name": "Failed Invoice Batch",
                "res_model": "hospital.invoice.batch",
                "view_mode": "form",
                "res_id": batch.id,
            }
        return {
            "type": "ir.actions.act_window",
            "name": "Customer Invoice",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": invoice.id,
        }

    def action_view_invoice_batches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Invoice Batches",
            "res_model": "hospital.invoice.batch",
            "view_mode": "list,form",
            "domain": [("billing_account_id", "=", self.id)],
        }


class HospitalEncounterInvoices(models.Model):
    _inherit = "hospital.encounter"

    hospital_invoice_batch_ids = fields.One2many(
        "hospital.invoice.batch", "encounter_id", readonly=True
    )

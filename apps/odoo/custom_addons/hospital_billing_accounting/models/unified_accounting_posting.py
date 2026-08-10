
import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .invoice_batch import assert_invoice_authorized
from .patient_advance_accounting import AMOUNT_TOLERANCE, CONTROLLED_CTX


SOURCE_PHARMACY_DISPENSE = "hospital.pharmacy.dispense"
PHARMACY_EVENT = "pharmacy_dispense"
VALUATION_CTX = "hospital_inventory_valuation_controlled"
QTY_TOLERANCE = 0.0005


class HospitalStockMovementAccountingBridge(models.Model):
    _inherit = "hospital.stock.movement"

    hospital_valuation_move_id = fields.Many2one(
        "account.move",
        string="Valuation Journal Entry",
        readonly=True,
        copy=False,
        ondelete="restrict",
        index=True,
    )

    def write(self, vals):
        protected = {"hospital_valuation_move_id", "inventory_accounting_state", "accounting_note"}
        if protected & set(vals) and not (self.env.su and self.env.context.get(VALUATION_CTX)):
            raise AccessError("Inventory valuation accounting fields are controlled by the hospital accounting bridge.")
        return super().write(vals)


class AccountMoveHospitalValuation(models.Model):
    _inherit = "account.move"

    hospital_stock_movement_ids = fields.One2many(
        "hospital.stock.movement",
        "hospital_valuation_move_id",
        string="Hospital Stock Movements",
        readonly=True,
    )


class HospitalBillingEngineUnifiedAccounting(models.AbstractModel):
    _inherit = "hospital.billing.engine"

    @api.model
    def _source_pharmacy_charges(self, dispense):
        return self.env["hospital.charge.line"].sudo().with_context(active_test=False).search([
            ("source_model", "=", SOURCE_PHARMACY_DISPENSE),
            ("source_res_id", "=", dispense.id),
            ("source_event", "=", PHARMACY_EVENT),
            ("charge_state", "=", "active"),
        ], order="id")

    @api.model
    def _source_receipts(self, charges):
        if not charges:
            return self.env["hospital.charge.receipt"]
        return self.env["hospital.charge.receipt.allocation"].sudo().search([
            ("charge_line_id", "in", charges.ids),
        ], order="id").mapped("receipt_id").sorted(lambda r: r.id)

    @api.model
    def _invoice_for_charges(self, account, charges, token):
        existing = self.env["hospital.charge.invoice.allocation"].sudo().search([
            ("charge_id", "in", charges.ids),
            ("allocation_type", "=", "invoice"),
            ("move_id.move_type", "=", "out_invoice"),
            ("move_id.state", "!=", "cancel"),
        ], limit=1).move_id
        if existing:
            return existing
        eligible = charges.filtered(lambda c: c.qty_invoice_eligible > QTY_TOLERANCE)
        if not eligible:
            return self.env["account.move"]
        invoice = self.create_invoice(account, charges=eligible, request_token=token)
        if not invoice:
            raise UserError("Hospital invoice creation failed; review the failed invoice batch for details.")
        return invoice

    @api.model
    def _post_source_receipt_accounting(self, receipts):
        posted = self.env["account.move"]
        for receipt in receipts.filtered(lambda r: r.state == "confirmed"):
            move = receipt.accounting_move_id
            if not move:
                move = receipt.with_user(self.env.user).action_post_receipt_accounting()
            if move and move.state != "posted":
                raise UserError("Receipt %s has an accounting entry that is not posted." % receipt.name)
            posted |= move
        return posted

    @api.model
    def _post_pharmacy_valuation(self, dispense, config):
        config._assert_inventory_valuation_configuration()
        consumptions = self.env["hospital.stock.consumption"].sudo().search([
            ("pharmacy_dispense_id", "=", dispense.id),
            ("state", "=", "consumed"),
        ], order="id")
        movements = self.env["hospital.stock.movement"].sudo().search([
            ("consumption_id", "in", consumptions.ids),
            ("movement_type", "=", "consumption"),
            ("inventory_accounting_state", "in", ("pending", "ready")),
            ("hospital_valuation_move_id", "=", False),
        ], order="id")
        if not movements:
            return self.env["account.move"]
        company = dispense.billing_account_id.company_id or self.env.company
        currency = dispense.billing_account_id.currency_id or company.currency_id
        partner = dispense.patient_id.accounting_partner_id.commercial_partner_id
        if not partner:
            raise UserError("Patient %s has no accounting partner." % dispense.patient_id.display_name)
        if any(move.currency_id and move.currency_id != currency for move in movements):
            raise UserError("Inventory movement currency differs from the billing account currency.")
        amount = currency.round(sum(movements.mapped("movement_value")))
        if currency.compare_amounts(amount, 0.0) <= 0:
            raise UserError("No positive inventory valuation amount is available for %s." % dispense.name)
        move = self.env["account.move"].sudo().create({
            "move_type": "entry",
            "date": fields.Date.context_today(dispense),
            "journal_id": config.inventory_valuation_journal_id.id,
            "company_id": company.id,
            "currency_id": currency.id,
            "ref": "Pharmacy Stock Valuation - %s" % dispense.name,
            "line_ids": [
                (0, 0, {
                    "name": "COGS - %s" % dispense.name,
                    "account_id": config.cogs_account_id.id,
                    "partner_id": partner.id,
                    "debit": amount,
                    "credit": 0.0,
                }),
                (0, 0, {
                    "name": "Inventory consumed - %s" % dispense.name,
                    "account_id": config.inventory_asset_account_id.id,
                    "partner_id": partner.id,
                    "debit": 0.0,
                    "credit": amount,
                }),
            ],
        })
        move.action_post()
        movements.with_context(**{VALUATION_CTX: True}).write({
            "hospital_valuation_move_id": move.id,
            "inventory_accounting_state": "posted",
            "accounting_note": "Posted by unified hospital accounting bridge for %s." % dispense.name,
        })
        return move.with_user(self.env.user)

    @api.model
    def post_pharmacy_dispense_accounting(self, dispense, request_token=None):
        assert_invoice_authorized(self.env, "post unified pharmacy accounting")
        if isinstance(dispense, int):
            dispense = self.env["hospital.pharmacy.dispense"].browse(dispense)
        dispense.ensure_one()
        if dispense.state != "dispensed":
            raise UserError("Only fully dispensed pharmacy records can be posted to accounting by this action.")
        account = dispense.billing_account_id
        if not account or not dispense.encounter_id or account.encounter_id != dispense.encounter_id:
            raise UserError("The pharmacy dispense is not linked to a consistent encounter billing account.")
        if account.patient_id != dispense.patient_id:
            raise UserError("The pharmacy dispense patient differs from the billing account patient.")
        partner = dispense.patient_id.accounting_partner_id.commercial_partner_id
        if not partner:
            raise UserError("Patient %s has no accounting partner." % dispense.patient_id.display_name)
        charges = self._source_pharmacy_charges(dispense)
        if not charges:
            raise UserError("No unified pharmacy charge is linked to %s." % dispense.name)
        config = self._source_config(charges[:1])
        token_root = (request_token or "pharmacy-dispense-accounting:%s" % dispense.id).strip()
        if not token_root:
            token_root = uuid.uuid4().hex
        result = {"invoice": self.env["account.move"], "receipt_moves": self.env["account.move"], "applications": self.env["hospital.patient.advance.application"], "valuation_move": self.env["account.move"]}
        with self.env.cr.savepoint(flush=True):
            self.env.cr.execute("SELECT id FROM hospital_pharmacy_dispense WHERE id = %s FOR UPDATE", [dispense.id])
            self.env.cr.execute("SELECT id FROM hospital_billing_account WHERE id = %s FOR UPDATE", [account.id])
            invoice = self._invoice_for_charges(account, charges, "%s:invoice" % token_root)
            if not invoice:
                raise UserError("No delivered, uninvoiced pharmacy charge quantity is eligible for accounting posting.")
            if invoice and invoice.state == "draft":
                invoice.sudo().action_post()
            result["invoice"] = invoice
            receipts = self._source_receipts(charges)
            result["receipt_moves"] = self._post_source_receipt_accounting(receipts)
            if invoice and invoice.state == "posted" and invoice.amount_residual > AMOUNT_TOLERANCE:
                application = self.apply_patient_advance_to_invoice(invoice, request_token="%s:advance:%s" % (token_root, invoice.id), source_receipts=receipts)
                result["applications"] |= application
            result["valuation_move"] = self._post_pharmacy_valuation(dispense, config)
        return result


class HospitalPharmacyDispenseAccountingBridge(models.Model):
    _inherit = "hospital.pharmacy.dispense"

    def action_post_unified_accounting(self):
        self.ensure_one()
        result = self.env["hospital.billing.engine"].with_user(self.env.user).post_pharmacy_dispense_accounting(self)
        invoice = result.get("invoice")
        if invoice:
            return {
                "type": "ir.actions.act_window",
                "name": "Hospital Customer Invoice",
                "res_model": "account.move",
                "view_mode": "form",
                "res_id": invoice.id,
            }
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {"title": "Accounting", "message": "No new invoice was required.", "type": "success", "sticky": False}}


class HospitalBillingAccountAccountingIndicators(models.Model):
    _inherit = "hospital.billing.account"

    accounting_invoice_count = fields.Integer(compute="_compute_accounting_bridge_indicators", compute_sudo=True)
    accounting_payment_entry_count = fields.Integer(compute="_compute_accounting_bridge_indicators", compute_sudo=True)
    accounting_valuation_entry_count = fields.Integer(compute="_compute_accounting_bridge_indicators", compute_sudo=True)
    accounting_invoiced_amount = fields.Monetary(compute="_compute_accounting_bridge_indicators", compute_sudo=True, currency_field="currency_id")
    accounting_paid_amount = fields.Monetary(compute="_compute_accounting_bridge_indicators", compute_sudo=True, currency_field="currency_id")
    unposted_delivered_amount = fields.Monetary(compute="_compute_accounting_bridge_indicators", compute_sudo=True, currency_field="currency_id")
    unposted_inventory_valuation = fields.Monetary(compute="_compute_accounting_bridge_indicators", compute_sudo=True, currency_field="currency_id")
    accounting_status = fields.Selection([
        ("nothing_to_post", "Nothing to Post"),
        ("pending", "Pending Accounting"),
        ("partial", "Partially Posted"),
        ("posted", "Posted"),
    ], compute="_compute_accounting_bridge_indicators", compute_sudo=True)

    def _compute_accounting_bridge_indicators(self):
        Move = self.env["account.move"].sudo()
        Movement = self.env["hospital.stock.movement"].sudo()
        for account in self:
            invoices = Move.search([("hospital_billing_account_id", "=", account.id), ("move_type", "=", "out_invoice"), ("state", "!=", "cancel")])
            receipts = account.receipt_ids.filtered(lambda r: "accounting_move_id" in r._fields and r.accounting_move_id)
            consumptions = self.env["hospital.stock.consumption"].sudo().search([("pharmacy_dispense_id.billing_account_id", "=", account.id), ("state", "=", "consumed")])
            valuation_movements = Movement.search([("consumption_id", "in", consumptions.ids), ("movement_type", "=", "consumption")])
            pending_valuation = valuation_movements.filtered(lambda m: m.inventory_accounting_state in ("pending", "ready") and not m.hospital_valuation_move_id)
            unposted_charges = account.charge_line_ids.filtered(lambda c: c.qty_invoice_eligible > QTY_TOLERANCE)
            account.accounting_invoice_count = len(invoices)
            account.accounting_payment_entry_count = len(receipts.mapped("accounting_move_id"))
            account.accounting_valuation_entry_count = len(valuation_movements.mapped("hospital_valuation_move_id"))
            account.accounting_invoiced_amount = sum(invoices.mapped("amount_total"))
            account.accounting_paid_amount = sum(invoices.mapped(lambda inv: inv.amount_total - inv.amount_residual))
            account.unposted_delivered_amount = sum(unposted_charges.mapped(lambda c: c.qty_invoice_eligible * c.unit_price * (1.0 - (c.discount or 0.0) / 100.0)))
            account.unposted_inventory_valuation = sum(pending_valuation.mapped("movement_value"))
            if account.unposted_delivered_amount or account.unposted_inventory_valuation:
                account.accounting_status = "pending" if not invoices else "partial"
            elif invoices or receipts or valuation_movements:
                account.accounting_status = "posted"
            else:
                account.accounting_status = "nothing_to_post"

    def action_view_accounting_invoices(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": "Hospital Invoices", "res_model": "account.move", "view_mode": "list,form", "domain": [("hospital_billing_account_id", "=", self.id), ("move_type", "=", "out_invoice")]}

    def action_view_accounting_payment_entries(self):
        self.ensure_one()
        moves = self.receipt_ids.mapped("accounting_move_id") | self.env["hospital.patient.advance.application"].sudo().search([("billing_account_id", "=", self.id)]).mapped("move_id")
        return {"type": "ir.actions.act_window", "name": "Payment / Advance Entries", "res_model": "account.move", "view_mode": "list,form", "domain": [("id", "in", moves.ids)]}

    def action_view_accounting_valuation_entries(self):
        self.ensure_one()
        consumptions = self.env["hospital.stock.consumption"].sudo().search([("pharmacy_dispense_id.billing_account_id", "=", self.id)])
        moves = self.env["hospital.stock.movement"].sudo().search([("consumption_id", "in", consumptions.ids)]).mapped("hospital_valuation_move_id")
        return {"type": "ir.actions.act_window", "name": "Inventory Valuation Entries", "res_model": "account.move", "view_mode": "list,form", "domain": [("id", "in", moves.ids)]}


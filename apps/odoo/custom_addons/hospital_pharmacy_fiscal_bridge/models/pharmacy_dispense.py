import logging

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from odoo.addons.hospital_fiscal_bridge.models.fiscal_transaction import AMOUNT_TOLERANCE

_logger = logging.getLogger(__name__)

QTY_PRECISION = 3


class HospitalPharmacyDispense(models.Model):
    _name = "hospital.pharmacy.dispense"
    _inherit = ["hospital.pharmacy.dispense", "mail.thread"]

    amount_payable = fields.Float(
        string="Amount Payable",
        compute="_compute_amount_payable",
        digits=(16, 2),
        help="Unified clearance amount still due for this dispense.",
    )
    fiscal_bill_id = fields.Many2one(
        "hospital.patient.bill",
        string="Legacy Fiscal Bill",
        readonly=True,
        copy=False,
        help="Legacy-only fiscal bill. Encounter-linked unified pharmacy dispenses no longer use this field.",
    )
    fiscal_transaction_ids = fields.One2many("hospital.fiscal.transaction", "pharmacy_dispense_id", string="Fiscal Transactions")
    fiscal_transaction_id = fields.Many2one("hospital.fiscal.transaction", string="Fiscal Transaction", compute="_compute_fiscal_payment_info")
    fiscal_transaction_count = fields.Integer(compute="_compute_fiscal_payment_info", string="Fiscal Payments")
    fiscal_payment_state = fields.Selection(
        [("none", "Not Requested"), ("prepared", "Prepared / Awaiting Payment"), ("paid", "Paid"), ("failed", "Failed"), ("cancelled", "Cancelled / Expired"), ("exception", "Exception / Manual Review")],
        string="Fiscal Payment Status",
        compute="_compute_fiscal_payment_info",
        default="none",
    )
    fiscal_reference = fields.Char(string="Fiscal Reference", compute="_compute_fiscal_payment_info")
    fiscal_receipt_number = fields.Char(string="Fiscal Receipt No.", compute="_compute_fiscal_payment_info")
    inventory_consumption_id = fields.Many2one("hospital.stock.consumption", string="Consumption Record", readonly=True, copy=False)
    inventory_consumption_state = fields.Selection(
        [("not_created", "Not Created"), ("created", "Created"), ("consumed", "Consumed"), ("failed", "Failed")],
        string="Consumption Status",
        compute="_compute_inventory_consumption_state",
        default="not_created",
    )
    auto_consumption_error = fields.Text(
        string="Auto Consumption Error",
        readonly=True,
        copy=False,
        help="Legacy recovery field. Payment success no longer consumes stock for unified pharmacy dispenses.",
    )

    @api.depends("line_ids.price_subtotal", "unified_billing_enabled")
    def _compute_amount_payable(self):
        for dispense in self:
            if "charge_line_ids" in dispense._fields and dispense.unified_billing_enabled:
                charges = dispense._pharmacy_charges() if hasattr(dispense, "_pharmacy_charges") else dispense.charge_line_ids
                dispense.amount_payable = sum(charges.mapped("amount_due_for_clearance"))
            else:
                dispense.amount_payable = sum(dispense.line_ids.mapped("price_subtotal"))

    @api.depends("fiscal_transaction_ids.state", "fiscal_transaction_ids.active", "fiscal_transaction_ids.external_receipt_no")
    def _compute_fiscal_payment_info(self):
        for dispense in self:
            transactions = dispense.fiscal_transaction_ids.filtered("active")
            dispense.fiscal_transaction_count = len(transactions)
            paid = transactions.filtered(lambda t: t.state in ("paid", "reversed")).sorted("id", reverse=True)
            active = transactions.filtered(lambda t: t.state in ("ready", "locked")).sorted("id", reverse=True)
            current = paid[:1] or active[:1] or transactions.sorted("id", reverse=True)[:1]
            dispense.fiscal_transaction_id = current
            dispense.fiscal_reference = current.name if current else False
            dispense.fiscal_receipt_number = paid[:1].external_receipt_no if paid else False
            states = set(transactions.mapped("state"))
            if paid:
                dispense.fiscal_payment_state = "paid"
            elif active:
                dispense.fiscal_payment_state = "prepared"
            elif "exception" in states:
                dispense.fiscal_payment_state = "exception"
            elif "failed" in states:
                dispense.fiscal_payment_state = "failed"
            elif states & {"cancelled", "expired"}:
                dispense.fiscal_payment_state = "cancelled"
            else:
                dispense.fiscal_payment_state = "none"

    @api.depends("inventory_consumption_ids.state", "auto_consumption_error")
    def _compute_inventory_consumption_state(self):
        for dispense in self:
            consumptions = dispense.inventory_consumption_ids.filtered(lambda c: c.state != "cancelled") if "inventory_consumption_ids" in dispense._fields else self.env["hospital.stock.consumption"]
            if any(c.state == "consumed" for c in consumptions):
                dispense.inventory_consumption_state = "consumed"
            elif dispense.auto_consumption_error:
                dispense.inventory_consumption_state = "failed"
            elif consumptions:
                dispense.inventory_consumption_state = "created"
            else:
                dispense.inventory_consumption_state = "not_created"

    def action_prepare_fiscal_payment(self):
        self.ensure_one()
        self._check_can_prepare_fiscal_payment()
        Fiscal = self.env["hospital.fiscal.transaction"].sudo()
        for existing in self.fiscal_transaction_ids.filtered(lambda t: t.active and t.state in ("ready", "locked")).sudo():
            if not existing._check_and_apply_expiry():
                return self._open_fiscal_transaction(existing)
        vals = {
            "cashier_id": self.env.user.id,
            "pharmacy_dispense_id": self.id,
            "source_model": self._name,
            "source_record_id": self.id,
            "source_reference": self.name,
        }
        if self.unified_billing_enabled:
            self._ensure_pharmacy_billing()
            vals.update({
                "source_patient_id": self.patient_id.id,
                "source_currency_id": self.billing_account_id.currency_id.id,
            })
        else:
            bill = self._get_or_create_fiscal_bill()
            vals["bill_id"] = bill.id
        transaction = Fiscal.create(vals)
        transaction.action_confirm_ready()
        self._post_fiscal_note(f"Fiscal payment prepared: <b>{transaction.name}</b> for <b>{transaction.amount_payable:.2f}</b> by {self.env.user.name}.")
        self._create_audit_log(action_type="update", description=f"Fiscal payment request {transaction.name} prepared for {transaction.amount_payable:.2f}.")
        return self._open_fiscal_transaction(transaction)

    def _check_can_prepare_fiscal_payment(self):
        self.ensure_one()
        if self.state not in ("ready", "partial"):
            raise UserError("Fiscal payment can only be prepared when the dispense is Ready or Partially Dispensed.")
        if not self.line_ids:
            raise UserError("This dispense has no medicine lines. Add medicines before preparing fiscal payment.")
        if all(float_compare(line.dispensed_quantity, 0.0, precision_digits=2) <= 0 for line in self.line_ids):
            raise UserError("Enter dispensed/intended quantity before preparing fiscal payment.")
        self._validate_dispense_quantities()
        paid = self.fiscal_transaction_ids.filtered(lambda t: t.active and t.state in ("paid", "reversed"))
        if paid:
            raise UserError(f"This dispense already has a successful fiscal payment ({paid[0].name}). It cannot be charged again.")
        if self.unified_billing_enabled:
            self._ensure_pharmacy_billing()
            if self.amount_payable <= AMOUNT_TOLERANCE:
                raise UserError("This dispense has no remaining unified amount payable.")
            return
        unpriced = [line.medicine_id.display_name for line in self.line_ids if line.dispensed_quantity > 0 and float_compare(line.unit_price, 0.0, precision_digits=2) <= 0]
        if unpriced:
            raise UserError("These dispensed medicines have no unit price: " + ", ".join(unpriced))
        if self.amount_payable <= AMOUNT_TOLERANCE:
            raise UserError("Cannot prepare fiscal payment because the payable amount is zero.")

    def _fiscal_validate_can_prepare_unified(self, transaction):
        self.ensure_one()
        self._ensure_pharmacy_billing()
        charges = self._pharmacy_charges()
        if not charges:
            raise UserError("No unified pharmacy charges exist for this dispense.")
        blocking = transaction.search([("source_model", "=", self._name), ("source_record_id", "=", self.id), ("state", "in", ("ready", "locked")), ("active", "=", True), ("id", "!=", transaction.id)], limit=1)
        if blocking:
            raise UserError(f"This dispense already has active fiscal payment request {blocking.name}.")
        if self.amount_payable <= AMOUNT_TOLERANCE:
            raise UserError("This dispense has no remaining amount payable.")

    def _fiscal_compute_amounts_unified(self, transaction):
        self.ensure_one()
        amount = self.amount_payable
        transaction.write({
            "amount_untaxed": amount,
            "amount_discount": 0.0,
            "amount_total": amount,
            "amount_paid_before": sum(self._pharmacy_charges().mapped("amount_received")),
            "amount_due": amount,
            "amount_payable": amount,
            "source_patient_id": self.patient_id.id,
            "source_currency_id": self.billing_account_id.currency_id.id,
        })

    def _fiscal_prepare_snapshot_lines_unified(self, transaction):
        self.ensure_one()
        Line = self.env["hospital.fiscal.transaction.line"].sudo()
        transaction.line_ids.unlink()
        vals = []
        for charge in self._pharmacy_charges().filtered(lambda c: c.amount_due_for_clearance > AMOUNT_TOLERANCE):
            vals.append({
                "transaction_id": transaction.id,
                "service_name": charge.service_id.name or charge.description,
                "service_code": charge.service_id.code or False,
                "source_type": "pharmacy",
                "description": charge.description,
                "quantity": charge.qty_requested,
                "unit_price": charge.unit_price,
                "discount": charge.discount,
                "subtotal": charge.amount_due_for_clearance,
                "tax_amount": 0.0,
                "total": charge.amount_due_for_clearance,
                "source_model": charge.source_model,
                "source_record_id": charge.source_line_id or charge.source_res_id,
            })
        if vals:
            Line.create(vals)

    def _create_unified_receipt_for_fiscal_success(self, transaction):
        self.ensure_one()
        if transaction.unified_receipt_id:
            return transaction.unified_receipt_id
        self._ensure_pharmacy_billing()
        charges = self._pharmacy_charges().filtered(lambda c: c.charge_state == "active" and c.amount_due_for_clearance > AMOUNT_TOLERANCE)
        remaining = transaction.amount_payable
        alloc_vals = []
        for charge in charges.sorted("id"):
            amount = min(remaining, charge.amount_due_for_clearance)
            if amount <= AMOUNT_TOLERANCE:
                continue
            alloc_vals.append((charge, amount))
            remaining -= amount
            if remaining <= AMOUNT_TOLERANCE:
                break
        if not alloc_vals:
            raise UserError("No eligible unified charges remain for this fiscal payment.")
        token = "fiscal:%s" % transaction.name
        Receipt = self.env["hospital.charge.receipt"].sudo()
        receipt = Receipt.search([("intake_token", "=", token)], limit=1)
        if not receipt:
            receipt = Receipt.create({
                "payment_method": "fiscal_terminal",
                "payment_reference": transaction.external_receipt_no or transaction.external_transaction_id or transaction.name,
                "received_at": transaction.fiscal_paid_at or fields.Datetime.now(),
                "received_by_id": transaction.cashier_id.id or self.env.user.id,
                "note": "Fiscal terminal payment %s for pharmacy dispense %s." % (transaction.name, self.name),
                "state": "draft",
                "intake_token": token,
            })
            self.env["hospital.charge.receipt.allocation"].sudo().create([
                {"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": amount}
                for charge, amount in alloc_vals
            ])
            receipt.sudo().write({"state": "confirmed", "fiscalized": True, "fiscal_reference": transaction.name})
            if hasattr(receipt, "action_post_receipt_accounting"):
                receipt.action_post_receipt_accounting()
        transaction.sudo().write({"unified_receipt_id": receipt.id})
        return receipt

    def _on_fiscal_payment_success(self, transaction):
        self.ensure_one()
        if transaction.state != "paid":
            return
        if self.unified_billing_enabled:
            receipt = self._create_unified_receipt_for_fiscal_success(transaction)
            self._post_fiscal_note(f"Fiscal payment received: <b>{transaction.name}</b>. Unified receipt <b>{receipt.name}</b> was confirmed. Dispense and inventory remain pending until Validate Dispense.")
            self._create_audit_log(action_type="update", description=f"Fiscal payment {transaction.name} created unified receipt {receipt.name}; no dispense or inventory action was executed.")
            return
        self._post_fiscal_note(f"Fiscal payment received: <b>{transaction.name}</b>. Legacy automatic dispense/stock consumption is disabled; validate dispense explicitly.")
        self._create_audit_log(action_type="update", description=f"Fiscal payment {transaction.name} confirmed. Explicit Validate Dispense is required.")

    def _prepare_fiscal_bill_line_vals(self):
        self.ensure_one()
        vals = []
        for line in self.line_ids:
            if line.dispensed_quantity <= 0:
                continue
            vals.append((0, 0, {"description": line.medicine_id.display_name, "source_type": "pharmacy", "quantity": line.dispensed_quantity, "unit_price": line.unit_price, "source_model": line._name, "source_record_id": line.id}))
        return vals

    def _get_or_create_fiscal_bill(self):
        self.ensure_one()
        if self.unified_billing_enabled:
            raise UserError("Unified pharmacy dispenses cannot create legacy patient bills. Use the unified fiscal payment route.")
        line_vals = self._prepare_fiscal_bill_line_vals()
        bill = self.fiscal_bill_id.sudo() if self.fiscal_bill_id else None
        if bill and (bill.state == "cancelled" or (bill.amount_paid > AMOUNT_TOLERANCE and bill.amount_due <= AMOUNT_TOLERANCE)):
            bill = None
        if bill and bill.amount_paid <= AMOUNT_TOLERANCE:
            bill.line_ids.sudo().unlink()
            bill.write({"line_ids": line_vals})
        if not bill:
            bill = self.env["hospital.patient.bill"].sudo().create({"patient_id": self.patient_id.id, "appointment_id": self.appointment_id.id or False, "physician_id": self.physician_id.id or False, "cashier_id": self.env.user.id, "line_ids": line_vals, "notes": f"Auto-created for legacy pharmacy dispense {self.name}."})
            self.with_context(skip_dispense_write_audit=True).write({"fiscal_bill_id": bill.id})
        if bill.state == "draft":
            bill.action_confirm()
        return bill

    def action_retry_inventory_consumption(self):
        self.ensure_one()
        if self.unified_billing_enabled:
            raise UserError("Inventory for unified pharmacy dispenses is controlled by Validate Dispense, not fiscal payment retry.")
        return self.action_mark_dispensed()

    def action_view_fiscal_transactions(self):
        self.ensure_one()
        transactions = self.fiscal_transaction_ids.filtered("active")
        if len(transactions) == 1:
            return self._open_fiscal_transaction(transactions)
        return {"type": "ir.actions.act_window", "name": "Fiscal Payments", "res_model": "hospital.fiscal.transaction", "view_mode": "list,form", "domain": [("pharmacy_dispense_id", "=", self.id)], "target": "current"}

    def _open_fiscal_transaction(self, transaction):
        return {"type": "ir.actions.act_window", "name": "Fiscal Payment", "res_model": "hospital.fiscal.transaction", "res_id": transaction.id, "view_mode": "form", "target": "current"}

    def _post_fiscal_note(self, body):
        for dispense in self:
            try:
                dispense.message_post(body=body)
            except Exception:
                _logger.warning("Pharmacy fiscal bridge: could not post chatter note on %s", dispense.name, exc_info=True)

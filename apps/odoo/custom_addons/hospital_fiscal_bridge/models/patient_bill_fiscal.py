from odoo import api, fields, models


class HospitalPatientBillFiscal(models.Model):
    _inherit = "hospital.patient.bill"

    fiscal_transaction_ids = fields.One2many(
        "hospital.fiscal.transaction",
        "bill_id",
        string="Fiscal Transactions",
    )
    fiscal_transaction_count = fields.Integer(
        compute="_compute_fiscal_state",
        string="Fiscal Transaction Count",
    )
    latest_fiscal_transaction_id = fields.Many2one(
        "hospital.fiscal.transaction",
        compute="_compute_fiscal_state",
        string="Latest Fiscal Transaction",
    )
    fiscal_state = fields.Selection(
        [
            ("not_requested", "Not Requested"),
            ("ready", "Ready"),
            ("locked", "In Progress"),
            ("paid", "Paid"),
            ("failed", "Failed"),
            ("partial", "Partially Paid"),
            ("cancelled", "Cancelled"),
            ("exception", "Exception"),
        ],
        compute="_compute_fiscal_state",
        string="Fiscal Status",
    )

    @api.depends(
        "fiscal_transaction_ids.state",
        "fiscal_transaction_ids.active",
        "state",
        "amount_due",
    )
    def _compute_fiscal_state(self):
        for bill in self:
            transactions = bill.fiscal_transaction_ids.filtered("active")
            bill.fiscal_transaction_count = len(transactions)
            latest = transactions.sorted("id", reverse=True)[:1]
            bill.latest_fiscal_transaction_id = latest
            if not transactions:
                bill.fiscal_state = "not_requested"
                continue
            states = set(transactions.mapped("state"))
            paid = [t for t in transactions if t.state == "paid"]
            if "exception" in states:
                bill.fiscal_state = "exception"
            elif paid and bill.state == "paid":
                bill.fiscal_state = "paid"
            elif paid:
                bill.fiscal_state = "partial"
            elif "locked" in states:
                bill.fiscal_state = "locked"
            elif "ready" in states:
                bill.fiscal_state = "ready"
            elif "failed" in states:
                bill.fiscal_state = "failed"
            elif "cancelled" in states or "expired" in states:
                bill.fiscal_state = "cancelled"
            else:
                bill.fiscal_state = "not_requested"

    def action_prepare_fiscal_transaction(self):
        """Create a fiscal payment request for the outstanding amount and
        finalize it (Ready), then open it."""
        self.ensure_one()
        transaction_model = self.env["hospital.fiscal.transaction"]
        transaction_model._validate_can_prepare(self)
        transaction = transaction_model.create({"bill_id": self.id})
        transaction.action_confirm_ready()
        return {
            "type": "ir.actions.act_window",
            "name": "Fiscal Transaction",
            "res_model": "hospital.fiscal.transaction",
            "res_id": transaction.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_fiscal_transactions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Fiscal Transactions",
            "res_model": "hospital.fiscal.transaction",
            "view_mode": "list,form",
            "domain": [("bill_id", "=", self.id)],
            "context": {"default_bill_id": self.id},
        }

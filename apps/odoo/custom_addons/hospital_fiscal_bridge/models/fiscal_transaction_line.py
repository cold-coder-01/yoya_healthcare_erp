from odoo import fields, models


class HospitalFiscalTransactionLine(models.Model):
    """Immutable snapshot of a bill line at the moment the fiscal
    transaction was prepared. Fiscal audit needs to know exactly what was
    sent to the terminal, even if the live bill lines change later."""

    _name = "hospital.fiscal.transaction.line"
    _description = "Fiscal Transaction Snapshot Line"
    _order = "transaction_id, id"

    transaction_id = fields.Many2one(
        "hospital.fiscal.transaction",
        required=True,
        ondelete="cascade",
        index=True,
    )
    bill_line_id = fields.Many2one(
        "hospital.patient.bill.line",
        ondelete="set null",
        help="Live bill line this snapshot was taken from (may be removed later).",
    )
    service_name = fields.Char()
    service_code = fields.Char()
    source_type = fields.Selection(
        [
            ("consultation", "Consultation"),
            ("laboratory", "Laboratory"),
            ("radiology", "Radiology"),
            ("pharmacy", "Pharmacy"),
            ("procedure", "Procedure"),
            ("admission", "Admission"),
            ("other", "Other"),
        ],
    )
    description = fields.Char()
    quantity = fields.Float(default=1.0, digits=(16, 3))
    unit_price = fields.Float(digits=(16, 2))
    discount = fields.Float(
        digits=(16, 2),
        help="Discount percentage copied from the bill line.",
    )
    subtotal = fields.Float(digits=(16, 2))
    tax_amount = fields.Float(digits=(16, 2), default=0.0)
    total = fields.Float(digits=(16, 2))
    currency_id = fields.Many2one(
        "res.currency",
        related="transaction_id.currency_id",
        store=True,
    )
    source_model = fields.Char()
    source_record_id = fields.Integer()
    department_name = fields.Char()

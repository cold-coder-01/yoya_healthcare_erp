"""One posted inpatient accounting act: the provenance record.

WHAT THIS IS
------------
Every accounting act the admission bridge performs leaves ONE row here:

    close          receipts posted, delivered care invoiced, advance applied
    refund         Dr Patient Advance Liability / Cr cash-bank for a refund
                   recorded now, atomically with the operational refund
    legacy_refund  the same entry for a refund recorded BEFORE refund
                   accounting existed, posted later with the method the
                   Accountant supplied

It ties the ledger back to the patient, the visit, the admission, the
billing account, the operational refund (its audit row) and the operation
token. The GL entries and the advance-application records stay the
accounting authority; this row only says which act produced them.

IMMUTABLE. Created and linked only inside the bridge's own workflow methods
(a ContextVar capability no RPC payload can raise), never unlinked. The token
is unique per company, so a replay finds the first result instead of posting
twice.
"""
import contextvars
from contextlib import contextmanager

from odoo import api, fields, models
from odoo.exceptions import UserError

_operation_var = contextvars.ContextVar(
    "hospital_admission_accounting_operation_capability", default=False
)


@contextmanager
def accounting_operation_capability():
    token = _operation_var.set(True)
    try:
        yield
    finally:
        _operation_var.reset(token)


REFUND_METHOD_SELECTION = [
    ("cash", "Cash"),
    ("bank_transfer", "Bank Transfer"),
    ("mobile_money", "Mobile Money"),
]


class HospitalAdmissionAccountingOperation(models.Model):
    _name = "hospital.admission.accounting.operation"
    _description = "Inpatient Accounting Operation"
    _order = "id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default="New")
    kind = fields.Selection(
        [
            ("close", "Accounting Close"),
            ("refund", "Refund"),
            ("legacy_refund", "Legacy Refund"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    request_token = fields.Char(required=True, readonly=True, copy=False, index=True)
    admission_id = fields.Many2one(
        "hospital.admission", required=True, readonly=True, ondelete="restrict", index=True
    )
    encounter_id = fields.Many2one(
        "hospital.encounter", required=True, readonly=True, ondelete="restrict", index=True
    )
    patient_id = fields.Many2one(
        "hospital.patient", required=True, readonly=True, ondelete="restrict", index=True
    )
    billing_account_id = fields.Many2one(
        "hospital.billing.account", required=True, readonly=True, ondelete="restrict", index=True
    )
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True)
    currency_id = fields.Many2one("res.currency", required=True, readonly=True)
    amount = fields.Monetary(readonly=True, currency_field="currency_id", default=0.0)
    payment_method = fields.Selection(REFUND_METHOD_SELECTION, readonly=True)
    payment_reference = fields.Char(readonly=True)
    accounting_date = fields.Date(readonly=True)
    journal_id = fields.Many2one("account.journal", readonly=True, ondelete="restrict")
    move_id = fields.Many2one(
        "account.move", string="Refund Entry", readonly=True, copy=False, ondelete="restrict"
    )
    receipt_ids = fields.Many2many(
        "hospital.charge.receipt",
        "hospital_admission_accounting_op_receipt_rel",
        "operation_id",
        "receipt_id",
        string="Receipts Posted",
        readonly=True,
    )
    invoice_ids = fields.Many2many(
        "account.move",
        "hospital_admission_accounting_op_invoice_rel",
        "operation_id",
        "move_id",
        string="Invoices Posted",
        readonly=True,
    )
    application_ids = fields.Many2many(
        "hospital.patient.advance.application",
        "hospital_admission_accounting_op_application_rel",
        "operation_id",
        "application_id",
        string="Advance Applications",
        readonly=True,
    )
    close_id = fields.Many2one(
        "hospital.admission.accounting.operation",
        string="Accounting Close",
        readonly=True,
        ondelete="restrict",
    )
    audit_log_id = fields.Many2one(
        "hospital.audit.log", string="Operational Refund", readonly=True, ondelete="restrict"
    )
    performed_by_id = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    performed_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)

    _sql_constraints = [
        (
            "op_token_unique",
            "unique(company_id, request_token)",
            "This inpatient accounting request token was already used.",
        ),
        (
            "op_move_unique",
            "unique(move_id)",
            "This refund entry already belongs to an inpatient accounting operation.",
        ),
        (
            "op_refund_log_unique",
            "unique(audit_log_id)",
            "This refund already has its accounting entry.",
        ),
        (
            "op_amount_not_negative",
            "CHECK(amount >= 0)",
            "An inpatient accounting amount cannot be negative.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        if not _operation_var.get():
            raise UserError(
                "Inpatient accounting operations are recorded by the accounting close "
                "and cannot be created directly."
            )
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.admission.accounting.operation")
                    or "New"
                )
        return super().create(vals_list)

    def write(self, vals):
        # The one permitted write: linking a refund posting to the operational
        # refund's audit row, written in the same savepoint right after it.
        if not _operation_var.get() or set(vals) - {"audit_log_id"}:
            raise UserError("Inpatient accounting operations are immutable.")
        return super().write(vals)

    def unlink(self):
        raise UserError("Inpatient accounting operations cannot be deleted.")

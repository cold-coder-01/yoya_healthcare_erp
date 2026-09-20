"""One successful Pharmacy Desk mutation: the replay record (Pharmacy Slice 2).

WHAT THIS IS FOR
----------------
A double click, a browser retry or a dropped response must not prepare or
validate a dispense twice. The client sends one UUID per intended action; the
first successful attempt leaves ONE row here, and every later attempt with the
same token is answered from the dispense's CURRENT state instead of running the
workflow again.

WHAT IT DELIBERATELY IS NOT
---------------------------
Not an audit of quantities, not a response cache and not a financial record. It
stores who did which kind of action to which dispense, a digest of what they
asked for, and the revision it produced. No PHI response body, no amount, no
batch. The dispense and its lines remain the only record of what was supplied.

IMMUTABLE. Created only inside the workflow method that just succeeded (a
ContextVar capability no RPC payload can raise), never written afterwards, never
unlinked. The token is globally unique in SQL, so two different requests can
never both own it.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError

from .pharmacy_authority import has_operation_capability

OPERATION_TOKEN_MAX_LENGTH = 64


class HospitalPharmacyOperation(models.Model):
    _name = "hospital.pharmacy.operation"
    _description = "Pharmacy Desk Operation"
    _order = "id desc"
    _rec_name = "operation_token"
    _sql_constraints = [
        (
            "operation_token_unique",
            "unique(operation_token)",
            "A pharmacy operation token can be used only once.",
        ),
    ]

    dispense_id = fields.Many2one(
        "hospital.pharmacy.dispense",
        required=True,
        index=True,
        ondelete="restrict",
        readonly=True,
    )
    operation_type = fields.Selection(
        [("prepare", "Prepare"), ("validate", "Validate")],
        required=True,
        readonly=True,
    )
    operation_token = fields.Char(required=True, readonly=True, index=True)
    request_digest = fields.Char(required=True, readonly=True)
    result_revision = fields.Integer(required=True, readonly=True)
    performed_by_id = fields.Many2one("res.users", required=True, readonly=True, ondelete="restrict")
    performed_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)

    @api.model_create_multi
    def create(self, vals_list):
        if not has_operation_capability():
            raise UserError(
                "Pharmacy operations are recorded by the Pharmacy Desk workflow "
                "and cannot be created directly."
            )
        for vals in vals_list:
            token = (vals.get("operation_token") or "").strip()
            if not token or len(token) > OPERATION_TOKEN_MAX_LENGTH:
                raise UserError("A pharmacy operation token is required.")
        return super().create(vals_list)

    def write(self, vals):
        raise UserError("Pharmacy operation records are immutable.")

    def unlink(self):
        raise UserError("Pharmacy operation records cannot be deleted.")

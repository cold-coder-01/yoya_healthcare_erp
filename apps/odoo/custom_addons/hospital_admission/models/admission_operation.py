"""One successful Admissions mutation: the replay record (Admissions Slice 2).

WHAT THIS IS FOR
----------------
A double click, a browser retry or a dropped response must not request an
admission twice or admit a patient twice. The client sends one UUID per
intended action; the first successful attempt leaves ONE row here, and every
later attempt with the same token is answered from the admission's CURRENT
state instead of running the workflow again. The same shape as
hospital.pharmacy.operation.

WHAT IT DELIBERATELY IS NOT
---------------------------
Not a clinical record, not a response cache, not a financial record. It stores
which kind of action, on which admission, by whom, a digest of what they asked
for and the revision it produced. No patient name, no reason text, no bed
label, no amount: the digest is a one-way hash, and the admission itself stays
the only record of what happened.

IMMUTABLE. Created only inside the workflow method that just succeeded (a
ContextVar capability no RPC payload can raise), never written afterwards,
never unlinked. The token is globally unique in SQL, so two different requests
can never both own it.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError

from .admission_authority import DESK_TOKEN_MAX_LENGTH, has_admission_operation_capability


class HospitalAdmissionOperation(models.Model):
    _name = "hospital.admission.operation"
    _description = "Admissions Desk Operation"
    _order = "id desc"
    _rec_name = "operation_token"
    _sql_constraints = [
        (
            "operation_token_unique",
            "unique(operation_token)",
            "An admission operation token can be used only once.",
        ),
    ]

    admission_id = fields.Many2one(
        "hospital.admission",
        required=True,
        index=True,
        ondelete="restrict",
        readonly=True,
    )
    operation_type = fields.Selection(
        [
            ("request", "Request Admission"),
            ("admit", "Admit"),
            # Admissions Slice 3.
            ("transfer", "Transfer"),
            ("cancel_request", "Cancel Request"),
            # Admissions Slice 4.
            ("medical_discharge", "Medical Discharge"),
            ("final_discharge", "Final Discharge"),
            # Inpatient advance slice.
            ("estimate", "Inpatient Estimate"),
            ("refund", "Patient Refund"),
        ],
        required=True,
        readonly=True,
    )
    operation_token = fields.Char(required=True, readonly=True, index=True)
    request_digest = fields.Char(required=True, readonly=True)
    result_revision = fields.Integer(required=True, readonly=True)
    performed_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict"
    )
    performed_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not has_admission_operation_capability():
            raise UserError(
                "Admission operations are recorded by the admissions workflow and "
                "cannot be created directly."
            )
        for vals in vals_list:
            token = (vals.get("operation_token") or "").strip()
            if not token or len(token) > DESK_TOKEN_MAX_LENGTH:
                raise UserError("An admission operation token is required.")
        return super().create(vals_list)

    def write(self, vals):
        raise UserError("Admission operation records are immutable.")

    def unlink(self):
        raise UserError("Admission operation records cannot be deleted.")

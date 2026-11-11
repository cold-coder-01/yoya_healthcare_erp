from odoo import api, fields, models


class HospitalInsuranceAccountingLog(models.Model):
    _name = "hospital.insurance.accounting.log"
    _description = "Hospital Insurance Accounting Bridge Log"
    _order = "performed_date desc, id desc"

    name = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default="New",
    )
    claim_id = fields.Many2one(
        "hospital.insurance.claim",
        string="Insurance Claim",
        ondelete="cascade",
        index=True,
    )
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        string="Patient Bill",
        ondelete="cascade",
        index=True,
    )
    action_type = fields.Selection(
        [
            ("reclassification", "Receivable Reclassification"),
            ("payment", "Payer Payment"),
            ("rejection_transfer", "Rejection Transfer"),
            ("writeoff", "Write-off"),
            ("reversal", "Reversal"),
            ("error", "Error"),
        ],
        required=True,
    )
    state = fields.Selection(
        [
            ("success", "Success"),
            ("error", "Error"),
        ],
        required=True,
        default="success",
    )
    message = fields.Text()
    raw_payload = fields.Text()
    move_id = fields.Many2one("account.move", string="Journal Entry")
    external_reference = fields.Char(
        copy=False,
        index=True,
        help="Idempotency/audit reference supplied by a manual deposit, bank slip, or future fiscal-payment provider.",
    )
    performed_by = fields.Many2one(
        "res.users",
        string="Performed By",
        default=lambda self: self.env.user,
    )
    performed_date = fields.Datetime(default=fields.Datetime.now)
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
    )
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "insurance_payment_external_reference_unique",
            "unique(company_id, action_type, external_reference)",
            "This insurer payment reference has already been processed for this company.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    sequence.next_by_code(
                        "hospital.insurance.accounting.log"
                    )
                    or "New"
                )
        return super().create(vals_list)

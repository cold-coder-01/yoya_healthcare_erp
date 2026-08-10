from odoo import api, fields, models


class HospitalAccountingPostingLog(models.Model):
    _name = "hospital.accounting.posting.log"
    _description = "Hospital Accounting Posting Log"
    _order = "posting_date desc, id desc"

    name = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default="New",
    )
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        string="Patient Bill",
        ondelete="cascade",
        index=True,
    )
    payment_id = fields.Many2one(
        "hospital.patient.bill.payment",
        string="Payment",
        ondelete="cascade",
        index=True,
    )
    event_type = fields.Selection(
        [
            ("bill_ready", "Bill Ready"),
            ("bill_post", "Bill Posted"),
            ("payment_post", "Payment Posted"),
            ("reversal", "Reversal"),
            ("error", "Error"),
        ],
        required=True,
    )
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("success", "Success"),
            ("failed", "Failed"),
        ],
        default="draft",
        required=True,
    )
    posting_date = fields.Datetime(default=fields.Datetime.now)
    posted_by = fields.Many2one(
        "res.users",
        string="Posted By",
        default=lambda self: self.env.user,
    )
    account_move_id = fields.Many2one("account.move", string="Journal Entry")
    debit_total = fields.Monetary(currency_field="currency_id")
    credit_total = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    message = fields.Text()
    raw_payload = fields.Text()
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.accounting.posting.log.sequence")
                    or "New"
                )
        return super().create(vals_list)

from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalPatientBillPayment(models.Model):
    _name = "hospital.patient.bill.payment"
    _description = "Patient Bill Payment"
    _order = "payment_date desc, id desc"

    name = fields.Char(readonly=True, copy=False, default="New")
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        required=True,
        ondelete="cascade",
    )
    payment_date = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
    )
    amount = fields.Float(required=True, digits=(16, 2))
    currency_id = fields.Many2one(
        "res.currency",
        related="bill_id.currency_id",
        store=True,
    )
    payment_method = fields.Selection(
        [
            ("cash", "Cash"),
            ("bank_transfer", "Bank Transfer"),
            ("mobile_money", "Mobile Money"),
            ("card", "Card"),
            ("insurance", "Insurance"),
            ("other", "Other"),
        ],
    )
    payment_reference = fields.Char()
    cashier_id = fields.Many2one(
        "res.users",
        default=lambda self: self.env.user,
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.patient.bill.payment.sequence")
                    or "New"
                )
        payments = super().create(vals_list)
        for payment in payments:
            payment._log_audit(
                "create",
                f"Payment {payment.name} of {payment.amount:.2f} created for bill {payment.bill_id.name}.",
            )
        return payments

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for payment in self:
                payment._log_audit(
                    "delete_attempt",
                    f"Delete attempt on payment {payment.name} by {self.env.user.name}.",
                )
            raise UserError(
                "Payment records cannot be deleted. Contact the system administrator."
            )
        return super().unlink()

    def _log_audit(self, action_type, description):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        patient_id = (
            self.bill_id.patient_id.id
            if self.bill_id and self.bill_id.patient_id
            else False
        )
        audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
            patient_id=patient_id,
            model_name=self._name,
            record_id=self.id,
            action_type=action_type,
            description=description,
        )


class HospitalPatientBillPaymentWizard(models.TransientModel):
    _name = "hospital.patient.bill.payment.wizard"
    _description = "Register Payment Wizard"

    bill_id = fields.Many2one(
        "hospital.patient.bill",
        required=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="bill_id.currency_id",
        readonly=True,
    )
    amount_due = fields.Float(
        related="bill_id.amount_due",
        readonly=True,
        digits=(16, 2),
        string="Remaining Due",
    )
    payment_date = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
    )
    amount = fields.Float(required=True, digits=(16, 2))
    payment_method = fields.Selection(
        [
            ("cash", "Cash"),
            ("bank_transfer", "Bank Transfer"),
            ("mobile_money", "Mobile Money"),
            ("card", "Card"),
            ("insurance", "Insurance"),
            ("other", "Other"),
        ],
        default="cash",
        required=True,
    )
    payment_reference = fields.Char()
    notes = fields.Text()

    @api.model
    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        if result.get("bill_id") and "amount" in fields_list:
            bill = self.env["hospital.patient.bill"].browse(result["bill_id"])
            result["amount"] = bill.amount_due
        return result

    def action_confirm_payment(self):
        self.ensure_one()
        bill = self.bill_id
        if bill.state not in ("confirmed", "partially_paid"):
            raise UserError(
                "Payment can only be registered on confirmed or partially paid bills."
            )
        if self.amount <= 0:
            raise UserError("Payment amount must be greater than zero.")
        if self.amount > bill.amount_due + 0.005:
            raise UserError(
                "Payment amount (%.2f) cannot exceed the remaining due amount (%.2f)."
                % (self.amount, bill.amount_due)
            )
        self.env["hospital.patient.bill.payment"].create(
            {
                "bill_id": bill.id,
                "amount": self.amount,
                "payment_method": self.payment_method,
                "payment_reference": self.payment_reference or False,
                "payment_date": self.payment_date,
                "notes": self.notes or False,
                "cashier_id": self.env.user.id,
            }
        )
        bill._update_payment_state()
        return {"type": "ir.actions.act_window_close"}

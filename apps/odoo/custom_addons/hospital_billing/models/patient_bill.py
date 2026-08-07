from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalPatientBill(models.Model):
    _name = "hospital.patient.bill"
    _description = "Patient Bill"
    _order = "bill_date desc, name desc"

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    appointment_id = fields.Many2one(
        "hospital.appointment",
        tracking=True,
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        tracking=True,
    )
    bill_date = fields.Date(
        default=fields.Date.context_today,
        required=True,
    )
    cashier_id = fields.Many2one(
        "res.users",
        string="Cashier",
        default=lambda self: self.env.user,
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    line_ids = fields.One2many(
        "hospital.patient.bill.line",
        "bill_id",
        string="Bill Lines",
    )
    payment_ids = fields.One2many(
        "hospital.patient.bill.payment",
        "bill_id",
        string="Payments",
    )
    payment_count = fields.Integer(
        compute="_compute_payment_count",
        string="Payment Count",
    )
    amount_untaxed = fields.Float(
        string="Subtotal",
        compute="_compute_amounts",
        store=True,
        digits=(16, 2),
    )
    discount_amount = fields.Float(
        string="Total Discount",
        compute="_compute_amounts",
        store=True,
        digits=(16, 2),
    )
    amount_total = fields.Float(
        string="Total",
        compute="_compute_amounts",
        store=True,
        digits=(16, 2),
    )
    amount_paid = fields.Float(
        string="Amount Paid",
        compute="_compute_amount_paid",
        store=True,
        digits=(16, 2),
        tracking=True,
    )
    amount_due = fields.Float(
        string="Amount Due",
        compute="_compute_amounts",
        store=True,
        digits=(16, 2),
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
        tracking=True,
    )
    payment_reference = fields.Char(tracking=True)
    notes = fields.Text()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("partially_paid", "Partially Paid"),
            ("paid", "Paid"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    active = fields.Boolean(default=True)

    @api.depends("payment_ids.amount", "payment_ids.active")
    def _compute_amount_paid(self):
        for bill in self:
            bill.amount_paid = sum(bill.payment_ids.mapped("amount"))

    @api.depends("payment_ids")
    def _compute_payment_count(self):
        for bill in self:
            bill.payment_count = len(bill.payment_ids)

    @api.depends("line_ids.subtotal", "line_ids.quantity", "line_ids.unit_price", "amount_paid")
    def _compute_amounts(self):
        for bill in self:
            subtotal_sum = sum(line.subtotal for line in bill.line_ids)
            gross_sum = sum(line.quantity * line.unit_price for line in bill.line_ids)
            bill.amount_untaxed = subtotal_sum
            bill.discount_amount = gross_sum - subtotal_sum
            bill.amount_total = subtotal_sum
            bill.amount_due = max(0.0, subtotal_sum - bill.amount_paid)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for bill in self:
            patient_name = bill.patient_id.display_name or "Patient"
            if bill.name and bill.name != "New":
                bill.display_name = f"{bill.name} - {patient_name}"
            else:
                bill.display_name = patient_name

    def _update_payment_state(self):
        """Recompute bill state based on cumulative payments. Called after each payment creation."""
        PaymentModel = self.env["hospital.patient.bill.payment"]
        for bill in self:
            payments = PaymentModel.search([("bill_id", "=", bill.id), ("active", "=", True)])
            total_paid = sum(payments.mapped("amount"))
            if total_paid <= 0.0:
                new_state = "confirmed" if bill.state == "partially_paid" else bill.state
            elif total_paid < bill.amount_total - 0.005:
                new_state = "partially_paid"
            else:
                new_state = "paid"
            if bill.state != new_state:
                bill.write({"state": new_state})

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = sequence.next_by_code(
                    "hospital.patient.bill.sequence"
                ) or "New"
        bills = super().create(vals_list)
        for bill in bills:
            bill._create_audit_log(
                action_type="create",
                description="Patient bill created.",
                new_value=f"Bill: {bill.name}, Patient: {bill.patient_id.display_name}",
            )
        return bills

    def write(self, vals):
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            bill.id: bill._audit_summary(tracked_vals.keys())
            for bill in self
        }
        result = super().write(vals)
        if tracked_vals:
            if vals.get("active") is False:
                action_type = "archive"
                description = "Patient bill archived."
            elif "state" in vals:
                action_type = "state_change"
                description = f"Bill state changed to {vals.get('state', '')}."
            else:
                action_type = "update"
                description = "Patient bill updated."
            for bill in self:
                bill._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(bill.id),
                    new_value=bill._audit_summary(tracked_vals.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for bill in self:
                bill._create_audit_log(
                    action_type="delete_attempt",
                    description=f"Delete attempt on bill {bill.name}.",
                )
            raise UserError(
                "Patient bills cannot be deleted. Archive them instead."
            )
        return super().unlink()

    def _audit_summary(self, field_names):
        values = []
        for field_name in field_names:
            if field_name in self._fields:
                value = self[field_name]
                if hasattr(value, "mapped"):
                    value = ", ".join(value.mapped("display_name"))
                values.append(f"{field_name}: {value}")
        return "; ".join(values)

    def _create_audit_log(self, action_type, description, old_value=False, new_value=False):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        patient_id = self.patient_id.id if self.patient_id else False
        audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
            patient_id=patient_id,
            model_name=self._name,
            record_id=self.id,
            action_type=action_type,
            description=description,
            old_value=old_value,
            new_value=new_value,
        )

    def action_confirm(self):
        for bill in self:
            bill._assert_not_encounter_managed_duplicate()
            if bill.state != "draft":
                raise UserError("Only draft bills can be confirmed.")
            bill.write({"state": "confirmed"})

    def _assert_not_encounter_managed_duplicate(self):
        """Narrow guard against billing the same encounter through both engines."""
        for bill in self:
            appointment = bill.appointment_id
            encounter = (
                appointment.encounter_id
                if appointment and "encounter_id" in appointment._fields
                else self.env["hospital.encounter"]
            )
            account = encounter.billing_account_id if encounter else False
            if account and account.charge_line_ids:
                raise UserError(
                    "This appointment is managed by encounter billing account %s. "
                    "The legacy patient-bill path is blocked to prevent duplicate "
                    "invoicing. Use the encounter invoice-batch action instead."
                    % account.display_name
                )
        return True

    def action_register_payment(self):
        self.ensure_one()
        if self.state not in ("confirmed", "partially_paid"):
            raise UserError(
                "Only confirmed or partially paid bills can register payments."
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Register Payment",
            "res_model": "hospital.patient.bill.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_bill_id": self.id},
        }

    def action_mark_paid(self):
        for bill in self:
            if bill.state not in ("confirmed", "partially_paid"):
                raise UserError(
                    "Only confirmed or partially paid bills can be marked as paid."
                )
            bill.write({"state": "paid"})

    def action_cancel(self):
        for bill in self:
            if bill.state not in ("draft", "confirmed", "partially_paid"):
                raise UserError(
                    "Only draft, confirmed, or partially paid bills can be cancelled."
                )
            bill.write({"state": "cancelled"})

    def action_reset_to_draft(self):
        for bill in self:
            if bill.state != "cancelled":
                raise UserError("Only cancelled bills can be reset to draft.")
            bill.write({"state": "draft"})


class HospitalPatientBillLine(models.Model):
    _name = "hospital.patient.bill.line"
    _description = "Patient Bill Line"
    _order = "sequence, id"

    bill_id = fields.Many2one(
        "hospital.patient.bill",
        required=True,
        ondelete="cascade",
    )
    service_id = fields.Many2one("hospital.billing.service")
    description = fields.Char(required=True)
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
    quantity = fields.Float(default=1.0, digits=(16, 3))
    unit_price = fields.Float(digits=(16, 2))
    discount = fields.Float(default=0.0, digits=(16, 2))
    subtotal = fields.Float(
        compute="_compute_subtotal",
        store=True,
        digits=(16, 2),
    )
    source_model = fields.Char()
    source_record_id = fields.Integer()
    sequence = fields.Integer(default=10)

    @api.depends("quantity", "unit_price", "discount")
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = line.quantity * line.unit_price * (1.0 - line.discount / 100.0)

    @api.onchange("service_id")
    def _onchange_service_id(self):
        if self.service_id:
            self.description = self.service_id.name
            self.unit_price = self.service_id.default_price
            self.source_type = self.service_id.service_type

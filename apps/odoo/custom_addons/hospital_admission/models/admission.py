import math

from odoo import api, fields, models
from odoo.exceptions import UserError, AccessError


class HospitalAdmission(models.Model):
    _name = "hospital.admission"
    _description = "Hospital Admission"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "admission_date desc, name desc"
    _rec_name = "name"

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
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        tracking=True,
    )
    appointment_id = fields.Many2one(
        "hospital.appointment",
        string="Appointment",
        tracking=True,
    )
    diagnosis_id = fields.Many2one(
        "hospital.patient.diagnosis",
        string="Diagnosis",
        tracking=True,
    )
    admission_date = fields.Datetime(
        default=fields.Datetime.now,
        tracking=True,
    )
    expected_discharge_date = fields.Datetime(tracking=True)
    discharge_date = fields.Datetime(tracking=True)
    ward_id = fields.Many2one(
        "hospital.ward",
        string="Ward",
        tracking=True,
    )
    room_id = fields.Many2one(
        "hospital.room",
        string="Room",
        tracking=True,
    )
    bed_id = fields.Many2one(
        "hospital.bed",
        string="Bed",
        tracking=True,
    )
    admission_reason = fields.Text()
    discharge_summary = fields.Text()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("admitted", "Admitted"),
            ("transferred", "Transferred"),
            ("discharged", "Discharged"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    active = fields.Boolean(default=True)
    transfer_ids = fields.One2many(
        "hospital.admission.transfer",
        "admission_id",
        string="Transfers",
    )
    notes = fields.Text()

    # ── Billing linkage ─────────────────────────────────────────
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        string="Bill",
        readonly=True,
        copy=False,
        tracking=True,
    )
    bill_count = fields.Integer(
        compute="_compute_bill_count",
        string="Bills",
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
    )
    billing_state = fields.Selection(
        [
            ("not_billed", "Not Billed"),
            ("billed", "Billed"),
            ("partially_paid", "Partially Paid"),
            ("paid", "Paid"),
        ],
        compute="_compute_billing_state",
        string="Billing Status",
        store=True,
    )

    # ── Billing preview (computed) ───────────────────────────────
    stay_days = fields.Float(
        compute="_compute_stay_days",
        string="Stay Days",
        digits=(16, 2),
        store=False,
    )
    admission_fee_amount = fields.Float(
        compute="_compute_admission_billing",
        string="Admission Fee",
        digits=(16, 2),
        store=False,
    )
    daily_rate_amount = fields.Float(
        compute="_compute_admission_billing",
        string="Daily Rate",
        digits=(16, 2),
        store=False,
    )
    bed_charge_amount = fields.Float(
        compute="_compute_admission_billing",
        string="Bed Charge",
        digits=(16, 2),
        store=False,
    )
    total_admission_charge = fields.Float(
        compute="_compute_admission_billing",
        string="Total Admission Charge",
        digits=(16, 2),
        store=False,
    )

    # ── Computed helpers ─────────────────────────────────────────

    def _compute_bill_count(self):
        for rec in self:
            rec.bill_count = 1 if rec.bill_id else 0

    @api.depends("bill_id", "bill_id.state", "bill_id.amount_paid", "bill_id.amount_total")
    def _compute_billing_state(self):
        for rec in self:
            if not rec.bill_id:
                rec.billing_state = "not_billed"
            elif rec.bill_id.state == "paid":
                rec.billing_state = "paid"
            elif rec.bill_id.state == "partially_paid":
                rec.billing_state = "partially_paid"
            else:
                rec.billing_state = "billed"

    @api.depends("admission_date", "discharge_date")
    def _compute_stay_days(self):
        for rec in self:
            start = rec.admission_date
            end = rec.discharge_date or fields.Datetime.now()
            if start and end and end > start:
                hours = (end - start).total_seconds() / 3600.0
                rec.stay_days = max(1, math.ceil(hours / 24))
            else:
                rec.stay_days = 1 if start else 0

    @api.depends(
        "stay_days",
        "ward_id", "ward_id.admission_fee", "ward_id.daily_ward_rate",
        "room_id", "room_id.daily_room_rate",
        "bed_id", "bed_id.daily_bed_rate",
    )
    def _compute_admission_billing(self):
        for rec in self:
            ward = rec.ward_id
            room = rec.room_id
            bed = rec.bed_id

            rec.admission_fee_amount = ward.admission_fee if ward else 0.0

            if bed and bed.daily_bed_rate > 0:
                rec.daily_rate_amount = bed.daily_bed_rate
            elif room and room.daily_room_rate > 0:
                rec.daily_rate_amount = room.daily_room_rate
            elif ward:
                rec.daily_rate_amount = ward.daily_ward_rate
            else:
                rec.daily_rate_amount = 0.0

            rec.bed_charge_amount = rec.daily_rate_amount * rec.stay_days
            rec.total_admission_charge = rec.admission_fee_amount + rec.bed_charge_amount

    # ── Billing actions ──────────────────────────────────────────

    def action_generate_admission_bill(self):
        self.ensure_one()
        if self.state != "discharged":
            raise UserError("Admission bill can only be generated after discharge.")
        if self.bill_id:
            raise UserError(
                f"This admission has already been billed. Bill reference: {self.bill_id.name}"
            )
        if not self.discharge_date:
            raise UserError("Discharge date is not set. Please discharge the patient first.")
        if not self.patient_id:
            raise UserError("Patient is required to generate a bill.")

        stay_days = self.stay_days
        if stay_days <= 0:
            raise UserError(
                "Invalid stay duration. Please check admission and discharge dates."
            )

        bill_lines = []

        if self.admission_fee_amount > 0:
            bill_lines.append((0, 0, {
                "description": f"Admission Fee - {self.name}",
                "source_type": "admission",
                "quantity": 1.0,
                "unit_price": self.admission_fee_amount,
                "source_model": "hospital.admission",
                "source_record_id": self.id,
                "sequence": 10,
            }))

        if self.daily_rate_amount > 0:
            parts = [
                self.ward_id.display_name if self.ward_id else "",
                self.room_id.name if self.room_id else "",
                self.bed_id.display_name if self.bed_id else "",
            ]
            location_desc = " / ".join(p for p in parts if p)
            bill_lines.append((0, 0, {
                "description": (
                    f"Bed Stay Charge - {location_desc} - {int(stay_days)} day(s)"
                    if location_desc
                    else f"Bed Stay Charge - {self.name} - {int(stay_days)} day(s)"
                ),
                "source_type": "admission",
                "quantity": float(stay_days),
                "unit_price": self.daily_rate_amount,
                "source_model": "hospital.admission",
                "source_record_id": self.id,
                "sequence": 20,
            }))

        if not bill_lines:
            raise UserError(
                "No charges found. Please configure Admission Fee or Daily Rate on the ward."
            )

        bill = self.env["hospital.patient.bill"].create({
            "patient_id": self.patient_id.id,
            "physician_id": self.physician_id.id if self.physician_id else False,
            "appointment_id": self.appointment_id.id if self.appointment_id else False,
            "bill_date": fields.Date.context_today(self),
            "cashier_id": self.env.user.id,
            "currency_id": (
                self.currency_id.id
                or self.env.company.currency_id.id
            ),
            "notes": f"Generated from admission {self.name}",
            "line_ids": bill_lines,
        })

        self.write({"bill_id": bill.id})

        self.env["hospital.audit.log"].create_log(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type="update",
            description=f"Admission bill generated: {bill.name} for admission {self.name}",
        )

        return {
            "type": "ir.actions.act_window",
            "name": "Patient Bill",
            "res_model": "hospital.patient.bill",
            "res_id": bill.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_admission_bill(self):
        self.ensure_one()
        if not self.bill_id:
            raise UserError("No bill has been generated for this admission yet.")
        return {
            "type": "ir.actions.act_window",
            "name": "Patient Bill",
            "res_model": "hospital.patient.bill",
            "res_id": self.bill_id.id,
            "view_mode": "form",
            "target": "current",
        }

    # ── CRUD / workflow ──────────────────────────────────────────

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.admission.sequence") or "New"
                )
        records = super().create(vals_list)
        for rec in records:
            self.env["hospital.audit.log"].create_log(
                patient_id=rec.patient_id.id,
                model_name=self._name,
                record_id=rec.id,
                action_type="create",
                description=f"Admission created: {rec.name}",
            )
        return records

    def write(self, vals):
        old_states = {rec.id: rec.state for rec in self}
        result = super().write(vals)
        if "state" in vals:
            for rec in self:
                self.env["hospital.audit.log"].create_log(
                    patient_id=rec.patient_id.id,
                    model_name=self._name,
                    record_id=rec.id,
                    action_type="state_change",
                    old_value=old_states.get(rec.id),
                    new_value=rec.state,
                    description=f"Admission state changed: {rec.name}",
                )
        return result

    def action_confirm_admission(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError("Only Draft admissions can be confirmed.")
            if rec.bed_id:
                if rec.bed_id.state == "occupied":
                    raise UserError(
                        f"Bed '{rec.bed_id.display_name}' is already occupied. "
                        "Please select a different bed."
                    )
                if rec.bed_id.state in ("maintenance", "blocked", "cleaning"):
                    raise UserError(
                        f"Bed '{rec.bed_id.display_name}' is not available "
                        f"(current state: {rec.bed_id.state}). Please select an available bed."
                    )
                rec.bed_id.write({
                    "state": "occupied",
                    "current_admission_id": rec.id,
                })
                self.env["hospital.audit.log"].create_log(
                    patient_id=rec.patient_id.id,
                    model_name="hospital.bed",
                    record_id=rec.bed_id.id,
                    action_type="update",
                    old_value="available",
                    new_value="occupied",
                    description=f"Bed {rec.bed_id.display_name} occupied by admission {rec.name}",
                )
            rec.write({"state": "admitted"})
        return True

    def action_discharge(self):
        for rec in self:
            if rec.state not in ("admitted", "transferred"):
                raise UserError("Only Admitted or Transferred admissions can be discharged.")
            if rec.bed_id:
                rec.bed_id.write({
                    "state": "available",
                    "current_admission_id": False,
                })
                self.env["hospital.audit.log"].create_log(
                    patient_id=rec.patient_id.id,
                    model_name="hospital.bed",
                    record_id=rec.bed_id.id,
                    action_type="update",
                    old_value="occupied",
                    new_value="available",
                    description=f"Bed {rec.bed_id.display_name} freed on discharge of admission {rec.name}",
                )
            rec.write({
                "state": "discharged",
                "discharge_date": fields.Datetime.now(),
            })
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state not in ("draft", "admitted", "transferred"):
                raise UserError("Cannot cancel a discharged admission.")
            if rec.state in ("admitted", "transferred") and rec.bed_id:
                if rec.bed_id.current_admission_id.id == rec.id:
                    rec.bed_id.write({
                        "state": "available",
                        "current_admission_id": False,
                    })
                    self.env["hospital.audit.log"].create_log(
                        patient_id=rec.patient_id.id,
                        model_name="hospital.bed",
                        record_id=rec.bed_id.id,
                        action_type="update",
                        old_value="occupied",
                        new_value="available",
                        description=f"Bed {rec.bed_id.display_name} freed on cancellation of admission {rec.name}",
                    )
            rec.write({"state": "cancelled"})
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state != "cancelled":
                raise UserError("Only Cancelled admissions can be reset to Draft.")
            rec.write({"state": "draft"})
        return True

    def action_open_transfer_wizard(self):
        self.ensure_one()
        if self.state not in ("admitted", "transferred"):
            raise UserError("Transfers are only allowed for Admitted or Transferred admissions.")
        return {
            "type": "ir.actions.act_window",
            "name": "Transfer Patient",
            "res_model": "hospital.admission.transfer.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_admission_id": self.id,
                "default_current_ward_id": self.ward_id.id if self.ward_id else False,
                "default_current_room_id": self.room_id.id if self.room_id else False,
                "default_current_bed_id": self.bed_id.id if self.bed_id else False,
            },
        }

    def unlink(self):
        for rec in self:
            if rec.state not in ("draft", "cancelled"):
                self.env["hospital.audit.log"].create_log(
                    patient_id=rec.patient_id.id,
                    model_name=self._name,
                    record_id=rec.id,
                    action_type="delete_attempt",
                    description=f"Blocked delete attempt on {rec.state} admission: {rec.name}",
                )
                raise AccessError(
                    f"Cannot delete admission '{rec.name}' in state '{rec.state}'. "
                    "Only Draft or Cancelled admissions can be deleted."
                )
        return super().unlink()


class HospitalAdmissionTransfer(models.Model):
    _name = "hospital.admission.transfer"
    _description = "Admission Transfer"
    _order = "transfer_date desc"

    admission_id = fields.Many2one(
        "hospital.admission",
        required=True,
        ondelete="cascade",
    )
    transfer_date = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
    )
    from_ward_id = fields.Many2one("hospital.ward", string="From Ward")
    from_room_id = fields.Many2one("hospital.room", string="From Room")
    from_bed_id = fields.Many2one("hospital.bed", string="From Bed")
    to_ward_id = fields.Many2one("hospital.ward", string="To Ward", required=True)
    to_room_id = fields.Many2one("hospital.room", string="To Room", required=True)
    to_bed_id = fields.Many2one("hospital.bed", string="To Bed", required=True)
    reason = fields.Text()
    transferred_by = fields.Many2one(
        "res.users",
        string="Transferred By",
        default=lambda self: self.env.user,
    )


class HospitalAdmissionTransferWizard(models.TransientModel):
    _name = "hospital.admission.transfer.wizard"
    _description = "Admission Transfer Wizard"

    admission_id = fields.Many2one(
        "hospital.admission",
        required=True,
        readonly=True,
    )
    current_ward_id = fields.Many2one(
        "hospital.ward",
        string="Current Ward",
        readonly=True,
    )
    current_room_id = fields.Many2one(
        "hospital.room",
        string="Current Room",
        readonly=True,
    )
    current_bed_id = fields.Many2one(
        "hospital.bed",
        string="Current Bed",
        readonly=True,
    )
    to_ward_id = fields.Many2one(
        "hospital.ward",
        string="New Ward",
        required=True,
    )
    to_room_id = fields.Many2one(
        "hospital.room",
        string="New Room",
        required=True,
    )
    to_bed_id = fields.Many2one(
        "hospital.bed",
        string="New Bed",
        required=True,
    )
    reason = fields.Text()

    def action_do_transfer(self):
        self.ensure_one()
        admission = self.admission_id
        if admission.state not in ("admitted", "transferred"):
            raise UserError(
                "Transfer is only allowed for Admitted or Transferred admissions."
            )
        if self.to_bed_id.state != "available":
            raise UserError(
                f"Bed '{self.to_bed_id.display_name}' is not available "
                f"(current state: {self.to_bed_id.state}). Please select an available bed."
            )
        old_bed = admission.bed_id
        if old_bed:
            old_bed.write({"state": "available", "current_admission_id": False})
            self.env["hospital.audit.log"].create_log(
                patient_id=admission.patient_id.id,
                model_name="hospital.bed",
                record_id=old_bed.id,
                action_type="update",
                old_value="occupied",
                new_value="available",
                description=f"Bed {old_bed.display_name} freed on transfer from admission {admission.name}",
            )
        self.to_bed_id.write({
            "state": "occupied",
            "current_admission_id": admission.id,
        })
        self.env["hospital.audit.log"].create_log(
            patient_id=admission.patient_id.id,
            model_name="hospital.bed",
            record_id=self.to_bed_id.id,
            action_type="update",
            old_value="available",
            new_value="occupied",
            description=f"Bed {self.to_bed_id.display_name} occupied on transfer for admission {admission.name}",
        )
        self.env["hospital.admission.transfer"].create({
            "admission_id": admission.id,
            "transfer_date": fields.Datetime.now(),
            "from_ward_id": old_bed.ward_id.id if old_bed and old_bed.ward_id else False,
            "from_room_id": old_bed.room_id.id if old_bed and old_bed.room_id else False,
            "from_bed_id": old_bed.id if old_bed else False,
            "to_ward_id": self.to_ward_id.id,
            "to_room_id": self.to_room_id.id,
            "to_bed_id": self.to_bed_id.id,
            "reason": self.reason,
            "transferred_by": self.env.user.id,
        })
        admission.write({
            "state": "transferred",
            "ward_id": self.to_ward_id.id,
            "room_id": self.to_room_id.id,
            "bed_id": self.to_bed_id.id,
        })
        self.env["hospital.audit.log"].create_log(
            patient_id=admission.patient_id.id,
            model_name=admission._name,
            record_id=admission.id,
            action_type="state_change",
            old_value=admission.state,
            new_value="transferred",
            description=(
                f"Admission {admission.name} transferred to "
                f"{self.to_bed_id.display_name} in {self.to_room_id.display_name}"
            ),
        )
        return {"type": "ir.actions.act_window_close"}

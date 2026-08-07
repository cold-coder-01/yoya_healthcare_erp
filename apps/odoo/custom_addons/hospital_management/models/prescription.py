from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalPrescription(models.Model):
    _name = "hospital.prescription"
    _description = "Prescription"
    _order = "prescription_date desc, id desc"

    name = fields.Char(readonly=True, copy=False, default="New")
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        required=True,
    )
    appointment_id = fields.Many2one("hospital.appointment")
    diagnosis_id = fields.Many2one("hospital.patient.diagnosis")
    prescription_date = fields.Date(
        default=fields.Date.context_today,
        required=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("dispensed", "Dispensed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
    )
    line_ids = fields.One2many(
        "hospital.prescription.line",
        "prescription_id",
        string="Medicine Lines",
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for prescription in self:
            if prescription.name and prescription.name != "New":
                prescription.display_name = prescription.name
            elif prescription.patient_id:
                prescription.display_name = f"New Prescription - {prescription.patient_id.display_name}"
            else:
                prescription.display_name = "New Prescription"

    @api.onchange("appointment_id")
    def _onchange_appointment_id(self):
        if not self.appointment_id:
            return
        if self.appointment_id.patient_id:
            self.patient_id = self.appointment_id.patient_id
        if self.appointment_id.doctor_id:
            self.physician_id = self.appointment_id.doctor_id

    @api.onchange("diagnosis_id")
    def _onchange_diagnosis_id(self):
        if not self.diagnosis_id:
            return
        if self.diagnosis_id.patient_id:
            self.patient_id = self.diagnosis_id.patient_id
        if self.diagnosis_id.physician_id:
            self.physician_id = self.diagnosis_id.physician_id
        if self.diagnosis_id.appointment_id and not self.appointment_id:
            self.appointment_id = self.diagnosis_id.appointment_id

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = sequence.next_by_code(
                    "hospital.prescription.sequence"
                ) or "New"
        prescriptions = super().create(vals_list)
        for prescription in prescriptions:
            prescription._create_audit_log(
                action_type="create",
                description="Prescription created.",
                new_value=prescription._audit_summary(
                    ["name", "patient_id", "physician_id", "prescription_date", "state"]
                ),
            )
        return prescriptions

    def write(self, vals):
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            prescription.id: prescription._audit_summary(tracked_vals.keys())
            for prescription in self
        }
        result = super().write(vals)
        if tracked_vals and not self.env.context.get("skip_prescription_write_audit"):
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Prescription archived."
                if action_type == "archive"
                else "Prescription updated."
            )
            for prescription in self:
                prescription._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(prescription.id),
                    new_value=prescription._audit_summary(tracked_vals.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for prescription in self:
                prescription._create_audit_log(
                    action_type="delete_attempt",
                    description="Prescription deletion blocked.",
                    old_value=prescription._audit_summary(
                        ["name", "patient_id", "physician_id", "state"]
                    ),
                )
            raise UserError(
                "Prescriptions are sensitive health records. Cancel or archive them instead of deleting."
            )
        return super().unlink()

    def action_confirm(self):
        for prescription in self.filtered(lambda record: record.state == "draft"):
            old_state = prescription.state
            prescription.with_context(skip_prescription_write_audit=True).write(
                {"state": "confirmed"}
            )
            prescription._log_state_change(old_state)

    def action_mark_dispensed(self):
        for prescription in self.filtered(lambda record: record.state == "confirmed"):
            old_state = prescription.state
            prescription.with_context(skip_prescription_write_audit=True).write(
                {"state": "dispensed"}
            )
            prescription._log_state_change(old_state)

    def action_cancel(self):
        for prescription in self.filtered(
            lambda record: record.state in ("draft", "confirmed")
        ):
            old_state = prescription.state
            prescription.with_context(skip_prescription_write_audit=True).write(
                {"state": "cancelled"}
            )
            prescription._log_state_change(old_state)

    def action_reset_to_draft(self):
        manager_or_admin = self.env.user.has_group(
            "hospital_management.group_hospital_manager"
        ) or self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        )
        for prescription in self.filtered(
            lambda record: record.state in ("cancelled", "dispensed")
        ):
            if prescription.state == "dispensed" and not manager_or_admin:
                raise UserError(
                    "Only Hospital Managers or System Administrators can reset dispensed prescriptions."
                )
            old_state = prescription.state
            prescription.with_context(skip_prescription_write_audit=True).write(
                {"state": "draft"}
            )
            prescription._log_state_change(old_state)

    def _log_state_change(self, old_state):
        self._create_audit_log(
            action_type="state_change",
            description="Prescription state changed.",
            old_value=f"State: {old_state}",
            new_value=f"State: {self.state}",
        )

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
        for prescription in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=prescription.patient_id.id,
                model_name=prescription._name,
                record_id=prescription.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )


class HospitalPrescriptionLine(models.Model):
    _name = "hospital.prescription.line"
    _description = "Prescription Line"
    _order = "sequence, id"

    prescription_id = fields.Many2one(
        "hospital.prescription",
        required=True,
        ondelete="cascade",
    )
    medicine_name = fields.Char(required=True)
    dosage = fields.Char()
    frequency = fields.Char()
    duration = fields.Char()
    route = fields.Selection(
        [
            ("oral", "Oral"),
            ("injection", "Injection"),
            ("topical", "Topical"),
            ("inhalation", "Inhalation"),
            ("eye_drop", "Eye Drop"),
            ("ear_drop", "Ear Drop"),
            ("nasal", "Nasal"),
            ("other", "Other"),
        ],
    )
    quantity = fields.Float()
    instructions = fields.Text()
    sequence = fields.Integer(default=10)

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HospitalAppointment(models.Model):
    _name = "hospital.appointment"
    _description = "Appointment"
    _order = "appointment_date desc, appointment_code desc"

    appointment_code = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default="New",
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        tracking=True,
        ondelete="restrict",
    )
    doctor_id = fields.Many2one("hospital.doctor", tracking=True)
    department_id = fields.Many2one("hospital.department", tracking=True)
    appointment_date = fields.Datetime(required=True, tracking=True)
    duration = fields.Float(
        string="Duration",
        help="Duration in hours",
        default=1.0,
    )
    reason = fields.Text()
    notes = fields.Text()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("in_consultation", "In Consultation"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="draft",
        tracking=True,
    )
    active = fields.Boolean(default=True)

    @api.depends("appointment_code", "patient_id")
    def _compute_display_name(self):
        for appointment in self:
            patient_name = appointment.patient_id.display_name or "Patient"
            if appointment.appointment_code and appointment.appointment_code != "New":
                appointment.display_name = f"{appointment.appointment_code} - {patient_name}"
            else:
                appointment.display_name = patient_name

    @api.constrains("appointment_date", "duration", "patient_id")
    def _check_required_appointment_values(self):
        for appointment in self:
            if not appointment.patient_id:
                raise ValidationError("Patient is required for an appointment.")
            if not appointment.appointment_date:
                raise ValidationError("Appointment date is required.")
            if appointment.duration <= 0:
                raise ValidationError("Appointment duration must be greater than zero.")

    @api.onchange("doctor_id")
    def _onchange_doctor_id(self):
        if self.doctor_id and self.doctor_id.department_id and not self.department_id:
            self.department_id = self.doctor_id.department_id

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("appointment_code") or vals.get("appointment_code") == "New":
                vals["appointment_code"] = sequence.next_by_code(
                    "hospital.appointment.sequence"
                ) or "New"
            if vals.get("doctor_id") and not vals.get("department_id"):
                doctor = self.env["hospital.doctor"].browse(vals["doctor_id"])
                if doctor.department_id:
                    vals["department_id"] = doctor.department_id.id
        return super().create(vals_list)

    def action_confirm(self):
        for appointment in self.filtered(lambda record: record.state == "draft"):
            appointment.state = "confirmed"
            appointment._create_audit_log("Appointment confirmed.")

    def action_start_consultation(self):
        for appointment in self.filtered(lambda record: record.state == "confirmed"):
            appointment.state = "in_consultation"
            appointment._create_audit_log("Appointment consultation started.")

    def action_done(self):
        for appointment in self.filtered(
            lambda record: record.state in ("confirmed", "in_consultation")
        ):
            appointment.state = "done"
            appointment._create_audit_log("Appointment marked as done.")

    def action_cancel(self):
        for appointment in self.filtered(
            lambda record: record.state in ("draft", "confirmed", "in_consultation")
        ):
            appointment.state = "cancelled"
            appointment._create_audit_log("Appointment cancelled.")

    def action_reset_to_draft(self):
        appointments = self.filtered(lambda record: record.state in ("cancelled", "done"))
        appointments.write({"state": "draft"})
        for appointment in appointments:
            appointment._create_audit_log("Appointment reset to draft.")

    def _create_audit_log(self, description):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for appointment in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=appointment.patient_id.id,
                model_name=appointment._name,
                record_id=appointment.id,
                action_type="state_change",
                description=description,
                new_value=f"State: {appointment.state}",
            )

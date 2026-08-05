from odoo import api, fields, models
from odoo.exceptions import UserError


class YoyaEmrAppointment(models.Model):
    _name = "yoya.emr.appointment"
    _description = "YOYA EMR Appointment"
    _order = "appointment_date desc, id desc"

    name = fields.Char(readonly=True, copy=False, default="New")
    patient_id = fields.Many2one(
        "yoya.emr.patient",
        required=True,
        ondelete="restrict",
    )
    appointment_date = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
    )
    doctor_name = fields.Char(required=True)
    reason = fields.Text()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("waiting", "Waiting"),
            ("in_consultation", "In Consultation"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
    )
    started_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "yoya.emr.appointment"
                ) or "New"
        return super().create(vals_list)

    def write(self, vals):
        if "state" in vals and not self.env.context.get("yoya_allow_state_write"):
            raise UserError("Use appointment actions to change the state.")
        return super().write(vals)

    def action_confirm(self):
        for appointment in self:
            if appointment.state != "draft":
                raise UserError("Only draft appointments can be confirmed.")
            appointment.with_context(yoya_allow_state_write=True).write(
                {"state": "waiting"}
            )

    def action_start_consultation(self):
        for appointment in self:
            if appointment.state != "waiting":
                raise UserError("Only waiting appointments can start consultation.")
            appointment.with_context(yoya_allow_state_write=True).write(
                {
                    "state": "in_consultation",
                    "started_at": fields.Datetime.now(),
                }
            )

    def action_complete(self):
        for appointment in self:
            if appointment.state != "in_consultation":
                raise UserError("Only appointments in consultation can be completed.")
            appointment.with_context(yoya_allow_state_write=True).write(
                {
                    "state": "completed",
                    "completed_at": fields.Datetime.now(),
                }
            )

    def action_cancel(self):
        for appointment in self:
            if appointment.state == "completed":
                raise UserError("Completed appointments cannot be cancelled.")
            appointment.with_context(yoya_allow_state_write=True).write(
                {"state": "cancelled"}
            )

    def action_reset_to_draft(self):
        for appointment in self:
            if appointment.state != "cancelled":
                raise UserError("Only cancelled appointments can be reset to draft.")
            appointment.with_context(yoya_allow_state_write=True).write(
                {"state": "draft"}
            )

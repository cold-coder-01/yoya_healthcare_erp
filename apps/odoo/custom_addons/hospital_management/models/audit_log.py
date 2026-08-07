from odoo import api, fields, models
from odoo.exceptions import AccessError


class HospitalAuditLog(models.Model):
    _name = "hospital.audit.log"
    _description = "Hospital Audit Log"
    _order = "action_date desc, id desc"

    user_id = fields.Many2one(
        "res.users",
        required=True,
        readonly=True,
        default=lambda self: self.env.user,
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        readonly=True,
        ondelete="set null",
    )
    model_name = fields.Char(required=True, readonly=True)
    record_id = fields.Integer(readonly=True)
    action_type = fields.Selection(
        [
            ("create", "Create"),
            ("read", "Read"),
            ("update", "Update"),
            ("delete_attempt", "Delete Attempt"),
            ("archive", "Archive"),
            ("consent_change", "Consent Change"),
            ("state_change", "State Change"),
        ],
        required=True,
        readonly=True,
    )
    action_date = fields.Datetime(
        required=True,
        readonly=True,
        default=fields.Datetime.now,
    )
    description = fields.Text(readonly=True)
    old_value = fields.Text(readonly=True)
    new_value = fields.Text(readonly=True)
    active = fields.Boolean(default=True, readonly=True)

    @api.model
    def create_log(
        self,
        patient_id=False,
        model_name=False,
        record_id=False,
        action_type=False,
        description=False,
        old_value=False,
        new_value=False,
    ):
        user_id = self.env.context.get("audit_user_id") or self.env.user.id
        return self.sudo().create(
            {
                "user_id": user_id,
                "patient_id": patient_id or False,
                "model_name": model_name,
                "record_id": record_id or 0,
                "action_type": action_type,
                "action_date": fields.Datetime.now(),
                "description": description,
                "old_value": old_value,
                "new_value": new_value,
            }
        )

    def write(self, vals):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            raise AccessError("Only Hospital System Administrators can modify audit logs.")
        return super().write(vals)

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            raise AccessError("Only Hospital System Administrators can delete audit logs.")
        return super().unlink()

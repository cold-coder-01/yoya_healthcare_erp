from odoo import api, fields, models


class HospitalPatientFamily(models.Model):
    _name = "hospital.patient.family"
    _description = "Patient Family Member"
    _order = "id desc"

    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
    )
    name = fields.Char(required=True)
    relation = fields.Selection(
        [
            ("father", "Father"),
            ("mother", "Mother"),
            ("spouse", "Spouse"),
            ("son", "Son"),
            ("daughter", "Daughter"),
            ("brother", "Brother"),
            ("sister", "Sister"),
            ("guardian", "Guardian"),
            ("other", "Other"),
        ]
    )
    phone = fields.Char()
    email = fields.Char()
    address = fields.Text()
    is_emergency_contact = fields.Boolean(default=False)
    has_medical_history = fields.Boolean(default=False)
    medical_history_notes = fields.Text()
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        family_members = super().create(vals_list)
        for family_member in family_members:
            family_member._create_audit_log(
                action_type="create",
                description="Patient family member created.",
                new_value=family_member._audit_summary(
                    [
                        "patient_id",
                        "name",
                        "relation",
                        "phone",
                        "is_emergency_contact",
                        "has_medical_history",
                    ]
                ),
            )
        return family_members

    def write(self, vals):
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            family_member.id: family_member._audit_summary(tracked_vals.keys())
            for family_member in self
        }
        result = super().write(vals)
        if tracked_vals:
            for family_member in self:
                family_member._create_audit_log(
                    action_type="update",
                    description="Patient family member updated.",
                    old_value=old_values.get(family_member.id),
                    new_value=family_member._audit_summary(tracked_vals.keys()),
                )
        return result

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
        audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type=action_type,
            description=description,
            old_value=old_value,
            new_value=new_value,
        )

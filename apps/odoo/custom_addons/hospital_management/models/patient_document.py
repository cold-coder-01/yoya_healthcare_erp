from odoo import api, fields, models


class HospitalPatientDocument(models.Model):
    _name = "hospital.patient.document"
    _description = "Patient Document"
    _order = "document_date desc, id desc"

    name = fields.Char(required=True)
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
    )
    document_type = fields.Selection(
        [
            ("identification", "Identification"),
            ("referral", "Referral"),
            ("consent", "Consent"),
            ("medical_record", "Medical Record"),
            ("lab_result", "Lab Result"),
            ("insurance", "Insurance"),
            ("other", "Other"),
        ],
        default="other",
    )
    document_date = fields.Date(default=fields.Date.context_today)
    attachment = fields.Binary()
    filename = fields.Char()
    description = fields.Text()
    uploaded_by = fields.Many2one(
        "res.users",
        readonly=True,
        default=lambda self: self.env.user,
    )
    active = fields.Boolean(default=True)

    @api.model_create_multi
    def create(self, vals_list):
        documents = super().create(vals_list)
        for document in documents:
            document._create_audit_log(
                action_type="create",
                description="Patient document created.",
                new_value=document._audit_summary(
                    ("name", "document_type", "document_date", "filename", "description")
                ),
            )
        return documents

    def write(self, vals):
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "attachment")
        }
        old_values = {
            document.id: document._audit_summary(tracked_vals.keys())
            for document in self
        }
        result = super().write(vals)
        if tracked_vals:
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Patient document archived."
                if action_type == "archive"
                else "Patient document updated."
            )
            for document in self:
                document._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(document.id),
                    new_value=document._audit_summary(tracked_vals.keys()),
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

from odoo import api, fields, models


class HospitalPatientDiagnosis(models.Model):
    _name = "hospital.patient.diagnosis"
    _description = "Patient Diagnosis"
    _order = "diagnosis_date desc, id desc"

    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    disease_id = fields.Many2one(
        "hospital.disease",
        required=True,
        tracking=True,
    )
    category_id = fields.Many2one(
        "hospital.disease.category",
        related="disease_id.category_id",
        store=True,
        readonly=True,
    )
    disease_code = fields.Char(
        related="disease_id.code",
        string="ICD Code",
        readonly=True,
    )
    disease_description = fields.Text(
        related="disease_id.description",
        string="Description",
        readonly=True,
    )
    diagnosis_date = fields.Date(
        default=fields.Date.context_today,
        tracking=True,
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        tracking=True,
    )
    appointment_id = fields.Many2one(
        "hospital.appointment",
        tracking=True,
    )
    diagnosis_type = fields.Selection(
        [
            ("primary", "Primary"),
            ("secondary", "Secondary"),
            ("differential", "Differential"),
            ("history", "History"),
        ],
        tracking=True,
    )
    severity = fields.Selection(
        [
            ("mild", "Mild"),
            ("moderate", "Moderate"),
            ("severe", "Severe"),
            ("critical", "Critical"),
        ],
        tracking=True,
    )
    status = fields.Selection(
        [
            ("active", "Active"),
            ("resolved", "Resolved"),
            ("chronic", "Chronic"),
            ("suspected", "Suspected"),
        ],
        tracking=True,
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("disease_id", "patient_id")
    def _compute_display_name(self):
        for record in self:
            disease = record.disease_id.name or ""
            patient = record.patient_id.display_name or ""
            if disease and patient:
                record.display_name = f"{disease} - {patient}"
            elif disease:
                record.display_name = disease
            elif patient:
                record.display_name = patient
            else:
                record.display_name = "New Diagnosis"

    @api.model_create_multi
    def create(self, vals_list):
        diagnoses = super().create(vals_list)
        for diagnosis in diagnoses:
            diagnosis._create_audit_log(
                action_type="create",
                description="Patient diagnosis created.",
                new_value=f"Diagnosis: {diagnosis.disease_id.name} for {diagnosis.patient_id.display_name}",
            )
        return diagnoses

    def write(self, vals):
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            diagnosis.id: diagnosis._audit_summary(tracked_vals.keys())
            for diagnosis in self
        }
        result = super().write(vals)
        if tracked_vals:
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Patient diagnosis archived."
                if action_type == "archive"
                else "Patient diagnosis updated."
            )
            for diagnosis in self:
                diagnosis._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(diagnosis.id),
                    new_value=diagnosis._audit_summary(tracked_vals.keys()),
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

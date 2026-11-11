from odoo import fields, models


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    surgery_ids = fields.One2many(
        "hospital.surgery.request",
        "patient_id",
        string="Surgeries",
    )
    surgery_count = fields.Integer(
        compute="_compute_surgery_count",
        string="Surgeries",
    )

    def _compute_surgery_count(self):
        for patient in self:
            patient.surgery_count = (
                self.env["hospital.surgery.request"].search_count(
                    [("patient_id", "=", patient.id)]
                )
                if patient.id
                else 0
            )

    def action_view_surgeries(self):
        self.ensure_one()
        latest = self.env["hospital.surgery.request"].search(
            [("patient_id", "=", self.id)],
            order="request_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Surgeries",
            "res_model": "hospital.surgery.request",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest:
            action["view_mode"] = "form,list"
            action["res_id"] = latest.id
        return action


class HospitalAdmission(models.Model):
    _inherit = "hospital.admission"

    surgery_ids = fields.One2many(
        "hospital.surgery.request",
        "admission_id",
        string="Surgeries",
    )
    surgery_count = fields.Integer(
        compute="_compute_admission_surgery_count",
        string="Surgeries",
    )

    def _compute_admission_surgery_count(self):
        for admission in self:
            admission.surgery_count = (
                self.env["hospital.surgery.request"].search_count(
                    [("admission_id", "=", admission.id)]
                )
                if admission.id
                else 0
            )

    def action_view_surgeries(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Surgeries",
            "res_model": "hospital.surgery.request",
            "view_mode": "list,form",
            "domain": [("admission_id", "=", self.id)],
            "context": {
                "default_patient_id": self.patient_id.id,
                "default_admission_id": self.id,
            },
        }

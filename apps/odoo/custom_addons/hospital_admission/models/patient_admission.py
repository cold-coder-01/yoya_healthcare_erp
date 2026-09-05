from odoo import fields, models


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    admission_ids = fields.One2many(
        "hospital.admission",
        "patient_id",
        string="Admissions",
    )
    admission_count = fields.Integer(
        compute="_compute_admission_count",
        string="Admissions",
    )

    def _compute_admission_count(self):
        for patient in self:
            patient.admission_count = (
                self.env["hospital.admission"].search_count(
                    [("patient_id", "=", patient.id)]
                )
                if patient.id
                else 0
            )

    def action_view_patient_admissions(self):
        self.ensure_one()
        Admission = self.env["hospital.admission"]
        latest = Admission.search(
            [("patient_id", "=", self.id)],
            order="admission_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Admissions",
            "res_model": "hospital.admission",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest:
            action["view_mode"] = "form,list"
            action["res_id"] = latest.id
        return action

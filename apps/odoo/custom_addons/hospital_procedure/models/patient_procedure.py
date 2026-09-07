from odoo import fields, models


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    procedure_request_ids = fields.One2many(
        "hospital.procedure.request",
        "patient_id",
        string="Procedure Requests",
    )
    procedure_request_count = fields.Integer(
        compute="_compute_procedure_request_count",
        string="Procedures",
    )

    def _compute_procedure_request_count(self):
        for patient in self:
            if not patient.id:
                patient.procedure_request_count = 0
                continue
            patient.procedure_request_count = self.env["hospital.procedure.request"].search_count(
                [("patient_id", "=", patient.id)]
            )

    def action_view_procedure_requests(self):
        self.ensure_one()
        latest = self.env["hospital.procedure.request"].search(
            [("patient_id", "=", self.id)],
            order="request_datetime desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Procedures",
            "res_model": "hospital.procedure.request",
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

    procedure_request_ids = fields.One2many(
        "hospital.procedure.request",
        "admission_id",
        string="Procedure Requests",
    )
    procedure_request_count = fields.Integer(
        compute="_compute_admission_procedure_request_count",
        string="Procedures",
    )

    def _compute_admission_procedure_request_count(self):
        for admission in self:
            if not admission.id:
                admission.procedure_request_count = 0
                continue
            admission.procedure_request_count = self.env["hospital.procedure.request"].search_count(
                [("admission_id", "=", admission.id)]
            )

    def action_view_admission_procedures(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Procedures",
            "res_model": "hospital.procedure.request",
            "view_mode": "list,form",
            "domain": [("admission_id", "=", self.id)],
            "context": {
                "default_patient_id": self.patient_id.id,
                "default_admission_id": self.id,
            },
        }

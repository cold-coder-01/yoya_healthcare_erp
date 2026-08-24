from odoo import fields, models


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    radiology_request_ids = fields.One2many(
        "hospital.radiology.request",
        "patient_id",
        string="Radiology Requests",
    )
    radiology_result_ids = fields.One2many(
        "hospital.radiology.result",
        "patient_id",
        string="Radiology Results",
    )
    radiology_request_count = fields.Integer(
        compute="_compute_radiology_record_counts",
        string="Radiology Requests",
    )
    radiology_result_count = fields.Integer(
        compute="_compute_radiology_record_counts",
        string="Radiology Results",
    )

    def _compute_radiology_record_counts(self):
        Request = self.env["hospital.radiology.request"]
        Result = self.env["hospital.radiology.result"]
        for patient in self:
            if not patient.id:
                patient.radiology_request_count = 0
                patient.radiology_result_count = 0
                continue
            patient.radiology_request_count = Request.search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.radiology_result_count = Result.search_count(
                [("patient_id", "=", patient.id)]
            )

    def action_view_radiology_requests(self):
        self.ensure_one()
        Request = self.env["hospital.radiology.request"]
        latest_request = Request.search(
            [("patient_id", "=", self.id)],
            order="request_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Radiology Request",
            "res_model": "hospital.radiology.request",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_request:
            action["res_id"] = latest_request.id
        return action

    def action_view_radiology_results(self):
        self.ensure_one()
        Result = self.env["hospital.radiology.result"]
        latest_result = Result.search(
            [("patient_id", "=", self.id)],
            order="result_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Radiology Result",
            "res_model": "hospital.radiology.result",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_result:
            action["res_id"] = latest_result.id
        return action

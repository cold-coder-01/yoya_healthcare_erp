from odoo import fields, models


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    bill_ids = fields.One2many(
        "hospital.patient.bill",
        "patient_id",
        string="Bills",
    )
    bill_count = fields.Integer(
        compute="_compute_bill_count",
        string="Bills",
    )

    def _compute_bill_count(self):
        for patient in self:
            patient.bill_count = (
                self.env["hospital.patient.bill"].search_count(
                    [("patient_id", "=", patient.id)]
                )
                if patient.id
                else 0
            )

    def action_view_patient_bills(self):
        self.ensure_one()
        Bill = self.env["hospital.patient.bill"]
        latest_bill = Bill.search(
            [("patient_id", "=", self.id)],
            order="bill_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Bills",
            "res_model": "hospital.patient.bill",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_bill:
            action["view_mode"] = "form,list"
            action["res_id"] = latest_bill.id
        return action

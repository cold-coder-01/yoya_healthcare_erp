from odoo import fields, models


class HospitalPatientPharmacy(models.Model):
    _inherit = "hospital.patient"

    pharmacy_dispense_ids = fields.One2many(
        "hospital.pharmacy.dispense",
        "patient_id",
        string="Pharmacy Dispenses",
    )
    pharmacy_dispense_count = fields.Integer(
        compute="_compute_pharmacy_dispense_count",
        string="Pharmacy Dispenses",
    )

    def _compute_pharmacy_dispense_count(self):
        Dispense = self.env["hospital.pharmacy.dispense"]
        for patient in self:
            if not patient.id:
                patient.pharmacy_dispense_count = 0
                continue
            patient.pharmacy_dispense_count = Dispense.search_count(
                [("patient_id", "=", patient.id)]
            )

    def action_view_pharmacy_dispenses(self):
        self.ensure_one()
        Dispense = self.env["hospital.pharmacy.dispense"]
        latest_dispense = Dispense.search(
            [("patient_id", "=", self.id)],
            order="dispense_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Pharmacy Dispenses",
            "res_model": "hospital.pharmacy.dispense",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_dispense:
            action["res_id"] = latest_dispense.id
        return action

from odoo import fields, models


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    coverage_ids = fields.One2many(
        "hospital.patient.coverage",
        "patient_id",
        string="Coverages",
    )
    guarantee_letter_ids = fields.One2many(
        "hospital.guarantee.letter",
        "patient_id",
        string="Guarantee Letters",
    )
    insurance_claim_ids = fields.One2many(
        "hospital.insurance.claim",
        "patient_id",
        string="Insurance Claims",
    )
    coverage_count = fields.Integer(compute="_compute_insurance_counts")
    guarantee_count = fields.Integer(compute="_compute_insurance_counts")
    claim_count = fields.Integer(compute="_compute_insurance_counts")

    def _compute_insurance_counts(self):
        Coverage = self.env["hospital.patient.coverage"]
        Guarantee = self.env["hospital.guarantee.letter"]
        Claim = self.env["hospital.insurance.claim"]
        for patient in self:
            if not patient.id:
                patient.coverage_count = 0
                patient.guarantee_count = 0
                patient.claim_count = 0
                continue
            patient.coverage_count = Coverage.search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.guarantee_count = Guarantee.search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.claim_count = Claim.search_count(
                [("patient_id", "=", patient.id)]
            )

    def action_view_coverages(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Patient Coverages",
            "res_model": "hospital.patient.coverage",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }

    def action_view_guarantees(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Guarantee Letters",
            "res_model": "hospital.guarantee.letter",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }

    def action_view_claims(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Insurance Claims",
            "res_model": "hospital.insurance.claim",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }


class HospitalAdmission(models.Model):
    _inherit = "hospital.admission"

    coverage_id = fields.Many2one(
        "hospital.patient.coverage",
        string="Coverage",
    )
    guarantee_id = fields.Many2one(
        "hospital.guarantee.letter",
        string="Guarantee Letter",
    )
    insurance_claim_ids = fields.One2many(
        "hospital.insurance.claim",
        "admission_id",
        string="Insurance Claims",
    )
    claim_count = fields.Integer(compute="_compute_admission_claim_count")

    def _compute_admission_claim_count(self):
        Claim = self.env["hospital.insurance.claim"]
        for admission in self:
            admission.claim_count = (
                Claim.search_count([("admission_id", "=", admission.id)])
                if admission.id
                else 0
            )

    def action_view_admission_claims(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Insurance Claims",
            "res_model": "hospital.insurance.claim",
            "view_mode": "list,form",
            "domain": [("admission_id", "=", self.id)],
            "context": {
                "default_admission_id": self.id,
                "default_patient_id": self.patient_id.id,
            },
        }

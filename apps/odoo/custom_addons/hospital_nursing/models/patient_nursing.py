from odoo import fields, models


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    nursing_round_ids = fields.One2many(
        "hospital.nursing.round",
        "patient_id",
        string="Nursing Rounds",
    )
    nursing_note_ids = fields.One2many(
        "hospital.nursing.note",
        "patient_id",
        string="Nursing Notes",
    )
    care_plan_ids = fields.One2many(
        "hospital.nursing.care.plan",
        "patient_id",
        string="Care Plans",
    )
    medication_administration_ids = fields.One2many(
        "hospital.medication.administration",
        "patient_id",
        string="Medication Administrations",
    )
    nursing_round_count = fields.Integer(
        compute="_compute_nursing_counts",
        string="Nursing Rounds",
    )
    nursing_note_count = fields.Integer(
        compute="_compute_nursing_counts",
        string="Nursing Notes",
    )
    care_plan_count = fields.Integer(
        compute="_compute_nursing_counts",
        string="Care Plans",
    )
    medication_administration_count = fields.Integer(
        compute="_compute_nursing_counts",
        string="MAR",
    )

    def _compute_nursing_counts(self):
        for patient in self:
            if not patient.id:
                patient.nursing_round_count = 0
                patient.nursing_note_count = 0
                patient.care_plan_count = 0
                patient.medication_administration_count = 0
                continue
            patient.nursing_round_count = self.env["hospital.nursing.round"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.nursing_note_count = self.env["hospital.nursing.note"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.care_plan_count = self.env["hospital.nursing.care.plan"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.medication_administration_count = self.env["hospital.medication.administration"].search_count(
                [("patient_id", "=", patient.id)]
            )

    def action_view_nursing_rounds(self):
        self.ensure_one()
        latest = self.env["hospital.nursing.round"].search(
            [("patient_id", "=", self.id)],
            order="round_datetime desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Nursing Rounds",
            "res_model": "hospital.nursing.round",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest:
            action["view_mode"] = "form,list"
            action["res_id"] = latest.id
        return action

    def action_view_nursing_notes(self):
        self.ensure_one()
        latest = self.env["hospital.nursing.note"].search(
            [("patient_id", "=", self.id)],
            order="note_datetime desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Nursing Notes",
            "res_model": "hospital.nursing.note",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest:
            action["view_mode"] = "form,list"
            action["res_id"] = latest.id
        return action

    def action_view_care_plans(self):
        self.ensure_one()
        latest = self.env["hospital.nursing.care.plan"].search(
            [("patient_id", "=", self.id)],
            order="plan_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Care Plans",
            "res_model": "hospital.nursing.care.plan",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest:
            action["view_mode"] = "form,list"
            action["res_id"] = latest.id
        return action

    def action_view_medication_administrations(self):
        self.ensure_one()
        latest = self.env["hospital.medication.administration"].search(
            [("patient_id", "=", self.id)],
            order="scheduled_datetime desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Medication Administration",
            "res_model": "hospital.medication.administration",
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

    nursing_round_count = fields.Integer(
        compute="_compute_admission_nursing_counts",
        string="Nursing Rounds",
    )
    nursing_note_count = fields.Integer(
        compute="_compute_admission_nursing_counts",
        string="Nursing Notes",
    )
    care_plan_count = fields.Integer(
        compute="_compute_admission_nursing_counts",
        string="Care Plans",
    )
    medication_administration_count = fields.Integer(
        compute="_compute_admission_nursing_counts",
        string="MAR",
    )

    def _compute_admission_nursing_counts(self):
        for admission in self:
            if not admission.id:
                admission.nursing_round_count = 0
                admission.nursing_note_count = 0
                admission.care_plan_count = 0
                admission.medication_administration_count = 0
                continue
            admission.nursing_round_count = self.env["hospital.nursing.round"].search_count(
                [("admission_id", "=", admission.id)]
            )
            admission.nursing_note_count = self.env["hospital.nursing.note"].search_count(
                [("admission_id", "=", admission.id)]
            )
            admission.care_plan_count = self.env["hospital.nursing.care.plan"].search_count(
                [("admission_id", "=", admission.id)]
            )
            admission.medication_administration_count = self.env["hospital.medication.administration"].search_count(
                [("admission_id", "=", admission.id)]
            )

    def action_view_admission_nursing_rounds(self):
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": "Nursing Rounds",
            "res_model": "hospital.nursing.round",
            "view_mode": "list,form",
            "domain": [("admission_id", "=", self.id)],
            "context": {"default_patient_id": self.patient_id.id, "default_admission_id": self.id},
        }
        return action

    def action_view_admission_nursing_notes(self):
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": "Nursing Notes",
            "res_model": "hospital.nursing.note",
            "view_mode": "list,form",
            "domain": [("admission_id", "=", self.id)],
            "context": {"default_patient_id": self.patient_id.id, "default_admission_id": self.id},
        }
        return action

    def action_view_admission_care_plans(self):
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": "Care Plans",
            "res_model": "hospital.nursing.care.plan",
            "view_mode": "list,form",
            "domain": [("admission_id", "=", self.id)],
            "context": {"default_patient_id": self.patient_id.id, "default_admission_id": self.id},
        }
        return action

    def action_view_admission_medication_administrations(self):
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": "Medication Administration",
            "res_model": "hospital.medication.administration",
            "view_mode": "list,form",
            "domain": [("admission_id", "=", self.id)],
            "context": {"default_patient_id": self.patient_id.id, "default_admission_id": self.id},
        }
        return action

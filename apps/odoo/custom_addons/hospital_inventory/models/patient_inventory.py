from odoo import fields, models


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    inventory_movement_ids = fields.One2many(
        "hospital.stock.movement",
        "patient_id",
        string="Inventory Movements",
    )
    inventory_movement_count = fields.Integer(
        compute="_compute_inventory_movement_count",
        string="Inventory Usage",
    )
    inventory_consumption_ids = fields.One2many(
        "hospital.stock.consumption",
        "patient_id",
        string="Inventory Consumptions",
    )

    def _compute_inventory_movement_count(self):
        Movement = self.env["hospital.stock.movement"]
        for patient in self:
            patient.inventory_movement_count = (
                Movement.search_count([("patient_id", "=", patient.id)]) if patient.id else 0
            )

    def action_view_inventory_movements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Usage",
            "res_model": "hospital.stock.movement",
            "view_mode": "list,form",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }


class HospitalAdmission(models.Model):
    _inherit = "hospital.admission"

    inventory_movement_ids = fields.One2many(
        "hospital.stock.movement",
        "admission_id",
        string="Inventory Movements",
    )
    inventory_movement_count = fields.Integer(
        compute="_compute_admission_inventory_movement_count",
        string="Inventory Usage",
    )

    def _compute_admission_inventory_movement_count(self):
        Movement = self.env["hospital.stock.movement"]
        for admission in self:
            admission.inventory_movement_count = (
                Movement.search_count([("admission_id", "=", admission.id)]) if admission.id else 0
            )

    def action_view_admission_inventory_movements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Usage",
            "res_model": "hospital.stock.movement",
            "view_mode": "list,form",
            "domain": [("admission_id", "=", self.id)],
            "context": {
                "default_admission_id": self.id,
                "default_patient_id": self.patient_id.id,
            },
        }


class HospitalProcedureRequest(models.Model):
    _inherit = "hospital.procedure.request"

    inventory_movement_ids = fields.One2many(
        "hospital.stock.movement",
        "procedure_id",
        string="Inventory Movements",
    )
    inventory_movement_count = fields.Integer(
        compute="_compute_procedure_inventory_movement_count",
        string="Inventory Usage",
    )

    def _compute_procedure_inventory_movement_count(self):
        Movement = self.env["hospital.stock.movement"]
        for procedure in self:
            procedure.inventory_movement_count = (
                Movement.search_count([("procedure_id", "=", procedure.id)]) if procedure.id else 0
            )

    def action_view_procedure_inventory_movements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Usage",
            "res_model": "hospital.stock.movement",
            "view_mode": "list,form",
            "domain": [("procedure_id", "=", self.id)],
            "context": {
                "default_procedure_id": self.id,
                "default_patient_id": self.patient_id.id,
            },
        }

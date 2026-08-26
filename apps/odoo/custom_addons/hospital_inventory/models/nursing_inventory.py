from odoo import fields, models
from odoo.exceptions import UserError


class HospitalMedicationAdministration(models.Model):
    _inherit = "hospital.medication.administration"

    inventory_item_id = fields.Many2one(
        "hospital.inventory.item", string="Inventory Item"
    )
    inventory_batch_id = fields.Many2one(
        "hospital.inventory.batch",
        string="Inventory Batch",
        domain="[('item_id', '=', inventory_item_id)]",
    )
    inventory_consumed_quantity = fields.Float(string="Consumed Quantity", default=1.0)
    inventory_consumption_id = fields.Many2one(
        "hospital.stock.consumption", string="Inventory Consumption", readonly=True, copy=False
    )
    inventory_consumption_count = fields.Integer(
        compute="_compute_inventory_consumption_count", string="Inventory Consumption"
    )

    def _compute_inventory_consumption_count(self):
        Consumption = self.env["hospital.stock.consumption"]
        for rec in self:
            rec.inventory_consumption_count = (
                Consumption.search_count([("medication_administration_id", "=", rec.id)])
                if rec.id
                else 0
            )

    def action_consume_medicine(self):
        self.ensure_one()
        if self.inventory_consumption_id:
            raise UserError("An inventory consumption already exists for this record.")
        if not self.inventory_item_id:
            raise UserError("Set the Inventory Item before consuming medicine.")
        if self.inventory_consumed_quantity <= 0:
            raise UserError("Consumed quantity must be greater than zero.")
        consumption = self.env["hospital.stock.consumption"].create_for_source(
            consumption_type="nursing",
            source_vals={
                "patient_id": self.patient_id.id,
                "admission_id": self.admission_id.id if self.admission_id else False,
                "medication_administration_id": self.id,
            },
            line_vals=[
                {
                    "item_id": self.inventory_item_id.id,
                    "batch_id": self.inventory_batch_id.id if self.inventory_batch_id else False,
                    "quantity": self.inventory_consumed_quantity or 1.0,
                }
            ],
        )
        self.inventory_consumption_id = consumption.id
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumption",
            "res_model": "hospital.stock.consumption",
            "res_id": consumption.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_inventory_consumptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumption",
            "res_model": "hospital.stock.consumption",
            "view_mode": "list,form",
            "domain": [("medication_administration_id", "=", self.id)],
            "context": {
                "default_medication_administration_id": self.id,
                "default_consumption_type": "nursing",
                "default_patient_id": self.patient_id.id,
            },
        }


class HospitalNursingRound(models.Model):
    _inherit = "hospital.nursing.round"

    inventory_consumption_ids = fields.One2many(
        "hospital.stock.consumption", "nursing_round_id", string="Inventory Consumptions"
    )
    inventory_consumption_count = fields.Integer(
        compute="_compute_round_inventory_consumption_count", string="Inventory Consumption"
    )

    def _compute_round_inventory_consumption_count(self):
        Consumption = self.env["hospital.stock.consumption"]
        for rec in self:
            rec.inventory_consumption_count = (
                Consumption.search_count([("nursing_round_id", "=", rec.id)]) if rec.id else 0
            )

    def action_create_nursing_supply_consumption(self):
        self.ensure_one()
        consumption = self.env["hospital.stock.consumption"].create_for_source(
            consumption_type="nursing",
            source_vals={
                "patient_id": self.patient_id.id,
                "admission_id": self.admission_id.id if self.admission_id else False,
                "nursing_round_id": self.id,
            },
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumption",
            "res_model": "hospital.stock.consumption",
            "res_id": consumption.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_inventory_consumptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumption",
            "res_model": "hospital.stock.consumption",
            "view_mode": "list,form",
            "domain": [("nursing_round_id", "=", self.id)],
            "context": {
                "default_nursing_round_id": self.id,
                "default_consumption_type": "nursing",
                "default_patient_id": self.patient_id.id,
            },
        }

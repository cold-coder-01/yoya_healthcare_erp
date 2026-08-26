from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalProcedureMaterialLine(models.Model):
    _name = "hospital.procedure.material.line"
    _description = "Procedure Material Template Line"
    _order = "procedure_type_id, id"

    procedure_type_id = fields.Many2one(
        "hospital.procedure.type",
        string="Procedure Type",
        required=True,
        ondelete="cascade",
    )
    item_id = fields.Many2one(
        "hospital.inventory.item",
        string="Item",
        required=True,
        ondelete="restrict",
    )
    default_quantity = fields.Float(string="Default Quantity", default=1.0)
    notes = fields.Text()


class HospitalProcedureType(models.Model):
    _inherit = "hospital.procedure.type"

    material_line_ids = fields.One2many(
        "hospital.procedure.material.line",
        "procedure_type_id",
        string="Material Template",
    )


class HospitalProcedureRequest(models.Model):
    _inherit = "hospital.procedure.request"

    inventory_consumption_ids = fields.One2many(
        "hospital.stock.consumption",
        "procedure_id",
        string="Inventory Consumptions",
    )
    inventory_consumption_count = fields.Integer(
        compute="_compute_inventory_consumption_count",
        string="Inventory Consumption",
    )

    def _compute_inventory_consumption_count(self):
        Consumption = self.env["hospital.stock.consumption"]
        for rec in self:
            rec.inventory_consumption_count = (
                Consumption.search_count([("procedure_id", "=", rec.id)]) if rec.id else 0
            )

    def action_prepare_inventory_consumption(self):
        self.ensure_one()
        line_vals = [
            {"item_id": ml.item_id.id, "quantity": ml.default_quantity or 1.0}
            for ml in self.procedure_type_id.material_line_ids
        ]
        consumption = self.env["hospital.stock.consumption"].create_for_source(
            consumption_type="procedure",
            source_vals={
                "patient_id": self.patient_id.id,
                "admission_id": self.admission_id.id if self.admission_id else False,
                "procedure_id": self.id,
            },
            line_vals=line_vals,
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
            "domain": [("procedure_id", "=", self.id)],
            "context": {
                "default_procedure_id": self.id,
                "default_consumption_type": "procedure",
                "default_patient_id": self.patient_id.id,
            },
        }

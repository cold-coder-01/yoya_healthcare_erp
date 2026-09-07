from odoo import fields, models


class HospitalInventoryCategory(models.Model):
    _name = "hospital.inventory.category"
    _description = "Inventory Item Category"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char()
    description = fields.Text()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

from odoo import fields, models


class HospitalDepartment(models.Model):
    _name = "hospital.department"
    _description = "Hospital Department"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(required=True)
    description = fields.Text()
    manager_id = fields.Many2one(
        "res.users",
        string="Department Manager",
        help="Responsible user for this department until the Doctor model is introduced.",
    )
    active = fields.Boolean(default=True)

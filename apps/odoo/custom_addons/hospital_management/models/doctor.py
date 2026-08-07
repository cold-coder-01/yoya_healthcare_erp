from odoo import fields, models


class HospitalDoctor(models.Model):
    _name = "hospital.doctor"
    _description = "Hospital Doctor"
    _order = "name"

    name = fields.Char(required=True)
    user_id = fields.Many2one("res.users", string="Related User")
    department_id = fields.Many2one("hospital.department", string="Department")
    specialization = fields.Char()
    license_number = fields.Char()
    phone = fields.Char()
    email = fields.Char()
    active = fields.Boolean(default=True)
    notes = fields.Text()

from odoo import fields, models


class HospitalPatientTag(models.Model):
    _name = "hospital.patient.tag"
    _description = "Patient Tag"
    _order = "name"

    name = fields.Char(required=True)
    color = fields.Integer()
    description = fields.Text()
    active = fields.Boolean(default=True)

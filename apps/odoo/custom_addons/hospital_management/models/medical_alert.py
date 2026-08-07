from odoo import fields, models


class HospitalMedicalAlert(models.Model):
    _name = "hospital.medical.alert"
    _description = "Hospital Medical Alert"
    _order = "severity desc, name"

    name = fields.Char(required=True)
    severity = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        required=True,
        default="medium",
    )
    description = fields.Text()
    active = fields.Boolean(default=True)

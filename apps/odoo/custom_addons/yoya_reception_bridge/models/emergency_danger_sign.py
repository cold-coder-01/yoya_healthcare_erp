"""Catalogue of danger signs used in rapid emergency screening at reception."""
from odoo import fields, models


class HospitalEmergencyDangerSign(models.Model):
    _name = "hospital.emergency.danger.sign"
    _description = "Hospital Emergency Danger Sign"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(
        help="Short stable key used by the reception API. Unique per company-wide "
        "catalogue.",
    )
    sequence = fields.Integer(default=10)
    severity = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        required=True,
        default="high",
    )
    requires_immediate_triage = fields.Boolean(
        default=True,
        help="Presence of this sign sends the patient straight to emergency triage.",
    )
    description = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "danger_sign_code_unique",
            "unique(code)",
            "A danger sign with this code already exists.",
        ),
    ]

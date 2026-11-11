from odoo import api, fields, models


class HospitalHrRole(models.Model):
    _name = "hospital.hr.role"
    _description = "Hospital HR Role"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    role_type = fields.Selection(
        [
            ("medical", "Medical"),
            ("nursing", "Nursing"),
            ("allied_health", "Allied Health"),
            ("pharmacy", "Pharmacy"),
            ("administrative", "Administrative"),
            ("support", "Support"),
        ],
        string="Role Type",
        required=True,
        default="administrative",
    )
    clinical_role = fields.Boolean(
        string="Clinical Role",
        help="Licensed clinical professional role involved in patient care or diagnostics.",
    )
    requires_license = fields.Boolean(
        string="Requires License",
        help="Staff holding this role must have a valid professional license "
        "(credential tracking arrives in a later task).",
    )
    can_be_scheduled = fields.Boolean(
        string="Can Be Scheduled",
        default=True,
        help="Staff with this role can be placed on duty rosters.",
    )
    can_be_on_call = fields.Boolean(string="Can Be On Call")
    allowed_department_ids = fields.Many2many(
        "hospital.department",
        "hospital_hr_role_department_rel",
        "role_id",
        "department_id",
        string="Allowed Departments",
        help="Clinical units this role may be assigned to. Empty means all departments.",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    notes = fields.Text()

    _sql_constraints = [
        ("code_unique", "unique(code)", "The role code must be unique."),
    ]

    @api.depends("name", "code")
    def _compute_display_name(self):
        for role in self:
            if role.code:
                role.display_name = f"[{role.code}] {role.name or ''}".strip()
            else:
                role.display_name = role.name or ""

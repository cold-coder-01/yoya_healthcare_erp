from odoo import api, fields, models


class HospitalOperationTheatre(models.Model):
    _name = "hospital.operation.theatre"
    _description = "Operation Theatre / Operating Room"
    _inherit = ["mail.thread"]
    _order = "code, name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(tracking=True)
    theatre_type = fields.Selection(
        [
            ("general", "General"),
            ("emergency", "Emergency"),
            ("obstetric", "Obstetric"),
            ("orthopedic", "Orthopedic"),
            ("minor", "Minor Procedure"),
            ("other", "Other"),
        ],
        string="Theatre Type",
        default="general",
        tracking=True,
    )
    department_id = fields.Many2one(
        "hospital.department",
        string="Department",
        ondelete="set null",
    )
    location_id = fields.Many2one(
        "hospital.inventory.location",
        string="Theatre Store Location",
        ondelete="set null",
        help="Operating Room / Theatre store location stock is consumed from "
        "during a surgery. Configure this to enable inventory consumption "
        "from the correct store.",
    )
    responsible_user_id = fields.Many2one("res.users", string="Responsible")
    capacity_note = fields.Text(string="Capacity / Setup Notes")
    active = fields.Boolean(default=True)
    notes = fields.Text()

    @api.depends("name", "code")
    def _compute_display_name(self):
        for rec in self:
            if rec.code:
                rec.display_name = f"[{rec.code}] {rec.name or ''}".strip()
            else:
                rec.display_name = rec.name or ""

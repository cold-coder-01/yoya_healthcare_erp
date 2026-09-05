from odoo import api, fields, models


class HospitalProcedureType(models.Model):
    _name = "hospital.procedure.type"
    _description = "Procedure / Clinical Service Type"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(string="Code")
    category = fields.Selection(
        [
            ("nursing", "Nursing"),
            ("minor_procedure", "Minor Procedure"),
            ("emergency", "Emergency"),
            ("therapy", "Therapy"),
            ("diagnostic", "Diagnostic"),
            ("respiratory", "Respiratory"),
            ("wound_care", "Wound Care"),
            ("injection", "Injection"),
            ("other", "Other"),
        ],
        string="Category",
        default="other",
    )
    default_price = fields.Monetary(
        string="Default Price",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    requires_doctor_approval = fields.Boolean(string="Requires Doctor Approval")
    requires_admission = fields.Boolean(string="Requires Admission")
    description = fields.Text(string="Description")
    active = fields.Boolean(default=True)

    @api.depends("name", "code")
    def _compute_display_name(self):
        for rec in self:
            if rec.code:
                rec.display_name = f"[{rec.code}] {rec.name or ''}".strip()
            else:
                rec.display_name = rec.name or ""

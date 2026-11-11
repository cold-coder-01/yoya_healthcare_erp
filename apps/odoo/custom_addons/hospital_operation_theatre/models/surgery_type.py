from odoo import api, fields, models
from odoo.exceptions import ValidationError


class HospitalSurgeryType(models.Model):
    _name = "hospital.surgery.type"
    _description = "Surgery Type / Procedure Catalog"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char(string="Code")
    surgery_category = fields.Selection(
        [
            ("general_surgery", "General Surgery"),
            ("obstetrics_gynecology", "Obstetrics & Gynecology"),
            ("orthopedic", "Orthopedic"),
            ("emergency", "Emergency"),
            ("minor", "Minor Surgery"),
            ("ent", "ENT"),
            ("urology", "Urology"),
            ("other", "Other"),
        ],
        string="Surgery Category",
        default="general_surgery",
    )
    expected_duration_hours = fields.Float(string="Expected Duration (Hours)")
    default_price = fields.Monetary(
        string="Default Price",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    requires_anesthesia = fields.Boolean(string="Requires Anesthesia", default=True)
    requires_blood_preparation = fields.Boolean(string="Requires Blood Preparation")
    requires_consent = fields.Boolean(string="Requires Consent", default=True)
    material_line_ids = fields.One2many(
        "hospital.surgery.material.line",
        "surgery_type_id",
        string="Default Materials",
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    @api.depends("name", "code")
    def _compute_display_name(self):
        for rec in self:
            if rec.code:
                rec.display_name = f"[{rec.code}] {rec.name or ''}".strip()
            else:
                rec.display_name = rec.name or ""


class HospitalSurgeryMaterialLine(models.Model):
    _name = "hospital.surgery.material.line"
    _description = "Surgery Type Default Material Line"
    _order = "surgery_type_id, id"

    surgery_type_id = fields.Many2one(
        "hospital.surgery.type",
        string="Surgery Type",
        required=True,
        ondelete="cascade",
    )
    item_id = fields.Many2one(
        "hospital.inventory.item",
        string="Item",
        required=True,
        ondelete="restrict",
    )
    default_quantity = fields.Float(string="Default Quantity", required=True, default=1.0)
    notes = fields.Text()

    @api.constrains("default_quantity")
    def _check_default_quantity(self):
        for line in self:
            if line.default_quantity <= 0:
                raise ValidationError(
                    "Default material quantity must be greater than zero."
                )

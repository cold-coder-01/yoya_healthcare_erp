from odoo import api, fields, models


class HospitalPharmacyMedicine(models.Model):
    _name = "hospital.pharmacy.medicine"
    _description = "Pharmacy Medicine"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char()
    category = fields.Char()
    dosage_form = fields.Selection(
        [
            ("tablet", "Tablet"),
            ("capsule", "Capsule"),
            ("syrup", "Syrup"),
            ("injection", "Injection"),
            ("cream", "Cream"),
            ("ointment", "Ointment"),
            ("drops", "Drops"),
            ("inhaler", "Inhaler"),
            ("solution", "Solution"),
            ("other", "Other"),
        ]
    )
    strength = fields.Char()
    generic_name = fields.Char()
    brand_name = fields.Char()
    route = fields.Selection(
        [
            ("oral", "Oral"),
            ("iv", "IV"),
            ("im", "IM"),
            ("topical", "Topical"),
            ("ophthalmic", "Ophthalmic"),
            ("otic", "Otic"),
            ("inhalation", "Inhalation"),
            ("subcutaneous", "Subcutaneous"),
            ("other", "Other"),
        ]
    )
    description = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("code", "name", "strength")
    def _compute_display_name(self):
        for medicine in self:
            name = medicine.name or ""
            strength = medicine.strength or ""
            display = f"{name} {strength}".strip() if strength else name
            if medicine.code:
                display = f"[{medicine.code}] {display}"
            medicine.display_name = display or "New Medicine"

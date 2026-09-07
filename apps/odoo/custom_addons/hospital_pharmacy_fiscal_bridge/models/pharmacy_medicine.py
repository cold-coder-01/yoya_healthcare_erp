from odoo import api, fields, models


class HospitalPharmacyMedicine(models.Model):
    _inherit = "hospital.pharmacy.medicine"

    sale_price = fields.Float(
        string="Sale Price",
        digits=(16, 2),
        default=0.0,
        help="Selling price per unit charged to the patient. Used to build "
        "the fiscal payment amount for pharmacy dispenses. The fiscal "
        "terminal never chooses the amount — it always comes from Odoo.",
    )


class HospitalPharmacyDispenseLine(models.Model):
    _inherit = "hospital.pharmacy.dispense.line"

    unit_price = fields.Float(
        string="Unit Price",
        digits=(16, 2),
        help="Selling price per unit for this dispense line. Defaulted from "
        "the medicine catalog sale price.",
    )
    price_subtotal = fields.Float(
        string="Subtotal",
        compute="_compute_price_subtotal",
        store=True,
        digits=(16, 2),
        help="Dispensed quantity x unit price. Only dispensed quantities are "
        "charged, never prescribed quantities.",
    )

    @api.depends("dispensed_quantity", "unit_price")
    def _compute_price_subtotal(self):
        for line in self:
            line.price_subtotal = (line.dispensed_quantity or 0.0) * (
                line.unit_price or 0.0
            )

    @api.onchange("medicine_id")
    def _onchange_medicine_id_fiscal_price(self):
        if self.medicine_id and not self.unit_price:
            self.unit_price = self.medicine_id.sale_price

    @api.model_create_multi
    def create(self, vals_list):
        # Lines created in code (e.g. from a prescription) never run the
        # onchange, so the catalog price is applied here as well.
        medicine_model = self.env["hospital.pharmacy.medicine"]
        for vals in vals_list:
            if not vals.get("unit_price") and vals.get("medicine_id"):
                vals["unit_price"] = medicine_model.browse(
                    vals["medicine_id"]
                ).sale_price
        return super().create(vals_list)

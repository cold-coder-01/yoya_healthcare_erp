from odoo import api, fields, models


class HospitalDiseaseCategory(models.Model):
    _name = "hospital.disease.category"
    _description = "Disease Category"
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(tracking=True)
    description = fields.Text()
    active = fields.Boolean(default=True)


class HospitalDisease(models.Model):
    _name = "hospital.disease"
    _description = "Disease"
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(tracking=True)
    category_id = fields.Many2one(
        "hospital.disease.category",
        string="Category",
        tracking=True,
    )
    description = fields.Text()
    active = fields.Boolean(default=True)

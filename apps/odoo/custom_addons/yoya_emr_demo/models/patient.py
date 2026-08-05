from odoo import api, fields, models


class YoyaEmrPatient(models.Model):
    _name = "yoya.emr.patient"
    _description = "YOYA EMR Patient"
    _order = "name"

    name = fields.Char(required=True)
    patient_number = fields.Char(
        readonly=True,
        copy=False,
        default="New",
    )
    gender = fields.Selection(
        [
            ("male", "Male"),
            ("female", "Female"),
            ("other", "Other"),
        ],
    )
    date_of_birth = fields.Date()
    phone = fields.Char()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "patient_number_unique",
            "unique(patient_number)",
            "Patient number must be unique.",
        ),
    ]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("patient_number", "New") == "New":
                vals["patient_number"] = self.env["ir.sequence"].next_by_code(
                    "yoya.emr.patient"
                ) or "New"
        return super().create(vals_list)

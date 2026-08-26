from odoo import api, fields, models


class HospitalDepartmentStock(models.Model):
    _name = "hospital.department.stock"
    _description = "Hospital Department Stock"
    _order = "department_id, item_id"
    _rec_name = "name"

    name = fields.Char(
        compute="_compute_name",
        store=True,
        string="Reference",
    )
    department_id = fields.Many2one(
        "hospital.department",
        string="Department",
        required=True,
        ondelete="cascade",
    )
    item_id = fields.Many2one(
        "hospital.inventory.item",
        string="Item",
        required=True,
        ondelete="cascade",
    )
    quantity_on_hand = fields.Float(
        compute="_compute_quantities",
        string="Quantity On Hand",
    )
    reserved_quantity = fields.Float(
        compute="_compute_quantities",
        string="Reserved Quantity",
    )
    available_quantity = fields.Float(
        compute="_compute_quantities",
        string="Available Quantity",
    )
    reorder_level = fields.Float(string="Reorder Level")
    is_below_reorder = fields.Boolean(
        compute="_compute_quantities",
        string="Below Reorder Level",
    )
    responsible_user_id = fields.Many2one("res.users", string="Responsible")
    notes = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "department_item_uniq",
            "unique(department_id, item_id)",
            "A department stock line already exists for this department and item.",
        ),
    ]

    @api.depends("department_id", "item_id")
    def _compute_name(self):
        for rec in self:
            if rec.department_id and rec.item_id:
                rec.name = f"{rec.department_id.name} / {rec.item_id.display_name}"
            else:
                rec.name = "Department Stock"

    @api.depends(
        "department_id",
        "item_id",
        "reorder_level",
    )
    def _compute_quantities(self):
        Batch = self.env["hospital.inventory.batch"]
        for rec in self:
            if not rec.department_id or not rec.item_id:
                rec.quantity_on_hand = 0.0
                rec.reserved_quantity = 0.0
                rec.available_quantity = 0.0
                rec.is_below_reorder = False
                continue
            batches = Batch.search(
                [
                    ("department_id", "=", rec.department_id.id),
                    ("item_id", "=", rec.item_id.id),
                    ("active", "=", True),
                ]
            )
            rec.quantity_on_hand = sum(batches.mapped("quantity_on_hand"))
            rec.reserved_quantity = sum(batches.mapped("reserved_quantity"))
            usable = batches.filtered(
                lambda b: b.state not in ("expired", "blocked", "depleted")
                and not b.is_expired
            )
            rec.available_quantity = sum(usable.mapped("available_quantity"))
            rec.is_below_reorder = bool(
                rec.reorder_level and rec.available_quantity <= rec.reorder_level
            )

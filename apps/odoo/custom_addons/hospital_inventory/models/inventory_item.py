from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalInventoryItem(models.Model):
    _name = "hospital.inventory.item"
    _description = "Hospital Inventory Item"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    # Batches whose expiry date falls within this many days are flagged as
    # "near expiry" for warning/filtering purposes.
    NEAR_EXPIRY_DAYS = 30

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(tracking=True)
    item_type = fields.Selection(
        [
            ("medicine", "Medicine"),
            ("consumable", "Medical Consumable"),
            ("lab_reagent", "Laboratory Reagent"),
            ("radiology_consumable", "Radiology Consumable"),
            ("procedure_material", "Procedure Material"),
            ("nursing_supply", "Nursing Supply"),
            ("ward_supply", "Ward Supply"),
            ("equipment_small", "Small Equipment"),
            ("other", "Other"),
        ],
        string="Item Type",
        required=True,
        default="consumable",
        tracking=True,
    )
    category_id = fields.Many2one(
        "hospital.inventory.category",
        string="Category",
    )
    category = fields.Char(string="Category (Legacy)")  # kept for backward compatibility
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
        index=True,
        help="Company that owns this item. Empty means shared legacy stock.",
    )
    unit_of_measure = fields.Selection(
        [
            ("unit", "Unit"),
            ("tablet", "Tablet"),
            ("capsule", "Capsule"),
            ("bottle", "Bottle"),
            ("vial", "Vial"),
            ("ampoule", "Ampoule"),
            ("box", "Box"),
            ("pack", "Pack"),
            ("ml", "Millilitre (ml)"),
            ("liter", "Litre"),
            ("gram", "Gram"),
            ("kg", "Kilogram"),
            ("roll", "Roll"),
            ("pair", "Pair"),
            ("kit", "Kit"),
            ("other", "Other"),
        ],
        string="Unit of Measure",
        required=True,
        default="unit",
    )
    description = fields.Text()
    standard_cost = fields.Monetary(
        string="Standard Cost",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    accounting_category = fields.Selection(
        [
            ("medicine", "Medicine"),
            ("medical_consumable", "Medical Consumable"),
            ("lab_reagent", "Laboratory Reagent"),
            ("radiology_consumable", "Radiology Consumable"),
            ("procedure_material", "Procedure Material"),
            ("nursing_supply", "Nursing Supply"),
            ("ward_supply", "Ward Supply"),
            ("other", "Other"),
        ],
        string="Accounting Category",
        default="medical_consumable",
        help="Groups items for a future inventory/accounting bridge. No "
        "journal entry is posted in this module.",
    )
    cost_method = fields.Selection(
        [
            ("manual_standard", "Manual Standard Cost"),
            ("batch_actual", "Batch Actual Cost"),
        ],
        string="Cost Method",
        default="batch_actual",
        help="batch_actual uses each batch's unit cost; manual_standard uses "
        "the item standard cost. Informational until accounting is bridged.",
    )
    minimum_stock_level = fields.Float(string="Minimum Stock Level")
    maximum_stock_level = fields.Float(string="Maximum Stock Level")
    reorder_level = fields.Float(string="Reorder Level")
    requires_batch_tracking = fields.Boolean(
        string="Requires Batch Tracking",
        default=True,
    )
    requires_expiry_tracking = fields.Boolean(
        string="Requires Expiry Tracking",
        default=True,
    )
    active = fields.Boolean(default=True)

    batch_ids = fields.One2many(
        "hospital.inventory.batch",
        "item_id",
        string="Batches",
    )
    batch_count = fields.Integer(
        compute="_compute_batch_stats",
        string="Batches",
        store=True,
    )
    total_quantity_on_hand = fields.Float(
        compute="_compute_batch_stats",
        string="Quantity On Hand",
        store=True,
    )
    total_available_quantity = fields.Float(
        compute="_compute_batch_stats",
        string="Available Quantity",
        store=True,
    )
    near_expiry_batch_count = fields.Integer(
        compute="_compute_batch_stats",
        string="Near-Expiry Batches",
        store=True,
    )
    expired_batch_count = fields.Integer(
        compute="_compute_batch_stats",
        string="Expired Batches",
        store=True,
    )
    is_low_stock = fields.Boolean(
        compute="_compute_batch_stats",
        string="Low Stock",
        store=True,
    )

    # ------------------------------------------------------------------
    # Display name
    # ------------------------------------------------------------------
    @api.depends("name", "code")
    def _compute_display_name(self):
        for rec in self:
            if rec.code:
                rec.display_name = f"[{rec.code}] {rec.name or ''}".strip()
            else:
                rec.display_name = rec.name or ""

    # ------------------------------------------------------------------
    # Computed stock statistics
    # ------------------------------------------------------------------
    @api.depends(
        "batch_ids",
        "batch_ids.quantity_on_hand",
        "batch_ids.available_quantity",
        "batch_ids.state",
        "batch_ids.is_expired",
        "batch_ids.is_near_expiry",
        "batch_ids.active",
        "reorder_level",
    )
    def _compute_batch_stats(self):
        for rec in self:
            batches = rec.batch_ids.filtered(lambda b: b.active)
            rec.batch_count = len(batches)
            rec.total_quantity_on_hand = sum(batches.mapped("quantity_on_hand"))
            # Only stock in usable batches counts as available.
            usable = batches.filtered(
                lambda b: b.state not in ("expired", "blocked", "depleted")
                and not b.is_expired
            )
            rec.total_available_quantity = sum(usable.mapped("available_quantity"))
            rec.near_expiry_batch_count = len(
                batches.filtered(lambda b: b.is_near_expiry and not b.is_expired)
            )
            rec.expired_batch_count = len(batches.filtered(lambda b: b.is_expired))
            rec.is_low_stock = bool(
                rec.reorder_level
                and rec.total_available_quantity <= rec.reorder_level
            )

    # ------------------------------------------------------------------
    # Smart-button action
    # ------------------------------------------------------------------
    def action_view_batches(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Batches / Lots",
            "res_model": "hospital.inventory.batch",
            "view_mode": "list,form",
            "domain": [("item_id", "=", self.id)],
            "context": {"default_item_id": self.id},
        }

    # ------------------------------------------------------------------
    # ORM overrides + audit
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Inventory item created.",
                new_value=record._audit_summary(["name", "code", "item_type"]),
            )
        return records

    def write(self, vals):
        tracked = {k: v for k, v in vals.items() if k not in ("write_date", "write_uid", "display_name")}
        old_values = {rec.id: rec._audit_summary(tracked.keys()) for rec in self}
        result = super().write(vals)
        if tracked:
            for rec in self:
                rec._create_audit_log(
                    action_type="update",
                    description="Inventory item updated.",
                    old_value=old_values.get(rec.id),
                    new_value=rec._audit_summary(tracked.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                rec._create_audit_log(
                    action_type="delete_attempt",
                    description="Deletion of inventory item blocked.",
                    old_value=rec._audit_summary(["name", "code"]),
                )
            raise UserError(
                "Inventory items are master data. Archive the item instead of deleting it."
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------
    def _audit_summary(self, field_names):
        values = []
        for field_name in field_names:
            if field_name in self._fields:
                value = self[field_name]
                if hasattr(value, "mapped"):
                    value = ", ".join(value.mapped("display_name"))
                values.append(f"{field_name}: {value}")
        return "; ".join(values)

    def _create_audit_log(self, action_type, description, old_value=False, new_value=False):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for rec in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                model_name=rec._name,
                record_id=rec.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )


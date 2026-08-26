from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalInventoryBatch(models.Model):
    _name = "hospital.inventory.batch"
    _description = "Hospital Inventory Batch / Lot"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "expiry_date asc, id desc"

    name = fields.Char(
        string="Batch Reference",
        readonly=True,
        copy=False,
        default="New",
        tracking=True,
    )
    item_id = fields.Many2one(
        "hospital.inventory.item",
        string="Item",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    batch_number = fields.Char(string="Batch / Lot Number", required=True, tracking=True)
    location_id = fields.Many2one(
        "hospital.inventory.location",
        string="Location",
        ondelete="restrict",
        tracking=True,
        help="Physical/virtual hospital location holding this batch.",
    )
    location_type = fields.Selection(
        related="location_id.location_type",
        string="Location Type",
        store=True,
    )
    department_id = fields.Many2one(
        "hospital.department",
        string="Department",
        ondelete="set null",
        tracking=True,
    )
    location_name = fields.Char(string="Storage Location (legacy)")
    received_date = fields.Date(string="Received Date", default=fields.Date.context_today)
    expiry_date = fields.Date(string="Expiry Date", tracking=True)
    quantity_on_hand = fields.Float(string="Quantity On Hand", tracking=True)
    reserved_quantity = fields.Float(string="Reserved Quantity", tracking=True)
    available_quantity = fields.Float(
        compute="_compute_available_quantity",
        string="Available Quantity",
        store=True,
    )
    unit_cost = fields.Monetary(string="Unit Cost", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    inventory_value = fields.Monetary(
        compute="_compute_inventory_value",
        string="Inventory Value",
        currency_field="currency_id",
        store=True,
        help="Stock valuation = quantity on hand Ã— unit cost. Informational "
        "only until an accounting bridge is added.",
    )
    available_value = fields.Monetary(
        compute="_compute_inventory_value",
        string="Available Value",
        currency_field="currency_id",
        store=True,
    )
    state = fields.Selection(
        [
            ("available", "Available"),
            ("reserved", "Reserved"),
            ("expired", "Expired"),
            ("depleted", "Depleted"),
            ("blocked", "Blocked"),
        ],
        string="Status",
        default="available",
        required=True,
        tracking=True,
    )
    is_expired = fields.Boolean(
        compute="_compute_expiry_flags",
        string="Expired",
        store=True,
    )
    is_near_expiry = fields.Boolean(
        compute="_compute_expiry_flags",
        string="Near Expiry",
        store=True,
    )
    days_to_expiry = fields.Integer(
        compute="_compute_expiry_flags",
        string="Days To Expiry",
        store=True,
    )
    active = fields.Boolean(default=True)
    notes = fields.Text()

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------
    @api.depends("quantity_on_hand", "reserved_quantity")
    def _compute_available_quantity(self):
        for rec in self:
            rec.available_quantity = max(rec.quantity_on_hand - rec.reserved_quantity, 0.0)

    @api.depends("quantity_on_hand", "available_quantity", "unit_cost")
    def _compute_inventory_value(self):
        for rec in self:
            rec.inventory_value = rec.quantity_on_hand * (rec.unit_cost or 0.0)
            rec.available_value = rec.available_quantity * (rec.unit_cost or 0.0)

    @api.depends("expiry_date")
    def _compute_expiry_flags(self):
        today = fields.Date.context_today(self)
        near_days = self.env["hospital.inventory.item"].NEAR_EXPIRY_DAYS
        for rec in self:
            if rec.expiry_date:
                delta = (rec.expiry_date - today).days
                rec.days_to_expiry = delta
                rec.is_expired = delta < 0
                rec.is_near_expiry = 0 <= delta <= near_days
            else:
                rec.days_to_expiry = 0
                rec.is_expired = False
                rec.is_near_expiry = False

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("location_id")
    def _onchange_location_id(self):
        if self.location_id and self.location_id.department_id and not self.department_id:
            self.department_id = self.location_id.department_id

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("quantity_on_hand", "reserved_quantity")
    def _check_quantities(self):
        for rec in self:
            if rec.quantity_on_hand < 0:
                raise ValidationError("Quantity on hand cannot be negative.")
            if rec.reserved_quantity < 0:
                raise ValidationError("Reserved quantity cannot be negative.")
            if rec.reserved_quantity > rec.quantity_on_hand:
                raise ValidationError(
                    "Reserved quantity cannot exceed the quantity on hand."
                )

    @api.constrains("expiry_date", "received_date")
    def _check_dates(self):
        for rec in self:
            if rec.expiry_date and rec.received_date and rec.expiry_date < rec.received_date:
                raise ValidationError(
                    "Expiry date cannot be before the received date."
                )

    # ------------------------------------------------------------------
    # Shared deduction helper (used by issue + consumption flows)
    # ------------------------------------------------------------------
    def deduct_quantity(self, quantity):
        """Validate and deduct `quantity` from this batch.

        Single source of truth for stock deduction so issue and consumption
        flows never duplicate the rules. Raises UserError on any unsafe state.
        """
        self.ensure_one()
        if quantity <= 0:
            raise UserError("Deduction quantity must be greater than zero.")
        if self.state in ("expired", "blocked", "depleted") or self.is_expired:
            raise UserError(
                f"Batch '{self.name}' is {self.state} and cannot be deducted."
            )
        if self.available_quantity < quantity:
            raise UserError(
                f"Insufficient stock in batch '{self.name}'. "
                f"Available: {self.available_quantity}, requested: {quantity}."
            )
        self.write({"quantity_on_hand": self.quantity_on_hand - quantity})

    # ------------------------------------------------------------------
    # Location-aware transfer helper
    # ------------------------------------------------------------------
    def _find_or_create_at_location(self, location):
        """Return the matching batch at ``location`` for a transfer, creating it.

        A destination batch mirrors the source identity (item, batch number,
        expiry, unit cost, currency) so traceability is preserved when stock
        physically moves between hospital locations. Quantity is carried by the
        caller â€” the returned batch may start at zero.
        """
        self.ensure_one()
        Batch = self.env["hospital.inventory.batch"]
        existing = Batch.search(
            [
                ("item_id", "=", self.item_id.id),
                ("batch_number", "=", self.batch_number),
                ("location_id", "=", location.id),
                ("expiry_date", "=", self.expiry_date),
                ("active", "=", True),
            ],
            limit=1,
        )
        if existing:
            return existing
        return Batch.create(
            {
                "item_id": self.item_id.id,
                "batch_number": self.batch_number,
                "location_id": location.id,
                "department_id": location.department_id.id or self.department_id.id,
                "received_date": self.received_date,
                "expiry_date": self.expiry_date,
                "quantity_on_hand": 0.0,
                "unit_cost": self.unit_cost,
                "currency_id": self.currency_id.id,
                "state": "available",
            }
        )

    # ------------------------------------------------------------------
    # State maintenance (no cron â€” recomputed on write/create)
    # ------------------------------------------------------------------
    def _sync_state(self):
        """Auto-transition state for usable batches based on quantity/expiry.

        Manually-set "blocked" batches are left untouched. We only move
        between available/reserved/depleted/expired.
        """
        for rec in self:
            if rec.state == "blocked":
                continue
            new_state = rec.state
            if rec.is_expired:
                new_state = "expired"
            elif rec.quantity_on_hand <= 0:
                new_state = "depleted"
            elif rec.reserved_quantity > 0 and rec.available_quantity <= 0:
                new_state = "reserved"
            else:
                # Recover from a previously auto-set expired/depleted state.
                if rec.state in ("expired", "depleted", "reserved"):
                    new_state = "available"
            if new_state != rec.state:
                rec.state = new_state

    # ------------------------------------------------------------------
    # ORM overrides + audit
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.inventory.batch.sequence")
                    or "New"
                )
            # Keep department consistent with the location when only one is set.
            if vals.get("location_id") and not vals.get("department_id"):
                location = self.env["hospital.inventory.location"].browse(
                    vals["location_id"]
                )
                if location.department_id:
                    vals["department_id"] = location.department_id.id
        records = super().create(vals_list)
        records._sync_state()
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Inventory batch created.",
                new_value=record._audit_summary(
                    ["name", "item_id", "batch_number", "quantity_on_hand", "expiry_date"]
                ),
            )
        return records

    def write(self, vals):
        tracked = {k: v for k, v in vals.items() if k not in ("write_date", "write_uid", "display_name")}
        old_values = {rec.id: rec._audit_summary(tracked.keys()) for rec in self}
        result = super().write(vals)
        # Avoid recursion: only resync when the driving fields changed.
        if not {"state"}.issuperset(vals.keys()) and any(
            k in vals for k in ("quantity_on_hand", "reserved_quantity", "expiry_date")
        ):
            self._sync_state()
        if tracked:
            for rec in self:
                rec._create_audit_log(
                    action_type="update",
                    description="Inventory batch updated.",
                    old_value=old_values.get(rec.id),
                    new_value=rec._audit_summary(tracked.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                rec._create_audit_log(
                    action_type="delete_attempt",
                    description="Deletion of inventory batch blocked.",
                    old_value=rec._audit_summary(["name", "item_id", "batch_number"]),
                )
            raise UserError(
                "Inventory batches carry stock history. Archive the batch instead of deleting it."
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


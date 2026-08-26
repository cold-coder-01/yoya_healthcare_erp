from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalOpeningStock(models.Model):
    _name = "hospital.opening.stock"
    _description = "Hospital Opening Stock / Initial Receipt"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "opening_date desc, id desc"

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
        tracking=True,
    )
    opening_date = fields.Datetime(
        string="Opening Date",
        default=fields.Datetime.now,
        tracking=True,
    )
    location_id = fields.Many2one(
        "hospital.inventory.location",
        string="Location",
        required=True,
        ondelete="restrict",
        tracking=True,
        help="Store/location the opening stock is loaded into "
        "(usually the Central Store).",
    )
    department_id = fields.Many2one(
        "hospital.department",
        string="Department",
        ondelete="set null",
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "hospital.opening.stock.line",
        "opening_id",
        string="Opening Stock Lines",
    )
    confirmed_by = fields.Many2one("res.users", string="Confirmed By", readonly=True, copy=False)
    confirmed_date = fields.Datetime(string="Confirmed Date", readonly=True, copy=False)
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    total_value = fields.Monetary(
        compute="_compute_total_value",
        string="Total Opening Value",
        currency_field="currency_id",
        store=True,
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    @api.depends("line_ids.line_value")
    def _compute_total_value(self):
        for rec in self:
            rec.total_value = sum(rec.line_ids.mapped("line_value"))

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("location_id")
    def _onchange_location_id(self):
        if self.location_id and self.location_id.department_id:
            self.department_id = self.location_id.department_id

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_confirm(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError("Only a draft opening stock can be confirmed.")
            if not rec.line_ids:
                raise UserError("Add at least one opening stock line before confirming.")
            department = rec.department_id or rec.location_id.department_id
            Batch = self.env["hospital.inventory.batch"]
            Movement = self.env["hospital.stock.movement"]
            for line in rec.line_ids:
                line._validate_for_confirm()
                today = fields.Date.context_today(rec)
                is_expired = bool(line.expiry_date and line.expiry_date < today)
                batch = Batch.create(
                    {
                        "item_id": line.item_id.id,
                        "batch_number": line.batch_number,
                        "location_id": rec.location_id.id,
                        "department_id": department.id if department else False,
                        "received_date": line.received_date or today,
                        "expiry_date": line.expiry_date,
                        "quantity_on_hand": line.quantity,
                        "unit_cost": line.unit_cost,
                        "currency_id": line.currency_id.id or rec.currency_id.id,
                        "state": "expired" if is_expired else "available",
                    }
                )
                movement = Movement.create(
                    {
                        "movement_type": "receipt",
                        "item_id": line.item_id.id,
                        "batch_id": batch.id,
                        "to_location_id": rec.location_id.id,
                        "to_department_id": department.id if department else False,
                        "quantity": line.quantity,
                        "unit_cost": line.unit_cost,
                        "currency_id": line.currency_id.id or rec.currency_id.id,
                        "inventory_accounting_state": "pending",
                        "notes": f"Opening stock {rec.name}",
                    }
                )
                line.write(
                    {
                        "created_batch_id": batch.id,
                        "movement_id": movement.id,
                    }
                )
            rec.write(
                {
                    "state": "confirmed",
                    "confirmed_by": self.env.user.id,
                    "confirmed_date": fields.Datetime.now(),
                }
            )
            rec._create_audit_log(
                action_type="state_change",
                description="Opening stock confirmed; batches and receipt movements created.",
                old_value="State: draft",
                new_value="State: confirmed",
            )

    def action_cancel(self):
        for rec in self:
            if rec.state == "confirmed":
                raise UserError(
                    "A confirmed opening stock cannot be cancelled. "
                    "A reversal flow is deferred to a future task."
                )
            if rec.state == "draft":
                rec.write({"state": "cancelled"})
                rec._create_audit_log(
                    action_type="state_change",
                    description="Opening stock cancelled.",
                    new_value="State: cancelled",
                )

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state == "cancelled"):
            rec.write({"state": "draft"})
            rec._create_audit_log(
                action_type="state_change",
                description="Opening stock reset to draft.",
                new_value="State: draft",
            )

    def action_view_movements(self):
        self.ensure_one()
        movement_ids = self.line_ids.mapped("movement_id").ids
        return {
            "type": "ir.actions.act_window",
            "name": "Stock Movements",
            "res_model": "hospital.stock.movement",
            "view_mode": "list,form",
            "domain": [("id", "in", movement_ids)],
        }

    # ------------------------------------------------------------------
    # ORM overrides + audit
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.opening.stock.sequence")
                    or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Opening stock created.",
                new_value=record._audit_summary(["name", "location_id"]),
            )
        return records

    # Business fields that must not change once an opening stock is confirmed.
    _LOCKED_FIELDS = {"location_id", "department_id", "opening_date", "line_ids", "name"}

    def write(self, vals):
        # Confirmed records are locked for their business fields (notes and the
        # workflow/chatter fields stay editable).
        if any(r.state == "confirmed" for r in self) and (
            self._LOCKED_FIELDS & set(vals)
        ) and not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            raise UserError(
                "A confirmed opening stock is locked. Its location and lines "
                "cannot be changed; only notes can be edited."
            )
        return super().write(vals)

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                if rec.state == "confirmed":
                    rec._create_audit_log(
                        action_type="delete_attempt",
                        description="Deletion of confirmed opening stock blocked.",
                        old_value=rec._audit_summary(["name", "state"]),
                    )
                    raise UserError(
                        "Confirmed opening stock records are part of the stock "
                        "history and cannot be deleted."
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


class HospitalOpeningStockLine(models.Model):
    _name = "hospital.opening.stock.line"
    _description = "Hospital Opening Stock Line"
    _order = "opening_id, id"

    opening_id = fields.Many2one(
        "hospital.opening.stock",
        string="Opening Stock",
        required=True,
        ondelete="cascade",
    )
    item_id = fields.Many2one(
        "hospital.inventory.item",
        string="Item",
        required=True,
        ondelete="restrict",
    )
    batch_number = fields.Char(string="Batch / Lot Number", required=True)
    received_date = fields.Date(string="Received Date", default=fields.Date.context_today)
    expiry_date = fields.Date(string="Expiry Date")
    quantity = fields.Float(string="Quantity", required=True, default=1.0)
    unit_cost = fields.Monetary(string="Unit Cost", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    line_value = fields.Monetary(
        compute="_compute_line_value",
        string="Line Value",
        currency_field="currency_id",
        store=True,
    )
    created_batch_id = fields.Many2one(
        "hospital.inventory.batch", string="Created Batch", readonly=True, copy=False
    )
    movement_id = fields.Many2one(
        "hospital.stock.movement", string="Receipt Movement", readonly=True, copy=False
    )
    notes = fields.Text()

    @api.depends("quantity", "unit_cost")
    def _compute_line_value(self):
        for line in self:
            line.line_value = line.quantity * line.unit_cost

    @api.onchange("item_id")
    def _onchange_item_id(self):
        if self.item_id and not self.unit_cost:
            self.unit_cost = self.item_id.standard_cost

    @api.constrains("quantity")
    def _check_quantity(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError("Opening stock quantity must be greater than zero.")

    @api.constrains("expiry_date", "received_date")
    def _check_dates(self):
        for line in self:
            if (
                line.expiry_date
                and line.received_date
                and line.expiry_date < line.received_date
            ):
                raise ValidationError("Expiry date cannot be before the received date.")

    def _validate_for_confirm(self):
        self.ensure_one()
        if self.quantity <= 0:
            raise UserError(
                f"Quantity for item '{self.item_id.display_name}' must be greater than zero."
            )
        if not self.batch_number:
            raise UserError(
                f"A batch/lot number is required for item '{self.item_id.display_name}'."
            )
        if (
            self.expiry_date
            and self.received_date
            and self.expiry_date < self.received_date
        ):
            raise UserError(
                f"Expiry date for item '{self.item_id.display_name}' "
                "cannot be before the received date."
            )

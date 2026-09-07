from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalStockRequest(models.Model):
    _name = "hospital.stock.request"
    _description = "Hospital Stock Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_date desc, id desc"

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
        tracking=True,
    )
    request_date = fields.Datetime(
        string="Request Date",
        default=fields.Datetime.now,
        tracking=True,
    )
    requested_by = fields.Many2one(
        "res.users",
        string="Requested By",
        default=lambda self: self.env.user,
        tracking=True,
    )
    department_id = fields.Many2one(
        "hospital.department",
        string="Department",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    source_location_id = fields.Many2one(
        "hospital.inventory.location",
        string="Source Location",
        ondelete="restrict",
        tracking=True,
        help="Location stock is issued/transferred from "
        "(usually the Central Store).",
    )
    destination_location_id = fields.Many2one(
        "hospital.inventory.location",
        string="Destination Location",
        ondelete="restrict",
        tracking=True,
        help="Location stock is moved into (a department/ward store).",
    )
    transit_location_id = fields.Many2one(
        "hospital.inventory.location",
        string="Transit Location",
        ondelete="restrict",
        tracking=True,
        help="Optional internal-transfer transit location.",
    )
    use_transit = fields.Boolean(string="Use Transit", default=False)
    request_type = fields.Selection(
        [
            ("issue_to_department", "Issue to Department"),
            ("return_to_store", "Return to Store"),
            ("transfer_between_departments", "Transfer Between Departments"),
            ("adjustment_request", "Adjustment Request"),
        ],
        string="Request Type",
        default="issue_to_department",
        required=True,
        tracking=True,
    )
    priority = fields.Selection(
        [
            ("low", "Low"),
            ("normal", "Normal"),
            ("high", "High"),
            ("urgent", "Urgent"),
        ],
        default="normal",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("issued", "Issued"),
            ("partially_issued", "Partially Issued"),
            ("returned", "Returned"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "hospital.stock.request.line",
        "request_id",
        string="Request Lines",
    )
    approved_by = fields.Many2one("res.users", string="Approved By", readonly=True, copy=False)
    approved_date = fields.Datetime(string="Approved Date", readonly=True, copy=False)
    issued_by = fields.Many2one("res.users", string="Issued By", readonly=True, copy=False)
    issued_date = fields.Datetime(string="Issued Date", readonly=True, copy=False)
    movement_count = fields.Integer(compute="_compute_movement_count", string="Movements")
    notes = fields.Text()
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    def _compute_movement_count(self):
        Movement = self.env["hospital.stock.movement"]
        for rec in self:
            rec.movement_count = (
                Movement.search_count([("related_request_id", "=", rec.id)]) if rec.id else 0
            )

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("request_type")
    def _onchange_request_type(self):
        Location = self.env["hospital.inventory.location"]
        central = Location.get_default_central_store()
        if self.request_type == "issue_to_department":
            if central and not self.source_location_id:
                self.source_location_id = central
        elif self.request_type == "return_to_store":
            if central and not self.destination_location_id:
                self.destination_location_id = central

    @api.onchange("use_transit")
    def _onchange_use_transit(self):
        if self.use_transit and not self.transit_location_id:
            transit = self.env["hospital.inventory.location"].get_default_transit()
            if transit:
                self.transit_location_id = transit

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            if not rec.line_ids:
                raise UserError("Add at least one request line before submitting.")
            rec.write({"state": "submitted"})
            rec._create_audit_log(
                action_type="state_change",
                description="Stock request submitted.",
                old_value="State: draft",
                new_value="State: submitted",
            )

    def action_approve(self):
        for rec in self.filtered(lambda r: r.state == "submitted"):
            for line in rec.line_ids:
                if not line.approved_quantity:
                    line.approved_quantity = line.requested_quantity
            rec.write(
                {
                    "state": "approved",
                    "approved_by": self.env.user.id,
                    "approved_date": fields.Datetime.now(),
                }
            )
            rec._create_audit_log(
                action_type="state_change",
                description="Stock request approved.",
                old_value="State: submitted",
                new_value="State: approved",
            )

    def action_issue_stock(self):
        for rec in self.filtered(lambda r: r.state in ("approved", "partially_issued")):
            if rec.request_type == "return_to_store":
                raise UserError(
                    "Use the Return button for return-to-store requests."
                )
            issued_any = False
            for line in rec.line_ids:
                to_issue = (line.approved_quantity or 0.0) - (line.issued_quantity or 0.0)
                if to_issue <= 0:
                    continue
                line._issue_quantity(to_issue)
                issued_any = True
            if not issued_any:
                raise UserError(
                    "Nothing to issue. Check approved quantities and selected batches."
                )
            fully_issued = all(
                (line.issued_quantity or 0.0) >= (line.approved_quantity or 0.0)
                for line in rec.line_ids
                if line.approved_quantity
            )
            new_state = "issued" if fully_issued else "partially_issued"
            rec.write(
                {
                    "state": new_state,
                    "issued_by": self.env.user.id,
                    "issued_date": fields.Datetime.now(),
                }
            )
            rec._create_audit_log(
                action_type="state_change",
                description="Stock request issued.",
                new_value=f"State: {new_state}",
            )

    def action_return_stock(self):
        for rec in self.filtered(
            lambda r: r.state in ("approved", "issued", "partially_issued")
        ):
            if rec.request_type != "return_to_store":
                raise UserError(
                    "Return is only allowed for return-to-store requests."
                )
            returned_any = False
            for line in rec.line_ids:
                qty = line.approved_quantity or line.requested_quantity
                if qty <= 0:
                    continue
                line._return_quantity(qty)
                returned_any = True
            if not returned_any:
                raise UserError("Nothing to return. Check the request lines.")
            rec.write({"state": "returned"})
            rec._create_audit_log(
                action_type="state_change",
                description="Stock returned to store.",
                new_value="State: returned",
            )

    def action_reject(self):
        for rec in self.filtered(lambda r: r.state == "submitted"):
            rec.write({"state": "rejected"})
            rec._create_audit_log(
                action_type="state_change",
                description="Stock request rejected.",
                old_value="State: submitted",
                new_value="State: rejected",
            )

    def action_cancel(self):
        for rec in self.filtered(lambda r: r.state in ("draft", "submitted", "approved")):
            rec.write({"state": "cancelled"})
            rec._create_audit_log(
                action_type="state_change",
                description="Stock request cancelled.",
                new_value="State: cancelled",
            )

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state in ("rejected", "cancelled")):
            rec.write({"state": "draft"})
            rec._create_audit_log(
                action_type="state_change",
                description="Stock request reset to draft.",
                new_value="State: draft",
            )

    def action_view_movements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Stock Movements",
            "res_model": "hospital.stock.movement",
            "view_mode": "list,form",
            "domain": [("related_request_id", "=", self.id)],
            "context": {"default_related_request_id": self.id},
        }

    # ------------------------------------------------------------------
    # ORM overrides + audit
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.stock.request.sequence")
                    or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Stock request created.",
                new_value=record._audit_summary(["name", "department_id", "request_type"]),
            )
        return records

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                if rec.state not in ("draft", "rejected", "cancelled"):
                    rec._create_audit_log(
                        action_type="delete_attempt",
                        description="Deletion of stock request blocked.",
                        old_value=rec._audit_summary(["name", "state"]),
                    )
                    raise UserError(
                        "Only draft, rejected, or cancelled requests can be deleted. "
                        "Cancel the request instead."
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


class HospitalStockRequestLine(models.Model):
    _name = "hospital.stock.request.line"
    _description = "Hospital Stock Request Line"
    _order = "request_id, id"

    request_id = fields.Many2one(
        "hospital.stock.request",
        string="Request",
        required=True,
        ondelete="cascade",
    )
    item_id = fields.Many2one(
        "hospital.inventory.item",
        string="Item",
        required=True,
        ondelete="restrict",
    )
    requested_quantity = fields.Float(string="Requested Qty", required=True, default=1.0)
    approved_quantity = fields.Float(string="Approved Qty")
    issued_quantity = fields.Float(string="Issued Qty", readonly=True, copy=False)
    batch_id = fields.Many2one(
        "hospital.inventory.batch",
        string="Batch / Lot",
        domain="[('item_id', '=', item_id)]",
    )
    notes = fields.Text()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @api.constrains("requested_quantity")
    def _check_requested_quantity(self):
        for line in self:
            if line.requested_quantity <= 0:
                raise ValidationError("Requested quantity must be greater than zero.")

    @api.constrains("approved_quantity", "requested_quantity")
    def _check_approved_quantity(self):
        for line in self:
            if line.approved_quantity and line.approved_quantity > line.requested_quantity:
                raise ValidationError(
                    "Approved quantity cannot exceed the requested quantity."
                )

    @api.constrains("issued_quantity", "approved_quantity")
    def _check_issued_quantity(self):
        manager = self.env.user.has_group(
            "hospital_management.group_hospital_manager"
        ) or self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        )
        for line in self:
            if (
                line.issued_quantity
                and line.approved_quantity
                and line.issued_quantity > line.approved_quantity
                and not manager
            ):
                raise ValidationError(
                    "Issued quantity cannot exceed the approved quantity."
                )

    @api.constrains("batch_id", "item_id")
    def _check_batch_item(self):
        for line in self:
            if line.batch_id and line.batch_id.item_id != line.item_id:
                raise ValidationError(
                    "The selected batch does not belong to the chosen item."
                )

    # ------------------------------------------------------------------
    # Issue / Return logic
    # ------------------------------------------------------------------
    def _movement_type_for_request(self):
        """Map the request type to the stock movement type."""
        mapping = {
            "issue_to_department": "issue",
            "transfer_between_departments": "transfer",
            "return_to_store": "return",
            "adjustment_request": "adjustment",
        }
        return mapping.get(self.request_id.request_type, "issue")

    def _issue_quantity(self, quantity):
        """Move `quantity` from the source batch into the destination location.

        The source batch decreases; a matching destination batch is created or
        topped up so stock physically *moves* rather than disappearing. Only a
        true consumption permanently removes stock from inventory.
        """
        self.ensure_one()
        if not self.batch_id:
            raise UserError(
                f"Select a batch for item '{self.item_id.display_name}' before issuing."
            )
        request = self.request_id
        batch = self.batch_id
        if batch.state in ("expired", "blocked", "depleted") or batch.is_expired:
            raise UserError(
                f"Batch '{batch.name}' is {batch.state} and cannot be issued."
            )
        if batch.available_quantity < quantity:
            raise UserError(
                f"Insufficient stock in batch '{batch.name}'. "
                f"Available: {batch.available_quantity}, requested to issue: {quantity}."
            )
        # Optional source-location guard: if a source location was set, the
        # selected batch must sit there.
        if (
            request.source_location_id
            and batch.location_id
            and batch.location_id != request.source_location_id
        ):
            raise UserError(
                f"Batch '{batch.name}' is at "
                f"'{batch.location_id.display_name}', not the request's source "
                f"location '{request.source_location_id.display_name}'."
            )
        from_location = request.source_location_id or batch.location_id
        destination = request.destination_location_id
        batch.write({"quantity_on_hand": batch.quantity_on_hand - quantity})
        # Move stock into the destination location (create/top up its batch).
        if destination and destination != batch.location_id:
            dest_batch = batch._find_or_create_at_location(destination)
            dest_batch.write(
                {"quantity_on_hand": dest_batch.quantity_on_hand + quantity}
            )
        self.issued_quantity = (self.issued_quantity or 0.0) + quantity
        self.env["hospital.stock.movement"].create(
            {
                "movement_type": self._movement_type_for_request(),
                "item_id": self.item_id.id,
                "batch_id": batch.id,
                "from_location_id": from_location.id if from_location else False,
                "to_location_id": destination.id if destination else False,
                "from_department_id": from_location.department_id.id
                if from_location and from_location.department_id
                else False,
                "to_department_id": request.department_id.id,
                "quantity": quantity,
                "unit_cost": batch.unit_cost,
                "currency_id": batch.currency_id.id,
                "related_request_id": request.id,
                "inventory_accounting_state": "not_applicable",
            }
        )

    def _return_quantity(self, quantity):
        """Move `quantity` from the department back into the destination store.

        Mirror of the issue flow: the department batch decreases, the central
        store (destination) batch is topped up.
        """
        self.ensure_one()
        if not self.batch_id:
            raise UserError(
                f"Select a batch for item '{self.item_id.display_name}' before returning."
            )
        request = self.request_id
        batch = self.batch_id
        from_location = request.source_location_id or batch.location_id
        destination = request.destination_location_id
        if destination and destination != batch.location_id:
            # Location-aware return: move stock from the department batch into
            # the destination (central) store.
            if batch.available_quantity < quantity:
                raise UserError(
                    f"Insufficient stock in batch '{batch.name}' to return. "
                    f"Available: {batch.available_quantity}, requested: {quantity}."
                )
            batch.write({"quantity_on_hand": batch.quantity_on_hand - quantity})
            dest_batch = batch._find_or_create_at_location(destination)
            dest_batch.write(
                {"quantity_on_hand": dest_batch.quantity_on_hand + quantity}
            )
        else:
            # Legacy behaviour (no destination location): add stock back into
            # the selected batch.
            batch.write({"quantity_on_hand": batch.quantity_on_hand + quantity})
        self.issued_quantity = (self.issued_quantity or 0.0) + quantity
        self.env["hospital.stock.movement"].create(
            {
                "movement_type": "return",
                "item_id": self.item_id.id,
                "batch_id": batch.id,
                "from_location_id": from_location.id if from_location else False,
                "to_location_id": destination.id if destination else False,
                "from_department_id": request.department_id.id,
                "to_department_id": destination.department_id.id
                if destination and destination.department_id
                else False,
                "quantity": quantity,
                "unit_cost": batch.unit_cost,
                "currency_id": batch.currency_id.id,
                "related_request_id": request.id,
                "inventory_accounting_state": "not_applicable",
            }
        )

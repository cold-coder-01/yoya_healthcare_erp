from odoo import api, fields, models


class HospitalStockConsumption(models.Model):
    _inherit = "hospital.stock.consumption"

    # Add a "surgery" consumption type so theatre consumption is traceable.
    consumption_type = fields.Selection(
        selection_add=[("surgery", "Surgery")],
        ondelete={"surgery": "set default"},
    )
    surgery_id = fields.Many2one(
        "hospital.surgery.request",
        string="Surgery",
        ondelete="set null",
    )

    # ------------------------------------------------------------------
    # Register surgery ownership through the base extensible hooks. Because
    # every constraint / provenance lock / onchange / context sanitizer /
    # is_source_generated compute reads ownership through these hooks, surgery
    # is validated with the correct rules (primary = surgery_id, context =
    # admission_id) instead of being false-flagged.
    # ------------------------------------------------------------------
    def _source_field_names(self):
        return super()._source_field_names() + ["surgery_id"]

    def _source_type_map(self):
        source_map = super()._source_type_map()
        source_map["surgery"] = {
            "allowed": {"surgery_id", "admission_id"},
            "context": {"admission_id"},
        }
        return source_map

    # The base @api.constrains lists only base fields, so a write touching ONLY
    # surgery_id would not fire it. Add a trigger for surgery_id that runs the
    # same integrity check.
    @api.constrains("surgery_id")
    def _check_surgery_source(self):
        self._check_source_documents()

    def action_consume(self):
        """Stamp the surgery on the movements created by the base consume flow.

        The base ``action_consume`` creates one movement per line. After it
        runs we copy ``surgery_id`` onto any movement that does not yet carry
        one so theatre material usage is traceable on the surgery case.
        """
        res = super().action_consume()
        for rec in self.filtered(lambda r: r.surgery_id):
            movements = rec.movement_ids.filtered(lambda m: not m.surgery_id)
            if movements:
                movements.write({"surgery_id": rec.surgery_id.id})
        return res


class HospitalStockMovement(models.Model):
    _inherit = "hospital.stock.movement"

    surgery_id = fields.Many2one(
        "hospital.surgery.request",
        string="Surgery",
        ondelete="set null",
        readonly=True,
    )


class HospitalSurgeryRequest(models.Model):
    _inherit = "hospital.surgery.request"

    inventory_consumption_ids = fields.One2many(
        "hospital.stock.consumption",
        "surgery_id",
        string="Inventory Consumptions",
    )
    inventory_movement_ids = fields.One2many(
        "hospital.stock.movement",
        "surgery_id",
        string="Inventory Movements",
    )
    inventory_consumption_count = fields.Integer(
        compute="_compute_inventory_counts",
        string="Inventory Consumption",
    )
    inventory_movement_count = fields.Integer(
        compute="_compute_inventory_counts",
        string="Inventory Movements",
    )
    estimated_material_cost = fields.Monetary(
        compute="_compute_estimated_material_cost",
        string="Estimated Material Cost",
        currency_field="currency_id",
    )
    actual_material_cost = fields.Monetary(
        compute="_compute_actual_material_cost",
        string="Actual Material Cost",
        currency_field="currency_id",
    )
    total_estimated_cost = fields.Monetary(
        compute="_compute_total_estimated_cost",
        string="Total Estimated Cost",
        currency_field="currency_id",
    )

    # ------------------------------------------------------------------
    # Computed
    # ------------------------------------------------------------------
    def _compute_inventory_counts(self):
        for rec in self:
            rec.inventory_consumption_count = len(rec.inventory_consumption_ids)
            rec.inventory_movement_count = len(rec.inventory_movement_ids)

    @api.depends(
        "surgery_type_id",
        "surgery_type_id.material_line_ids.default_quantity",
        "surgery_type_id.material_line_ids.item_id.standard_cost",
    )
    def _compute_estimated_material_cost(self):
        for rec in self:
            total = 0.0
            for ml in rec.surgery_type_id.material_line_ids:
                total += (ml.default_quantity or 0.0) * (ml.item_id.standard_cost or 0.0)
            rec.estimated_material_cost = total

    @api.depends(
        "inventory_movement_ids.movement_value",
        "inventory_movement_ids.movement_type",
    )
    def _compute_actual_material_cost(self):
        for rec in self:
            movements = rec.inventory_movement_ids.filtered(
                lambda m: m.movement_type == "consumption"
            )
            rec.actual_material_cost = sum(movements.mapped("movement_value"))

    @api.depends("default_price", "estimated_material_cost")
    def _compute_total_estimated_cost(self):
        for rec in self:
            rec.total_estimated_cost = (rec.default_price or 0.0) + (
                rec.estimated_material_cost or 0.0
            )

    # ------------------------------------------------------------------
    # Inventory actions
    # ------------------------------------------------------------------
    def _get_surgery_inventory_consumptions(self):
        """Return the active/draft/approved/consumed consumptions for this surgery."""
        self.ensure_one()
        return self.env["hospital.stock.consumption"].search([
            ("surgery_id", "=", self.id),
            ("state", "in", ["draft", "approved", "consumed"]),
        ])

    def action_prepare_inventory_consumption(self):
        """Create a draft theatre consumption from the surgery type materials.

        Single-preparation by default: if a consumption already exists for this
        surgery (draft/approved/consumed), open it instead of creating another,
        so the same surgery materials can never be deducted twice. Batches are
        left empty for manual selection. No stock is deducted until the user
        approves and consumes the record in hospital_inventory.
        """
        self.ensure_one()
        existing = self._get_surgery_inventory_consumptions()
        if existing:
            self.message_post(
                body="Inventory consumption already exists for this surgery. "
                "Opening the existing record instead of creating a duplicate."
            )
            if len(existing) == 1:
                return self._open_record_action(
                    "Inventory Consumption",
                    "hospital.stock.consumption",
                    existing.id,
                )
            return self.action_view_inventory_consumptions()
        line_vals = [
            {"item_id": ml.item_id.id, "quantity": ml.default_quantity or 1.0}
            for ml in self.surgery_type_id.material_line_ids
        ]
        Location = self.env["hospital.inventory.location"]
        source_location = self.theatre_id.location_id
        consumption_location = Location.get_default_consumption_location()
        # Only the intended sources are passed. The centralized, source-sanitized
        # create_for_source service strips stray default_<source_field> context
        # keys and explicitly clears every sibling source field, so no unrelated
        # source document can leak in.
        source_vals = {
            "patient_id": self.patient_id.id,
            "admission_id": self.admission_id.id if self.admission_id else False,
            "department_id": self.department_id.id if self.department_id else False,
            "surgery_id": self.id,
            "notes": f"Surgery consumption for {self.name}",
        }
        if consumption_location:
            source_vals["consumption_location_id"] = consumption_location.id
        if source_location:
            source_vals["source_location_id"] = source_location.id
        consumption = self.env["hospital.stock.consumption"].create_for_source(
            consumption_type="surgery",
            source_vals=source_vals,
            line_vals=line_vals,
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumption",
            "res_model": "hospital.stock.consumption",
            "res_id": consumption.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_inventory_consumptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumptions",
            "res_model": "hospital.stock.consumption",
            "view_mode": "list,form",
            "domain": [("surgery_id", "=", self.id)],
            "context": {
                "default_surgery_id": self.id,
                "default_consumption_type": "surgery",
                "default_patient_id": self.patient_id.id,
            },
        }

    def action_view_inventory_movements(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Movements",
            "res_model": "hospital.stock.movement",
            "view_mode": "list,form",
            "domain": [("surgery_id", "=", self.id)],
            "context": {"default_surgery_id": self.id},
        }

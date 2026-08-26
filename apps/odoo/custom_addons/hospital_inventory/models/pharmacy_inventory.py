from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare


QTY_PRECISION = 3
PHARMACY_ITEM_TYPES = ("medicine", "consumable")
PHARMACY_CATEGORY_CODE = "CAT-PHARM"
DOSAGE_FORM_UOM_COMPATIBILITY = {
    "tablet": {"tablet", "unit"},
    "capsule": {"capsule", "unit"},
    "syrup": {"bottle", "ml", "liter"},
    "injection": {"vial", "ampoule", "unit"},
    "cream": {"gram", "kg", "unit"},
    "ointment": {"gram", "kg", "unit"},
    "drops": {"bottle", "ml"},
    "inhaler": {"unit"},
    "solution": {"bottle", "ml", "liter"},
}


class HospitalPharmacyMedicine(models.Model):
    _inherit = "hospital.pharmacy.medicine"

    inventory_item_id = fields.Many2one(
        "hospital.inventory.item",
        string="Inventory Item",
        help="Inventory catalog item this medicine maps to for stock consumption.",
    )
    inventory_category_id = fields.Many2one(
        related="inventory_item_id.category_id",
        string="Inventory Category",
        store=True,
        readonly=True,
        help="Stock/reporting category inherited from the linked inventory item. Distinct from the medicine's therapeutic category.",
    )

    @api.model
    def _pharmacy_inventory_allowed_company_ids(self):
        return self.env.context.get("allowed_company_ids") or self.env.company.ids

    @api.model
    def _pharmacy_inventory_item_domain(self):
        return [
            ("active", "=", True),
            ("item_type", "in", list(PHARMACY_ITEM_TYPES)),
            ("category_id.code", "=", PHARMACY_CATEGORY_CODE),
            "|",
            ("company_id", "=", False),
            ("company_id", "in", self._pharmacy_inventory_allowed_company_ids()),
        ]

    def _is_valid_pharmacy_inventory_item(self, item):
        self.ensure_one()
        if not item or not item.active:
            return False
        if item.item_type not in PHARMACY_ITEM_TYPES:
            return False
        if not item.category_id or item.category_id.code != PHARMACY_CATEGORY_CODE:
            return False
        if "company_id" in item._fields and item.company_id and item.company_id.id not in self._pharmacy_inventory_allowed_company_ids():
            return False
        return True

    def _check_inventory_item_uom_compatibility(self, item):
        self.ensure_one()
        if not item or not self.dosage_form:
            return True
        allowed_uoms = DOSAGE_FORM_UOM_COMPATIBILITY.get(self.dosage_form)
        if allowed_uoms and item.unit_of_measure not in allowed_uoms:
            raise ValidationError(
                "%s cannot be linked to inventory item %s because dosage form '%s' "
                "is not compatible with inventory UOM '%s'."
                % (
                    self.display_name,
                    item.display_name,
                    dict(self._fields["dosage_form"].selection).get(self.dosage_form, self.dosage_form),
                    dict(item._fields["unit_of_measure"].selection).get(item.unit_of_measure, item.unit_of_measure),
                )
            )
        return True

    @api.constrains("inventory_item_id", "dosage_form")
    def _check_inventory_item_id(self):
        for medicine in self:
            item = medicine.inventory_item_id
            if not item:
                continue
            if not medicine._is_valid_pharmacy_inventory_item(item):
                raise ValidationError(
                    "%s can only be linked to an active Pharmacy Medicine inventory item "
                    "with item type Medicine or Medical Consumable." % medicine.display_name
                )
            medicine._check_inventory_item_uom_compatibility(item)

    @api.onchange("inventory_item_id")
    def _onchange_inventory_item_id(self):
        item = self.inventory_item_id
        if not item:
            return
        if not self._is_valid_pharmacy_inventory_item(item):
            return {
                "warning": {
                    "title": "Invalid Inventory Item",
                    "message": "Select an active Pharmacy Medicine item with item type Medicine or Medical Consumable.",
                }
            }
        self._check_inventory_item_uom_compatibility(item)
        if not self.name:
            self.name = item.name
        if not self.code:
            self.code = item.code


class HospitalPharmacyDispense(models.Model):
    _inherit = "hospital.pharmacy.dispense"

    inventory_consumption_ids = fields.One2many("hospital.stock.consumption", "pharmacy_dispense_id", string="Inventory Consumptions")
    inventory_consumption_count = fields.Integer(compute="_compute_inventory_consumption_count", string="Inventory Consumption")

    def _compute_inventory_consumption_count(self):
        Consumption = self.env["hospital.stock.consumption"]
        for dispense in self:
            dispense.inventory_consumption_count = Consumption.search_count([("pharmacy_dispense_id", "=", dispense.id)]) if dispense.id else 0

    def action_mark_dispensed(self):
        # Billing clearance and dispense-state validation run in super().  If the
        # stock phase below raises, Odoo rolls back the whole transaction: no false
        # dispense state and no false charge delivery survives.
        result = super().action_mark_dispensed()
        for dispense in self.filtered(lambda d: d.state in ("dispensed", "partial")):
            dispense._consume_pharmacy_inventory_increment()
        return result

    def action_cancel(self):
        """Stock that has left the shelf blocks cancellation. THE STOCK HALF OF
        A THREE-MODULE GUARD.

        hospital_pharmacy refuses to cancel a dispense that reached `partial` or
        `dispensed`, on clinical grounds; hospital_billing refuses one whose
        charges record delivered quantity, on financial grounds. Both of those
        already cover every path that reaches consumed stock today, because
        consumption only ever runs inside action_mark_dispensed() after billing
        has recorded delivery in the same transaction.

        This exists so the invariant does not DEPEND on that coincidence, and
        does not depend on hospital_billing being installed at all -- it is not
        a dependency of this module, and hospital_pharmacy + hospital_inventory
        without it is an installable combination. Stock consumption is this
        module's fact; refusing to pretend it did not happen is this module's
        job. Nothing here reverses a consumption, and this phase ships no
        workflow that can.
        """
        for dispense in self:
            consumed = [
                "%s -- %.3f consumed" % (line.medicine_id.display_name, line.inventory_consumed_quantity)
                for line in dispense.line_ids
                if float_compare(line.inventory_consumed_quantity or 0.0, 0.0, precision_digits=QTY_PRECISION) > 0
            ]
            if consumed:
                raise UserError(
                    "Pharmacy dispense %s has already consumed stock and cannot be cancelled.\n\n%s\n\n"
                    "Returning consumed stock requires a credit/reversal workflow, which is outside this phase. "
                    "No dispense state, charge or stock movement was changed." % (dispense.name, "\n".join("  - %s" % c for c in consumed))
                )
        return super().action_cancel()

    def _inventory_increment_lines(self):
        self.ensure_one()
        line_vals = []
        unlinked = []
        for line in self.line_ids:
            target = line.dispensed_quantity or 0.0
            already = line.inventory_consumed_quantity or 0.0
            delta = target - already
            if float_compare(delta, 0.0, precision_digits=QTY_PRECISION) <= 0:
                continue
            medicine = line.medicine_id
            item = medicine.inventory_item_id
            if not item:
                unlinked.append(medicine.display_name)
                continue
            if not medicine._is_valid_pharmacy_inventory_item(item):
                raise UserError(
                    "%s is linked to inventory item %s, but that item is not an active Pharmacy Medicine item "
                    "with item type Medicine or Medical Consumable." % (medicine.display_name, item.display_name)
                )
            medicine._check_inventory_item_uom_compatibility(item)
            line_vals.append((line, {"item_id": item.id, "quantity": delta}))
        if unlinked:
            raise UserError("These dispensed medicines are not linked to inventory items: " + ", ".join(unlinked))
        return line_vals

    def _get_pharmacy_source_location(self):
        self.ensure_one()
        location = self.env["hospital.inventory.location"].get_default_pharmacy_store()
        if not location:
            raise UserError("Configure an active Pharmacy Store inventory location before validating pharmacy dispense.")
        return location

    def _consume_pharmacy_inventory_increment(self):
        self.ensure_one()
        increments = self._inventory_increment_lines()
        if not increments:
            return self.env["hospital.stock.consumption"]
        source_location = self._get_pharmacy_source_location()
        consumption = self.env["hospital.stock.consumption"].create_for_source(
            consumption_type="pharmacy",
            source_vals={"patient_id": self.patient_id.id, "pharmacy_dispense_id": self.id, "source_location_id": source_location.id},
            line_vals=[vals for _line, vals in increments],
        )
        if self.pharmacist_id:
            consumption.write({"requested_by": self.pharmacist_id.id})
        self._auto_assign_consumption_batches(consumption)
        consumption.action_approve()
        consumption.action_consume()
        for line, _vals in increments:
            line.sudo().write({"inventory_consumed_quantity": line.dispensed_quantity})
        if "inventory_consumption_id" in self._fields:
            self.with_context(skip_dispense_write_audit=True).write({"inventory_consumption_id": consumption.id, "auto_consumption_error": False})
        return consumption

    def _auto_assign_consumption_batches(self, consumption):
        Batch = self.env["hospital.inventory.batch"]
        Line = self.env["hospital.stock.consumption.line"]
        source_location = consumption.source_location_id or self._get_pharmacy_source_location()
        if not consumption.source_location_id:
            consumption.write({"source_location_id": source_location.id})
        for line in consumption.line_ids:
            if line.batch_id:
                if line.batch_id.location_id != source_location:
                    raise UserError(
                        "Selected batch %s is at %s, not the pharmacy source location %s."
                        % (line.batch_id.display_name, line.batch_id.location_id.display_name, source_location.display_name)
                    )
                continue
            remaining = line.quantity
            candidates = Batch.search([
                ("item_id", "=", line.item_id.id),
                ("location_id", "=", source_location.id),
                ("state", "=", "available"),
                ("active", "=", True),
            ], order="expiry_date asc, id asc").filtered(lambda b: not b.is_expired and b.available_quantity > 0)
            allocations = []
            for batch in candidates:
                take = min(remaining, batch.available_quantity)
                if take <= 0:
                    continue
                allocations.append((batch, take))
                remaining -= take
                if float_compare(remaining, 0.0, precision_digits=QTY_PRECISION) <= 0:
                    break
            if float_compare(remaining, 0.0, precision_digits=QTY_PRECISION) > 0:
                raise UserError(
                    "Insufficient available stock for '%s' in pharmacy source location '%s': short %.3f after FEFO allocation. No stock was consumed from another store."
                    % (line.item_id.display_name, source_location.display_name, remaining)
                )
            first_batch, first_qty = allocations[0]
            line.write({"batch_id": first_batch.id, "quantity": first_qty, "unit_cost": first_batch.unit_cost, "currency_id": first_batch.currency_id.id or line.currency_id.id})
            for batch, qty in allocations[1:]:
                Line.create({"consumption_id": consumption.id, "item_id": line.item_id.id, "batch_id": batch.id, "quantity": qty, "unit_cost": batch.unit_cost, "currency_id": batch.currency_id.id or line.currency_id.id, "notes": "FEFO split allocation"})

    def action_create_inventory_consumption(self):
        self.ensure_one()
        if self.state not in ("dispensed", "partial"):
            raise UserError("Validate Dispense before creating pharmacy inventory consumption. Payment alone must never move stock.")
        consumption = self._consume_pharmacy_inventory_increment()
        if not consumption:
            raise UserError("No unconsumed dispense quantity remains for inventory consumption.")
        return self._open_inventory_consumption(consumption)

    def action_view_inventory_consumptions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Inventory Consumption",
            "res_model": "hospital.stock.consumption",
            "view_mode": "list,form",
            "domain": [("pharmacy_dispense_id", "=", self.id)],
            "context": {"default_pharmacy_dispense_id": self.id, "default_consumption_type": "pharmacy", "default_patient_id": self.patient_id.id},
        }

    def _open_inventory_consumption(self, consumption):
        return {"type": "ir.actions.act_window", "name": "Inventory Consumption", "res_model": "hospital.stock.consumption", "res_id": consumption.id, "view_mode": "form", "target": "current"}


class HospitalPharmacyDispenseLine(models.Model):
    _inherit = "hospital.pharmacy.dispense.line"

    inventory_consumed_quantity = fields.Float(string="Inventory Consumed Qty", readonly=True, copy=False, digits=(16, 3), default=0.0)

import contextvars
from contextlib import contextmanager

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare

from odoo.addons.hospital_pharmacy.models.pharmacy_authority import PharmacyWorkflowError


QTY_PRECISION = 3

# ---------------------------------------------------------------------------
# DISPENSE LINE CONSUMPTION AUTHORITY (Pharmacy Slice 0)
# ---------------------------------------------------------------------------
# `inventory_consumed_quantity` is this module's evidence that stock left the
# shelf for a line. The next validation consumes only the increment above it,
# and cancellation refuses a dispense that carries it. A direct write could
# erase consumed stock (so it would be consumed twice) or pre-claim a
# consumption that never happened (so it would never be consumed at all).
#
# It moves only inside _consume_pharmacy_inventory_increment(), under a
# ContextVar no RPC payload can set -- the design hospital_pharmacy's
# pharmacy_authority module documents. sudo() is not a capability.
_inventory_line_capability_var = contextvars.ContextVar(
    "hospital_inventory_pharmacy_line_capability", default=False
)


@contextmanager
def _inventory_line_capability():
    token = _inventory_line_capability_var.set(True)
    try:
        yield
    finally:
        _inventory_line_capability_var.reset(token)


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

    @api.model
    def _doctor_orderable_inventory_domain(self):
        """The inventory half of medicine orderability. AS A DOMAIN.

        DERIVED FROM _pharmacy_inventory_item_domain(), NOT RESTATED. That
        method is already the authoritative description of an inventory item
        this pharmacy can consume from, and _is_valid_pharmacy_inventory_item()
        is the same list of conditions expressed as Python. A third hand-written
        copy is exactly how a Doctor Desk picker starts offering medicines that
        _inventory_increment_lines() refuses at Validate Dispense.

        So this walks the existing domain and prefixes every leaf's field with
        `inventory_item_id.`, turning a domain over hospital.inventory.item into
        the same domain over hospital.pharmacy.medicine. Operator strings ('|',
        '&', '!') pass through untouched, which is what keeps the prefix-notation
        OR over company_id intact.

        WHY THIS MATTERS MORE THAN THE BILLING HALF. An unmapped-but-billable
        medicine is worse than a picker trap: the prescription is placed, the
        pharmacist marks it ready, the charge is raised and THE PATIENT PAYS,
        and only then does consumption refuse with 'not linked to inventory
        items'. The money is taken before the refusal.

        DELIBERATELY NOT INCLUDED: dosage-form/UoM compatibility. It is a Python
        dict lookup and not expressible as a domain, and it does not need to be
        -- an @api.constrains already enforces it at mapping time, so any STORED
        mapping is compatible by construction.

        ALSO DELIBERATELY NOT INCLUDED: live stock. See the Doctor Desk bridge.
        """
        prefixed = []
        for leaf in self._pharmacy_inventory_item_domain():
            if isinstance(leaf, (list, tuple)) and len(leaf) == 3:
                field, operator, value = leaf
                prefixed.append(("inventory_item_id.%s" % field, operator, value))
            else:
                prefixed.append(leaf)
        # Stated rather than left implicit. A medicine with no item is already
        # excluded by every traversal above, but _is_valid_pharmacy_inventory_
        # item() opens with `if not item: return False`, and the domain should
        # read the same way it does.
        return [("inventory_item_id", "!=", False)] + prefixed

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

    @api.model
    def _pharmacy_fefo_candidates(self, item, location):
        """The batches Validate Dispense may consume from, in FEFO order.

        ONE DEFINITION, used by the consumption itself AND by the Pharmacy
        Desk's stock sufficiency answer, so the desk can never call a line
        "sufficient" on batches the consumption would refuse. Pharmacy Store
        only: stock sitting in another store is not stock this counter has.
        """
        if not item or not location:
            return self.env["hospital.inventory.batch"]
        return self.env["hospital.inventory.batch"].search([
            ("item_id", "=", item.id),
            ("location_id", "=", location.id),
            ("state", "=", "available"),
            ("active", "=", True),
        ], order="expiry_date asc, id asc").filtered(lambda b: not b.is_expired and b.available_quantity > 0)

    def _pharmacy_desk_stock_facts(self):
        """Every stock fact the Pharmacy Desk needs, AS BOOLEANS. Pure read.

        Returns::

            {
              "store_configured": bool,   # an active Pharmacy Store exists
              "lines": {line_id: {
                  "inventory_mapped": bool,   # _inventory_increment_lines would accept it
                  "stock_basis": "increment" | "remaining" | None,
                  "stock_sufficient": bool | None,
              }},
            }

        WHAT "SUFFICIENT" IS MEASURED AGAINST. When the pharmacist has set an
        intended quantity above what was already consumed, the question is
        whether the NEXT Validate Dispense can consume that increment
        ("increment"). Otherwise it is whether the rest of the prescription
        could be filled from the shelf now ("remaining"). With nothing left to
        supply it is None -- there is no question to answer.

        Evaluated as the CALLER, deliberately without sudo(): Validate Dispense
        consumes as the caller too, so this sees exactly the batches that would.
        No quantity leaves this method -- the desk shows a verdict, not a stock
        ledger.
        """
        self.ensure_one()
        location = self.env["hospital.inventory.location"].get_default_pharmacy_store()
        available_by_item = {}
        # Pass 1: what each ITEM needs in total -- two lines of one item share
        # one shelf, so each is only sufficient if the shelf covers both.
        need_by_item = {}
        for line in self.line_ids:
            item = line.medicine_id.inventory_item_id
            consumed = line.inventory_consumed_quantity or 0.0
            increment = (line.dispensed_quantity or 0.0) - consumed
            remaining = (line.prescribed_quantity or 0.0) - consumed
            need = increment if float_compare(increment, 0.0, precision_digits=QTY_PRECISION) > 0 else max(remaining, 0.0)
            if item:
                need_by_item[item.id] = need_by_item.get(item.id, 0.0) + need
        lines = {}
        for line in self.line_ids:
            medicine = line.medicine_id
            item = medicine.inventory_item_id
            mapped = bool(item) and medicine._is_valid_pharmacy_inventory_item(item)
            consumed = line.inventory_consumed_quantity or 0.0
            increment = (line.dispensed_quantity or 0.0) - consumed
            remaining = (line.prescribed_quantity or 0.0) - consumed
            if float_compare(increment, 0.0, precision_digits=QTY_PRECISION) > 0:
                basis, need = "increment", increment
            elif float_compare(remaining, 0.0, precision_digits=QTY_PRECISION) > 0:
                basis, need = "remaining", remaining
            else:
                basis, need = None, 0.0
            sufficient = None
            if basis:
                if not (mapped and location):
                    sufficient = False
                else:
                    if item.id not in available_by_item:
                        available_by_item[item.id] = sum(
                            self._pharmacy_fefo_candidates(item, location).mapped("available_quantity")
                        )
                    sufficient = float_compare(
                        available_by_item[item.id],
                        max(need, need_by_item.get(item.id, need)),
                        precision_digits=QTY_PRECISION,
                    ) >= 0
            lines[line.id] = {
                "inventory_mapped": mapped,
                "stock_basis": basis,
                "stock_sufficient": sufficient,
            }
        return {"store_configured": bool(location), "lines": lines}

    # ------------------------------------------------------------------
    # Pharmacy Desk mutations (Pharmacy Slice 2): the stock hooks
    # ------------------------------------------------------------------
    def _desk_stock_items(self):
        return self.sudo().line_ids.mapped("medicine_id.inventory_item_id")

    def _desk_lock_stock_scope(self):
        """Lock steps 8-10:

          8. every inventory item this dispense maps to, ordered by id
          9. the default Pharmacy Store is resolved
          10. every Pharmacy Store batch of those items, ordered by item,
              expiry and id (FEFO order), FOR UPDATE

        Two validations drawing on the same item therefore queue on the item
        row, and neither can allocate a batch the other is about to empty.
        Caches are invalidated by the caller afterwards, so the availability
        recomputed next is read from the locked rows.
        """
        super()._desk_lock_stock_scope()
        cr = self.env.cr
        items = self._desk_stock_items()
        if not items:
            return
        cr.execute(
            "SELECT id FROM hospital_inventory_item WHERE id IN %s ORDER BY id FOR UPDATE",
            (tuple(sorted(items.ids)),),
        )
        location = self.env["hospital.inventory.location"].sudo().get_default_pharmacy_store()
        if not location:
            return
        cr.execute(
            "SELECT id FROM hospital_inventory_batch WHERE item_id IN %s AND location_id = %s "
            "ORDER BY item_id, expiry_date, id FOR UPDATE",
            (tuple(sorted(items.ids)), location.id),
        )

    def _desk_assert_mappings(self, lines):
        super()._desk_assert_mappings(lines)
        for line in lines.sudo():
            medicine = line.medicine_id
            item = medicine.inventory_item_id
            if not item or not medicine._is_valid_pharmacy_inventory_item(item):
                raise PharmacyWorkflowError("pharmacy_inventory_mapping_missing")

    def _desk_assert_validate_stock(self, lines):
        """Enough usable Pharmacy Store stock, on LOCKED batches, for the
        whole increment -- summed per item, so two lines of one item are judged
        against one shelf. Fixed code only: no batch, lot or shortage figure
        leaves this method."""
        super()._desk_assert_validate_stock(lines)
        location = self.env["hospital.inventory.location"].get_default_pharmacy_store()
        if not location:
            raise PharmacyWorkflowError("pharmacy_stock_insufficient")
        need_by_item = {}
        for line in lines:
            item = line.medicine_id.inventory_item_id
            increment = (line.dispensed_quantity or 0.0) - (line.inventory_consumed_quantity or 0.0)
            if float_compare(increment, 0.0, precision_digits=QTY_PRECISION) > 0:
                need_by_item[item] = need_by_item.get(item, 0.0) + increment
        for item, need in need_by_item.items():
            available = sum(self._pharmacy_fefo_candidates(item, location).mapped("available_quantity"))
            if float_compare(available, need, precision_digits=QTY_PRECISION) < 0:
                raise PharmacyWorkflowError("pharmacy_stock_insufficient")

    def _desk_assert_delivered(self, lines):
        super()._desk_assert_delivered(lines)
        for line in lines.sudo():
            if float_compare(
                line.inventory_consumed_quantity, line.dispensed_quantity, precision_digits=QTY_PRECISION
            ) != 0:
                raise PharmacyWorkflowError("pharmacy_mutation_response_failed")

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
        with _inventory_line_capability():
            for line, _vals in increments:
                line.sudo().write({"inventory_consumed_quantity": line.dispensed_quantity})
        if "inventory_consumption_id" in self._fields:
            self.with_context(skip_dispense_write_audit=True).write({"inventory_consumption_id": consumption.id, "auto_consumption_error": False})
        return consumption

    def _auto_assign_consumption_batches(self, consumption):
        Line = self.env["hospital.stock.consumption.line"]
        source_location = consumption.source_location_id or self._get_pharmacy_source_location()
        if not consumption.source_location_id:
            consumption.write({"source_location_id": source_location.id})
        # ONE balance per batch across every line of this consumption (Pharmacy
        # Slice 2). Two dispense lines mapping to the same inventory item used to
        # each read the batch's full available quantity and both allocate it;
        # the final deduct_quantity() caught the overdraw, but only as a raw
        # shortage error after everything else had run.
        balance = {}
        for line in consumption.line_ids:
            if line.batch_id:
                if line.batch_id.location_id != source_location:
                    raise UserError(
                        "Selected batch %s is at %s, not the pharmacy source location %s."
                        % (line.batch_id.display_name, line.batch_id.location_id.display_name, source_location.display_name)
                    )
                continue
            remaining = line.quantity
            candidates = self._pharmacy_fefo_candidates(line.item_id, source_location)
            allocations = []
            for batch in candidates:
                available = balance.get(batch.id, batch.available_quantity)
                take = min(remaining, available)
                if take <= 0:
                    continue
                allocations.append((batch, take))
                balance[batch.id] = available - take
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

    @api.model_create_multi
    def create(self, vals_list):
        # A line is born with nothing consumed. Only the consumption may say
        # otherwise.
        if not _inventory_line_capability_var.get():
            for vals in vals_list:
                if float_compare(vals.get("inventory_consumed_quantity") or 0.0, 0.0, precision_digits=QTY_PRECISION) > 0:
                    raise UserError(
                        "A pharmacy dispense line cannot be created with a consumed "
                        "quantity. Stock is consumed by Validate Dispense. Nothing "
                        "was changed."
                    )
        return super().create(vals_list)

    def write(self, vals):
        if "inventory_consumed_quantity" in vals:
            new = vals["inventory_consumed_quantity"] or 0.0
            for line in self:
                old = line.inventory_consumed_quantity or 0.0
                if float_compare(new, old, precision_digits=QTY_PRECISION) == 0:
                    continue
                if not _inventory_line_capability_var.get():
                    raise UserError(
                        "The consumed quantity of %s is recorded by Validate "
                        "Dispense and cannot be edited directly. Nothing was "
                        "changed." % line.medicine_id.display_name
                    )
                if float_compare(new, old, precision_digits=QTY_PRECISION) < 0:
                    raise UserError(
                        "The consumed quantity of %s cannot go down: returning "
                        "stock is not part of this workflow. Nothing was changed."
                        % line.medicine_id.display_name
                    )
        return super().write(vals)

    def _dispense_intent_floor(self):
        return max(super()._dispense_intent_floor(), self.inventory_consumed_quantity or 0.0)

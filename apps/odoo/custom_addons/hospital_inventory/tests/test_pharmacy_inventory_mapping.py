import uuid

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "pharmacy_inventory_mapping")
class TestPharmacyInventoryMapping(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.category = cls.env.ref("hospital_inventory.category_pharmacy_medicine")
        cls.pharmacy_location = cls.env["hospital.inventory.location"].sudo().get_default_pharmacy_store()
        if not cls.pharmacy_location:
            cls.pharmacy_location = cls.env["hospital.inventory.location"].sudo().create({
                "name": "Test Pharmacy Store",
                "code": "T-PHARM-%s" % uuid.uuid4().hex[:4],
                "location_type": "pharmacy_store",
            })
        cls.patient = cls.env["hospital.patient"].sudo().create({"name": "Inventory Mapping Patient"})

    def _item(self, **overrides):
        vals = {
            "name": "Mapping Item %s" % uuid.uuid4().hex[:6],
            "code": "MAP-%s" % uuid.uuid4().hex[:6],
            "item_type": "consumable",
            "accounting_category": "medicine",
            "category_id": self.category.id,
            "unit_of_measure": "capsule",
            "standard_cost": 10.0,
            "currency_id": self.company.currency_id.id,
            "company_id": self.company.id,
            "active": True,
        }
        vals.update(overrides)
        return self.env["hospital.inventory.item"].sudo().create(vals)

    def _medicine(self, **overrides):
        vals = {
            "name": "Mapping Medicine %s" % uuid.uuid4().hex[:6],
            "code": "MM-%s" % uuid.uuid4().hex[:6],
            "dosage_form": "capsule",
        }
        vals.update(overrides)
        return self.env["hospital.pharmacy.medicine"].sudo().create(vals)


    def _location(self, location_type, code_prefix):
        existing = self.env["hospital.inventory.location"].sudo().search(
            [("location_type", "=", location_type), ("active", "=", True)], limit=1
        )
        if existing:
            return existing
        return self.env["hospital.inventory.location"].sudo().create({
            "name": "%s Test Location" % code_prefix,
            "code": "%s-%s" % (code_prefix, uuid.uuid4().hex[:4]),
            "location_type": location_type,
        })

    def _batch(self, item, location, qty, expiry_days=180, state="available", active=True):
        expiry_date = fields.Date.add(fields.Date.today(), days=expiry_days)
        received_date = fields.Date.add(expiry_date, days=-30) if expiry_days < 0 else fields.Date.today()
        return self.env["hospital.inventory.batch"].sudo().create({
            "item_id": item.id,
            "batch_number": "MAPB-%s" % uuid.uuid4().hex[:6],
            "location_id": location.id,
            "received_date": received_date,
            "expiry_date": expiry_date,
            "quantity_on_hand": qty,
            "unit_cost": 10.0,
            "currency_id": self.company.currency_id.id,
            "state": state,
            "active": active,
        })

    def _dispense_for_item(self, item, qty=1.0, prescribed_qty=None):
        medicine = self._medicine()
        medicine.write({"inventory_item_id": item.id})
        return self.env["hospital.pharmacy.dispense"].sudo().create({
            "patient_id": self.patient.id,
            "line_ids": [(0, 0, {
                "medicine_id": medicine.id,
                "prescribed_quantity": prescribed_qty or qty,
                "dispensed_quantity": qty,
            })],
        })
    def _counts(self):
        models = {
            "items": "hospital.inventory.item",
            "batches": "hospital.inventory.batch",
            "consumptions": "hospital.stock.consumption",
            "stock_moves": "hospital.stock.movement",
            "receipts": "hospital.charge.receipt",
            "fiscal": "hospital.fiscal.transaction",
        }
        return {key: self.env[model].sudo().search_count([]) for key, model in models.items() if model in self.env}

    def test_dropdown_domain_includes_active_pharmacy_medical_consumable(self):
        item = self._item(item_type="consumable")
        medicine = self._medicine()
        domain = medicine._pharmacy_inventory_item_domain()
        self.assertIn(item, self.env["hospital.inventory.item"].search(domain))
        arch = self.env["hospital.pharmacy.medicine"].get_view(
            view_id=self.env.ref("hospital_pharmacy.view_hospital_pharmacy_medicine_form").id,
            view_type="form",
        )["arch"]
        self.assertIn("inventory_item_id", arch)
        self.assertIn("'item_type', 'in', ['medicine', 'consumable']", arch)
        self.assertIn("'category_id.code', '=', 'CAT-PHARM'", arch)

    def test_inactive_wrong_company_and_unrelated_item_types_excluded(self):
        medicine = self._medicine()
        valid = self._item()
        inactive = self._item(active=False)
        unrelated = self._item(item_type="equipment_small")
        other_company = self.env["res.company"].sudo().create({"name": "Other Mapping Company %s" % uuid.uuid4().hex[:6]})
        wrong_company = self._item(company_id=other_company.id)
        found = self.env["hospital.inventory.item"].search(medicine._pharmacy_inventory_item_domain())
        self.assertIn(valid, found)
        self.assertNotIn(inactive, found)
        self.assertNotIn(unrelated, found)
        self.assertNotIn(wrong_company, found)

    def test_capsule_uom_mapping_succeeds_and_incompatible_uom_blocks(self):
        medicine = self._medicine(dosage_form="capsule")
        capsule_item = self._item(unit_of_measure="capsule")
        medicine.write({"inventory_item_id": capsule_item.id})
        self.assertEqual(medicine.inventory_item_id, capsule_item)
        tablet_item = self._item(unit_of_measure="tablet")
        with self.assertRaisesRegex(ValidationError, "not compatible"):
            medicine.write({"inventory_item_id": tablet_item.id})

    def test_mapping_creates_no_inventory_or_financial_artifacts(self):
        medicine = self._medicine()
        item = self._item()
        before = self._counts()
        medicine.write({"inventory_item_id": item.id})
        after = self._counts()
        self.assertEqual(after, before)

    def test_after_mapping_inventory_increment_resolves_stock_and_is_idempotent(self):
        medicine = self._medicine()
        item = self._item()
        medicine.write({"inventory_item_id": item.id})
        batch = self.env["hospital.inventory.batch"].sudo().create({
            "item_id": item.id,
            "batch_number": "MAPB-%s" % uuid.uuid4().hex[:6],
            "location_id": self.pharmacy_location.id,
            "expiry_date": fields.Date.add(fields.Date.today(), months=6),
            "quantity_on_hand": 5.0,
            "unit_cost": 10.0,
            "currency_id": self.company.currency_id.id,
            "state": "available",
        })
        dispense = self.env["hospital.pharmacy.dispense"].sudo().create({
            "patient_id": self.patient.id,
            "line_ids": [(0, 0, {
                "medicine_id": medicine.id,
                "prescribed_quantity": 2.0,
                "dispensed_quantity": 1.0,
            })],
        })
        increments = dispense._inventory_increment_lines()
        self.assertEqual(len(increments), 1)
        self.assertEqual(increments[0][1]["item_id"], item.id)
        consumption = dispense._consume_pharmacy_inventory_increment()
        self.assertEqual(consumption.state, "consumed")
        batch.invalidate_recordset(["quantity_on_hand", "available_quantity"])
        self.assertEqual(batch.quantity_on_hand, 4.0)
        self.assertEqual(dispense.line_ids.inventory_consumed_quantity, 1.0)
        consumption_count = self.env["hospital.stock.consumption"].sudo().search_count([("pharmacy_dispense_id", "=", dispense.id)])
        move_count = self.env["hospital.stock.movement"].sudo().search_count([("consumption_id", "=", consumption.id)])
        self.assertEqual(move_count, 1)
        dispense._consume_pharmacy_inventory_increment()
        self.assertEqual(self.env["hospital.stock.consumption"].sudo().search_count([("pharmacy_dispense_id", "=", dispense.id)]), consumption_count)
        self.assertEqual(self.env["hospital.stock.movement"].sudo().search_count([("consumption_id", "=", consumption.id)]), move_count)

    def test_pharmacy_consumption_uses_pharmacy_source_and_ignores_main_store_batch(self):
        item = self._item()
        main_location = self._location("central_store", "MAINMAP")
        main_batch = self._batch(item, main_location, 5.0, expiry_days=1)
        pharmacy_batch = self._batch(item, self.pharmacy_location, 5.0, expiry_days=10)
        dispense = self._dispense_for_item(item, qty=1.0)

        consumption = dispense._consume_pharmacy_inventory_increment()
        self.assertEqual(consumption.source_location_id, self.pharmacy_location)
        self.assertEqual(consumption.line_ids.batch_id, pharmacy_batch)
        movement = self.env["hospital.stock.movement"].sudo().search([("consumption_id", "=", consumption.id)], limit=1)
        self.assertEqual(movement.from_location_id, self.pharmacy_location)
        main_batch.invalidate_recordset(["quantity_on_hand", "available_quantity"])
        pharmacy_batch.invalidate_recordset(["quantity_on_hand", "available_quantity"])
        self.assertEqual(main_batch.quantity_on_hand, 5.0)
        self.assertEqual(pharmacy_batch.quantity_on_hand, 4.0)

    def test_insufficient_pharmacy_stock_blocks_without_main_store_fallback(self):
        item = self._item()
        main_location = self._location("central_store", "MAINMAP")
        main_batch = self._batch(item, main_location, 5.0, expiry_days=1)
        dispense = self._dispense_for_item(item, qty=1.0)
        before = self._counts()

        with self.assertRaisesRegex(UserError, "Insufficient available stock"):
            with self.env.cr.savepoint():
                dispense._consume_pharmacy_inventory_increment()

        after = self._counts()
        self.assertEqual(after, before)
        main_batch.invalidate_recordset(["quantity_on_hand", "available_quantity"])
        dispense.line_ids.invalidate_recordset(["inventory_consumed_quantity"])
        self.assertEqual(main_batch.quantity_on_hand, 5.0)
        self.assertEqual(dispense.line_ids.inventory_consumed_quantity, 0.0)

    def test_multiple_pharmacy_batches_split_increment_by_fefo(self):
        item = self._item()
        first = self._batch(item, self.pharmacy_location, 0.6, expiry_days=1)
        second = self._batch(item, self.pharmacy_location, 0.6, expiry_days=2)
        dispense = self._dispense_for_item(item, qty=1.0)

        consumption = dispense._consume_pharmacy_inventory_increment()
        self.assertEqual(len(consumption.line_ids), 2)
        quantities_by_batch = {line.batch_id.id: line.quantity for line in consumption.line_ids}
        self.assertEqual(quantities_by_batch[first.id], 0.6)
        self.assertEqual(quantities_by_batch[second.id], 0.4)
        first.invalidate_recordset(["quantity_on_hand", "available_quantity"])
        second.invalidate_recordset(["quantity_on_hand", "available_quantity"])
        self.assertEqual(first.quantity_on_hand, 0.0)
        self.assertAlmostEqual(second.quantity_on_hand, 0.2)
        self.assertEqual(
            self.env["hospital.stock.movement"].sudo().search_count([("consumption_id", "=", consumption.id)]),
            2,
        )

    def test_expired_inactive_and_depleted_pharmacy_batches_are_excluded(self):
        item = self._item()
        self._batch(item, self.pharmacy_location, 5.0, expiry_days=-1)
        self._batch(item, self.pharmacy_location, 5.0, expiry_days=1, active=False)
        self._batch(item, self.pharmacy_location, 0.0, expiry_days=1, state="depleted")
        good = self._batch(item, self.pharmacy_location, 5.0, expiry_days=30)
        dispense = self._dispense_for_item(item, qty=1.0)

        consumption = dispense._consume_pharmacy_inventory_increment()
        self.assertEqual(consumption.line_ids.batch_id, good)
        good.invalidate_recordset(["quantity_on_hand", "available_quantity"])
        self.assertEqual(good.quantity_on_hand, 4.0)
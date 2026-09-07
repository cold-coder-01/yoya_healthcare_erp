"""Slice 6A: cancelling a prescription or a dispense must not strand money.

WHAT THESE TESTS ARE ABOUT. Medication charges are raised and ACTIVATED at Mark
Ready, so between Mark Ready and Validate Dispense the patient owes for
medication nobody has handed over yet. Cancelling in that window used to move a
state and leave every charge live: the visit stayed in the cashier's SERVICE
PAYMENTS lane forever, collecting for medication that would never be dispensed.

The other half is the opposite risk. Once medication HAS been handed over the
charge is a real receivable and the stock has really left the shelf, and
cancelling either would erase a fact. Pharmacy's `partial` state is the ordinary
outcome of prescribing thirty tablets to a counter holding ten, so that guard
protects the common case here rather than an edge case.

THE FIXTURE IS END TO END ON PURPOSE. Reaching `partial` and `dispensed`
honestly means paying the charge and consuming real stock through
hospital_inventory's FEFO allocator, so the delivered-guard tests are asserting
against records the workflow actually produced rather than states written by
hand.
"""
import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "pharmacy_cancellation_integrity")
class TestPharmacyCancellationIntegrity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Cancellation Test Doctor"}
        )
        cls.accountant = cls._make_user(
            "cancel_accountant", "hospital_management.group_hospital_accountant"
        )
        cls.service = cls.env["hospital.billing.service"].sudo().create({
            "name": "Cancellation Test Medicine",
            "code": "T-CANCEL-MED-%s" % uuid.uuid4().hex[:6],
            "service_type": "pharmacy",
            "default_price": 100.0,
            "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id,
            "uom_id": cls.uom.id,
            # Prepayment keeps a charge blocking until it is paid, which is what
            # makes the clearance gate and the cashier lane observable.
            "prepayment_required": True,
            "tax_treatment": "exempt",
        })
        cls.medicine = cls.env["hospital.pharmacy.medicine"].sudo().create({
            "name": "Cancellation Test Medicine",
            "code": "CTM-%s" % uuid.uuid4().hex[:6],
            "dosage_form": "capsule",
            "route": "oral",
            "billing_service_id": cls.service.id,
        })
        if "sale_price" in cls.medicine._fields:
            cls.medicine.sudo().write({"sale_price": 100.0})
        cls.inventory_ready = cls._configure_inventory()

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, login, *group_xmlids):
        groups = [cls.env.ref("base.group_user").id] + [
            cls.env.ref(x).id for x in group_xmlids
        ]
        suffix = uuid.uuid4().hex[:6]
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s_%s@example.test" % (login, suffix),
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, groups)],
        })

    @classmethod
    def _configure_inventory(cls):
        """Map the medicine to a stocked inventory item, when inventory exists.

        hospital_billing does not depend on hospital_inventory, so every test
        that needs real stock consumption skips rather than fails when it is
        absent. In this deployment it is installed.
        """
        if "hospital.inventory.item" not in cls.env:
            return False
        location = cls.env["hospital.inventory.location"].sudo().get_default_pharmacy_store()
        if not location:
            return False
        category = cls.env.ref(
            "hospital_inventory.category_pharmacy_medicine", raise_if_not_found=False
        )
        if not category:
            return False
        item = cls.env["hospital.inventory.item"].sudo().create({
            "name": "Cancellation Test Item %s" % uuid.uuid4().hex[:6],
            "code": "CTI-%s" % uuid.uuid4().hex[:6],
            "item_type": "medicine",
            "accounting_category": "medicine",
            "category_id": category.id,
            # Compatible with dosage_form "capsule"; an incompatible pair is
            # refused by hospital_inventory's own constraint.
            "unit_of_measure": "capsule",
            "standard_cost": 10.0,
            "currency_id": cls.company.currency_id.id,
            "company_id": cls.company.id,
        })
        cls.env["hospital.inventory.batch"].sudo().create({
            "item_id": item.id,
            "batch_number": "CTB-%s" % uuid.uuid4().hex[:6],
            "location_id": location.id,
            "received_date": fields.Date.today(),
            "expiry_date": fields.Date.add(fields.Date.today(), days=365),
            "quantity_on_hand": 500.0,
            "unit_cost": 10.0,
            "currency_id": cls.company.currency_id.id,
            "state": "available",
        })
        cls.medicine.sudo().write({"inventory_item_id": item.id})
        cls.inventory_item = item
        cls.pharmacy_location = location
        return True

    def _prescription(self, qty=10.0):
        """A confirmed prescription and the dispense its confirmation composed."""
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create(
            {"name": "Cancellation Partner %s" % suffix}
        )
        patient = self.env["hospital.patient"].sudo().create({
            "name": "Cancellation Patient %s" % suffix,
            "accounting_partner_id": partner.id,
        })
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": self.doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": "confirmed",
        })
        encounter = self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id,
            "appointment_id": appointment.id,
            "encounter_type": "outpatient",
            "primary_doctor_id": self.doctor.id,
            "company_id": self.company.id,
        })
        prescription = self.env["hospital.prescription"].sudo().create({
            "patient_id": patient.id,
            "physician_id": self.doctor.id,
            "appointment_id": appointment.id,
            "line_ids": [(0, 0, {
                "medicine_id": self.medicine.id,
                "medicine_name": self.medicine.name,
                "quantity": qty,
                "dosage": "500mg",
                "frequency": "twice daily",
                "duration": "5 days",
                "route": "oral",
            })],
        })
        prescription.action_confirm()
        dispense = prescription.pharmacy_dispense_ids[:1]
        return prescription, dispense, appointment, encounter

    def _ready(self, dispense, intended):
        """Take the dispense to `ready`, which is where the charges appear."""
        dispense.line_ids.sudo().write({"dispensed_quantity": intended})
        dispense.sudo().action_mark_ready()
        return dispense.charge_line_ids

    def _pay(self, dispense):
        charges = dispense.charge_line_ids
        receipt = self.env["hospital.charge.receipt"].sudo().create({
            "payment_method": "cash",
            "received_at": fields.Datetime.now(),
            "received_by_id": self.accountant.id,
            "state": "draft",
            "intake_token": uuid.uuid4().hex,
        })
        for charge in charges:
            due = charge.amount_due_for_clearance
            if due > 0:
                self.env["hospital.charge.receipt.allocation"].sudo().create({
                    "receipt_id": receipt.id,
                    "charge_line_id": charge.id,
                    "amount": due,
                })
        receipt.sudo().write({"state": "confirmed"})
        return receipt

    def _stock_counts(self):
        counts = {}
        for key, model in (
            ("consumption", "hospital.stock.consumption"),
            ("stock_moves", "hospital.stock.movement"),
        ):
            if model in self.env:
                counts[key] = self.env[model].sudo().search_count([])
        return counts

    # ------------------------------------------------------------------
    # D1 -- dispense cancellation cleans the medication charges
    # ------------------------------------------------------------------
    def test_ready_dispense_cancellation_cancels_active_charges(self):
        """1 + 2. Ready, nothing delivered: cancel succeeds and nothing is owed."""
        _prescription, dispense, _appointment, _encounter = self._prescription(qty=10.0)
        charges = self._ready(dispense, 10.0)
        self.assertTrue(charges, "Mark Ready should have raised a medication charge")
        self.assertEqual(set(charges.mapped("charge_state")), {"active"})
        self.assertGreater(sum(charges.mapped("amount_due_for_clearance")), 0.0)

        encounter = dispense.encounter_id
        dispense.sudo().action_cancel()
        self.env.invalidate_all()

        self.assertEqual(dispense.state, "cancelled")
        self.assertEqual(set(charges.mapped("charge_state")), {"cancelled"})
        # NEVER DELETED. A cancelled charge is a record of a decision.
        self.assertTrue(all(charge.exists() for charge in charges))

        # NOTHING IS OWED ANY MORE, ASSERTED WHERE THAT IS ACTUALLY DECIDED.
        #
        # The raw per-charge amount_due_for_clearance is NOT zeroed, and that is
        # the shared model's deliberate design rather than an omission here: it
        # is computed from the frozen estimate less cash received and never reads
        # charge_state, so it keeps saying what the cancelled charge would have
        # cost. Every consumer filters by charge_state BEFORE summing it, so
        # zeroing it would change nothing for any caller while destroying the
        # historical figure -- and it is shared with consultation, laboratory and
        # radiology, none of which is in this slice's scope.
        #
        # These two are the figures a human sees and a workflow gates on.
        self.assertEqual(dispense.unified_amount_due_for_clearance, 0.0)
        clearance = self.env["hospital.billing.engine"].sudo().check_financial_clearance(
            encounter
        )
        self.assertTrue(
            clearance["cleared"],
            "a cancelled medication charge must not keep blocking the encounter",
        )

    def test_cancelled_dispense_leaves_the_service_payment_lane(self):
        """3. The cashier stops being asked to collect for it."""
        _prescription, dispense, appointment, _encounter = self._prescription(qty=10.0)
        self._ready(dispense, 10.0)
        if not hasattr(appointment, "_is_active_service_clearance_pending"):
            self.skipTest("yoya_reception_bridge is not installed")
        appointment.sudo().write({"state": "in_consultation"})
        appointment.invalidate_recordset()
        self.assertTrue(
            appointment.sudo()._is_active_service_clearance_pending(),
            "an unpaid ready dispense should hold the visit in SERVICE PAYMENTS",
        )

        dispense.sudo().action_cancel()
        # reception_clearance_ok is a NON-STORED compute on the ENCOUNTER, so
        # invalidating the appointment alone would re-read a cached verdict from
        # before the cancellation and this test would pass or fail on cache
        # timing rather than on behaviour.
        self.env.invalidate_all()

        self.assertFalse(
            appointment.sudo()._is_active_service_clearance_pending(),
            "cancelling the dispense should release the visit from the lane",
        )
        self.assertFalse(
            appointment.sudo()._active_service_blocking_charges(),
            "no charge should still be presented to the cashier for collection",
        )

    def test_partially_dispensed_cancellation_is_refused(self):
        """4 + 5 + 6. Delivered medication blocks cancellation, and nothing moves."""
        if not self.inventory_ready:
            self.skipTest("hospital_inventory is not configured in this database")
        _prescription, dispense, _appointment, _encounter = self._prescription(qty=30.0)
        charges = self._ready(dispense, 10.0)
        self._pay(dispense)
        dispense.sudo().action_mark_dispensed()
        self.assertEqual(dispense.state, "partial")

        charge = charges[:1]
        before = {
            "charge_state": charge.charge_state,
            "qty_delivered": charge.qty_delivered,
            "delivery_state": charge.delivery_state,
            "delivered_qty": dispense.line_ids[:1].billing_delivered_quantity,
            "consumed_qty": dispense.line_ids[:1].inventory_consumed_quantity
            if "inventory_consumed_quantity" in dispense.line_ids._fields
            else None,
        }
        stock_before = self._stock_counts()

        with self.assertRaises(UserError):
            dispense.sudo().action_cancel()

        dispense.invalidate_recordset()
        charge.invalidate_recordset()
        self.assertEqual(dispense.state, "partial", "state must survive the refusal")
        self.assertEqual(charge.charge_state, before["charge_state"])
        self.assertEqual(charge.qty_delivered, before["qty_delivered"])
        self.assertEqual(charge.delivery_state, before["delivery_state"])
        self.assertEqual(
            dispense.line_ids[:1].billing_delivered_quantity, before["delivered_qty"]
        )
        if before["consumed_qty"] is not None:
            self.assertEqual(
                dispense.line_ids[:1].inventory_consumed_quantity,
                before["consumed_qty"],
            )
        self.assertEqual(self._stock_counts(), stock_before)

    def test_flagged_partial_with_no_delivery_is_still_cancellable(self):
        """The stuck-record path, closed.

        action_mark_partial() moves ready -> partial WITHOUT delivering anything,
        so `partial` does not by itself mean medication changed hands. A guard
        that refused the state outright would make such a record impossible to
        cancel forever: the charges would be cancelled, the state guard would
        then raise, and the transaction would roll back on every attempt. The
        refusal is keyed on delivery evidence instead, so this one cancels
        cleanly and its charges are cleaned up with it.
        """
        _prescription, dispense, _appointment, _encounter = self._prescription(qty=10.0)
        charges = self._ready(dispense, 10.0)
        dispense.sudo().action_mark_partial()
        self.assertEqual(dispense.state, "partial")
        self.assertEqual(
            sum(dispense.line_ids.mapped("billing_delivered_quantity")),
            0.0,
            "the premise of this test is that nothing was delivered",
        )

        dispense.sudo().action_cancel()
        self.env.invalidate_all()

        self.assertEqual(dispense.state, "cancelled")
        self.assertEqual(set(charges.mapped("charge_state")), {"cancelled"})
        self.assertEqual(dispense.unified_amount_due_for_clearance, 0.0)

    def test_charge_cleanup_is_idempotent(self):
        """7. A repeated cancellation neither double-cancels nor resurrects."""
        _prescription, dispense, _appointment, _encounter = self._prescription(qty=10.0)
        charges = self._ready(dispense, 10.0)
        dispense.sudo().action_cancel()
        charges.invalidate_recordset()
        snapshot = {
            charge.id: (charge.charge_state, charge.cancel_reason) for charge in charges
        }
        charge_count = self.env["hospital.charge.line"].sudo().with_context(
            active_test=False
        ).search_count(dispense._charge_domain())

        dispense.sudo().action_cancel()

        charges.invalidate_recordset()
        self.assertEqual(
            {charge.id: (charge.charge_state, charge.cancel_reason) for charge in charges},
            snapshot,
        )
        self.assertEqual(
            self.env["hospital.charge.line"].sudo().with_context(
                active_test=False
            ).search_count(dispense._charge_domain()),
            charge_count,
        )
        self.assertEqual(dispense.state, "cancelled")

    # ------------------------------------------------------------------
    # D2 -- prescription cancellation propagates to the dispense
    # ------------------------------------------------------------------
    def test_prescription_cancel_cancels_a_draft_dispense(self):
        """8. Withdrawing before pharmacy touched it withdraws both."""
        prescription, dispense, _appointment, _encounter = self._prescription(qty=10.0)
        self.assertEqual(dispense.state, "draft")

        prescription.sudo().action_cancel()

        self.assertEqual(prescription.state, "cancelled")
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "cancelled")

    def test_prescription_cancel_cancels_a_ready_dispense_and_its_charges(self):
        """9. Withdrawing after Mark Ready also clears the money."""
        prescription, dispense, _appointment, _encounter = self._prescription(qty=10.0)
        charges = self._ready(dispense, 10.0)
        self.assertEqual(set(charges.mapped("charge_state")), {"active"})

        encounter = dispense.encounter_id
        prescription.sudo().action_cancel()
        self.env.invalidate_all()

        self.assertEqual(prescription.state, "cancelled")
        self.assertEqual(dispense.state, "cancelled")
        self.assertEqual(set(charges.mapped("charge_state")), {"cancelled"})
        self.assertEqual(dispense.unified_amount_due_for_clearance, 0.0)
        self.assertTrue(
            self.env["hospital.billing.engine"]
            .sudo()
            .check_financial_clearance(encounter)["cleared"]
        )

    def test_prescription_cancel_succeeds_when_dispense_already_cancelled(self):
        """10. An already-withdrawn dispense is skipped, not refused."""
        prescription, dispense, _appointment, _encounter = self._prescription(qty=10.0)
        dispense.sudo().action_cancel()
        self.assertEqual(dispense.state, "cancelled")

        prescription.sudo().action_cancel()

        self.assertEqual(prescription.state, "cancelled")
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "cancelled")

    def test_prescription_cancel_refused_for_partial_dispense_atomically(self):
        """11. Refusal leaves the prescription, dispense, charge and stock alone."""
        if not self.inventory_ready:
            self.skipTest("hospital_inventory is not configured in this database")
        prescription, dispense, _appointment, _encounter = self._prescription(qty=30.0)
        charges = self._ready(dispense, 10.0)
        self._pay(dispense)
        dispense.sudo().action_mark_dispensed()
        self.assertEqual(dispense.state, "partial")
        stock_before = self._stock_counts()
        charge_states = charges.mapped("charge_state")

        with self.assertRaises(UserError):
            prescription.sudo().action_cancel()

        prescription.invalidate_recordset()
        dispense.invalidate_recordset()
        charges.invalidate_recordset()
        self.assertEqual(
            prescription.state, "confirmed", "the prescription must not move"
        )
        self.assertEqual(dispense.state, "partial")
        self.assertEqual(charges.mapped("charge_state"), charge_states)
        self.assertEqual(self._stock_counts(), stock_before)

    def test_prescription_cancel_refused_for_fully_dispensed(self):
        """12. Same refusal once everything has been handed over."""
        if not self.inventory_ready:
            self.skipTest("hospital_inventory is not configured in this database")
        prescription, dispense, _appointment, _encounter = self._prescription(qty=10.0)
        self._ready(dispense, 10.0)
        self._pay(dispense)
        dispense.sudo().action_mark_dispensed()
        self.assertEqual(dispense.state, "dispensed")

        with self.assertRaises(UserError):
            prescription.sudo().action_cancel()

        prescription.invalidate_recordset()
        dispense.invalidate_recordset()
        self.assertEqual(prescription.state, "confirmed")
        self.assertEqual(dispense.state, "dispensed")

    def test_cancelled_prescription_cannot_drive_a_live_dispense_again(self):
        """13. The reopen path is closed, from both the clinical and money side."""
        # (a) A dispense that never reached `ready` carries no charges, so the
        #     refusal must come from the cancelled PRESCRIPTION.
        prescription, dispense, _appointment, _encounter = self._prescription(qty=10.0)
        prescription.sudo().action_cancel()
        self.assertEqual(dispense.state, "cancelled")
        with self.assertRaises(UserError):
            dispense.sudo().action_reset_to_draft()
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "cancelled")

        # (b) A dispense cancelled from `ready` has cancelled charges, and those
        #     cannot be revived, so the refusal comes from the money side first.
        prescription_b, dispense_b, _a, _e = self._prescription(qty=10.0)
        self._ready(dispense_b, 10.0)
        prescription_b.sudo().action_cancel()
        self.assertEqual(dispense_b.state, "cancelled")
        with self.assertRaises(UserError):
            dispense_b.sudo().action_reset_to_draft()
        dispense_b.invalidate_recordset()
        self.assertEqual(dispense_b.state, "cancelled")

        # And Mark Ready is a no-op on a cancelled dispense either way, so no
        # amount of retrying puts it back in the pharmacy queue.
        dispense_b.sudo().action_mark_ready()
        dispense_b.invalidate_recordset()
        self.assertEqual(dispense_b.state, "cancelled")

"""Pharmacy Slice 0: model authority over prescriptions, dispenses and lines.

WHAT IS HELD HERE
-----------------
  * Clinical and workflow facts cannot be forged by an ordinary write -- not by
    an operator, not by sudo(), and not by a context key, because a context key
    is client input.
  * The workflow methods that OWN those facts still work end to end: confirm,
    Mark Ready, Validate Dispense (partial and full), cancel, reset.
  * The billing and inventory high-water marks move only inside their own
    module's workflow, never downwards, and bound the pharmacist's intent.
  * `partial` is a delivery fact: action_mark_partial() cannot fabricate it.
  * Composition no longer stamps the prescribing doctor as the pharmacist.

Every test runs through real res.users with real groups and calls the model
directly -- the boundary has to hold for anyone who can reach the ORM.
"""
import uuid

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

FORGED_CONTEXT = {
    "skip_dispense_write_audit": True,
    "skip_prescription_write_audit": True,
    "pharmacy_quantity_sync": True,
    "allow_state_write": True,
    "pharmacy_workflow": True,
}


@tagged("post_install", "-at_install", "pharmacy_model_authority")
class TestPharmacyModelAuthority(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.doctor_user = cls._make_user("pma_doctor", "hospital_management.group_hospital_doctor")
        cls.pharmacist = cls._make_user("pma_pharm", "hospital_management.group_hospital_pharmacist")
        cls.manager = cls._make_user("pma_manager", "hospital_management.group_hospital_manager")
        cls.accountant = cls._make_user("pma_acct", "hospital_management.group_hospital_accountant")
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Authority Test Doctor", "user_id": cls.doctor_user.id}
        )
        service = cls.env["hospital.billing.service"].sudo().create({
            "name": "Authority Test Medicine",
            "code": "T-PMA-%s" % uuid.uuid4().hex[:6],
            "service_type": "pharmacy",
            "default_price": 100.0,
            "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id,
            "uom_id": cls.uom.id,
            "prepayment_required": True,
            "tax_treatment": "exempt",
        })
        cls.medicine = cls.env["hospital.pharmacy.medicine"].sudo().create({
            "name": "Authority Test Medicine",
            "code": "PMA-%s" % uuid.uuid4().hex[:6],
            "dosage_form": "capsule",
            "route": "oral",
            "billing_service_id": service.id,
        })
        cls.inventory_ready = cls._configure_inventory()

    @classmethod
    def _make_user(cls, login, *group_xmlids):
        groups = [cls.env.ref("base.group_user").id] + [cls.env.ref(x).id for x in group_xmlids]
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s_%s@example.test" % (login, uuid.uuid4().hex[:6]),
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, groups)],
        })

    @classmethod
    def _configure_inventory(cls):
        if "hospital.inventory.item" not in cls.env:
            return False
        location = cls.env["hospital.inventory.location"].sudo().get_default_pharmacy_store()
        category = cls.env.ref("hospital_inventory.category_pharmacy_medicine", raise_if_not_found=False)
        if not (location and category):
            return False
        item = cls.env["hospital.inventory.item"].sudo().create({
            "name": "Authority Test Item %s" % uuid.uuid4().hex[:6],
            "code": "PMI-%s" % uuid.uuid4().hex[:6],
            "item_type": "medicine",
            "accounting_category": "medicine",
            "category_id": category.id,
            "unit_of_measure": "capsule",
            "standard_cost": 10.0,
            "currency_id": cls.company.currency_id.id,
            "company_id": cls.company.id,
        })
        cls.env["hospital.inventory.batch"].sudo().create({
            "item_id": item.id,
            "batch_number": "PMB-%s" % uuid.uuid4().hex[:6],
            "location_id": location.id,
            "received_date": fields.Date.today(),
            "expiry_date": fields.Date.add(fields.Date.today(), days=365),
            "quantity_on_hand": 500.0,
            "unit_cost": 10.0,
            "currency_id": cls.company.currency_id.id,
            "state": "available",
        })
        cls.medicine.sudo().write({"inventory_item_id": item.id})
        return True

    # ------------------------------------------------------------------
    def _prescription(self, qty=10.0, confirm=True):
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create({"name": "PMA Partner %s" % suffix})
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "PMA Patient %s" % suffix, "accounting_partner_id": partner.id}
        )
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": self.doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": "confirmed",
        })
        self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id,
            "appointment_id": appointment.id,
            "encounter_type": "outpatient",
            "primary_doctor_id": self.doctor.id,
            "company_id": self.company.id,
        })
        prescription = self.env["hospital.prescription"].with_user(self.doctor_user).create({
            "patient_id": patient.id,
            "physician_id": self.doctor.id,
            "appointment_id": appointment.id,
            "line_ids": [(0, 0, {
                "medicine_id": self.medicine.id,
                "medicine_name": self.medicine.name,
                "quantity": qty,
                "dosage": "500mg",
                "route": "oral",
            })],
        })
        dispense = self.env["hospital.pharmacy.dispense"]
        if confirm:
            prescription.with_user(self.doctor_user).action_confirm()
            dispense = prescription.sudo().pharmacy_dispense_ids[:1]
        return prescription.sudo(), dispense.sudo()

    def _pay(self, dispense):
        receipt = self.env["hospital.charge.receipt"].sudo().create({
            "payment_method": "cash",
            "received_at": fields.Datetime.now(),
            "received_by_id": self.accountant.id,
            "state": "draft",
            "intake_token": uuid.uuid4().hex,
        })
        for charge in dispense.charge_line_ids:
            due = charge.amount_due_for_clearance
            if due > 0:
                self.env["hospital.charge.receipt.allocation"].sudo().create(
                    {"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": due}
                )
        receipt.sudo().write({"state": "confirmed"})

    def _ready(self, dispense, intended):
        dispense.line_ids.with_user(self.pharmacist).write({"dispensed_quantity": intended})
        dispense.with_user(self.pharmacist).action_mark_ready()
        self.env.invalidate_all()

    def _validate(self, dispense):
        self._pay(dispense)
        dispense.with_user(self.pharmacist).action_mark_dispensed()
        self.env.invalidate_all()

    # ==================================================================
    # Prescription
    # ==================================================================
    def test_prescription_direct_state_write_refused_on_every_channel(self):
        prescription, _dispense = self._prescription()
        for target in ("dispensed", "cancelled", "draft"):
            with self.assertRaisesRegex(UserError, "by editing its state"):
                prescription.sudo().with_context(**FORGED_CONTEXT).write({"state": target})
        with self.assertRaises(UserError):
            prescription.with_user(self.manager).write({"state": "dispensed"})
        prescription.invalidate_recordset()
        self.assertEqual(prescription.state, "confirmed")

    def test_prescription_cannot_be_created_confirmed(self):
        prescription, _dispense = self._prescription(confirm=False)
        with self.assertRaisesRegex(UserError, "created as a draft"):
            self.env["hospital.prescription"].sudo().create({
                "patient_id": prescription.patient_id.id,
                "physician_id": self.doctor.id,
                "state": "confirmed",
            })

    def test_confirmed_prescription_clinical_fields_are_frozen(self):
        prescription, _dispense = self._prescription()
        other_doctor = self.env["hospital.doctor"].sudo().create({"name": "PMA Other"})
        other_patient = self.env["hospital.patient"].sudo().create({"name": "PMA Other Patient"})
        for vals in (
            {"physician_id": other_doctor.id},
            {"patient_id": other_patient.id},
            {"appointment_id": False},
            {"line_ids": [(0, 0, {"medicine_id": self.medicine.id, "medicine_name": "x", "quantity": 99})]},
        ):
            with self.assertRaisesRegex(UserError, "can no longer be edited"):
                prescription.sudo().with_context(**FORGED_CONTEXT).write(vals)
        # Echoing the current value is not a change, and notes stay editable.
        prescription.sudo().write({"physician_id": self.doctor.id, "notes": "Reviewed"})

    def test_confirmed_prescription_lines_are_frozen(self):
        prescription, _dispense = self._prescription()
        line = prescription.line_ids
        for vals in ({"quantity": 999.0}, {"dosage": "5g"}, {"route": "injection"},
                     {"medicine_name": "Other"}, {"instructions": "x"}, {"sequence": 99}):
            with self.assertRaisesRegex(UserError, "can no longer be edited"):
                line.sudo().with_context(**FORGED_CONTEXT).write(vals)
        with self.assertRaises(UserError):
            line.sudo().unlink()
        with self.assertRaises(UserError):
            self.env["hospital.prescription.line"].sudo().create({
                "prescription_id": prescription.id,
                "medicine_id": self.medicine.id,
                "medicine_name": "Injected",
                "quantity": 1.0,
            })
        line.invalidate_recordset()
        self.assertEqual(line.quantity, 10.0)

    def test_draft_prescription_remains_editable(self):
        prescription, _dispense = self._prescription(confirm=False)
        prescription.line_ids.with_user(self.doctor_user).write({"quantity": 12.0})
        prescription.with_user(self.doctor_user).action_confirm()
        dispense = prescription.pharmacy_dispense_ids
        self.assertEqual(dispense.line_ids.prescribed_quantity, 12.0)

    def test_reset_to_draft_still_works_but_content_stays_what_pharmacy_received(self):
        prescription, dispense = self._prescription()
        prescription.action_cancel()
        self.assertEqual(prescription.state, "cancelled")
        self.assertEqual(dispense.state, "cancelled")
        prescription.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(prescription.state, "draft")
        with self.assertRaisesRegex(UserError, "can no longer be edited"):
            prescription.line_ids.write({"quantity": 50.0})

    def test_prescription_cannot_be_marked_dispensed_ahead_of_pharmacy(self):
        prescription, dispense = self._prescription()
        with self.assertRaisesRegex(UserError, "has not been fully dispensed"):
            prescription.action_mark_dispensed()
        self.assertEqual(prescription.state, "confirmed")
        if not self.inventory_ready:
            return
        self._ready(dispense, 10.0)
        self._validate(dispense)
        self.assertEqual(dispense.state, "dispensed")
        prescription.action_mark_dispensed()
        self.assertEqual(prescription.state, "dispensed")

    # ==================================================================
    # Dispense header
    # ==================================================================
    def test_dispense_direct_state_write_refused_on_every_channel(self):
        _prescription, dispense = self._prescription()
        for target in ("ready", "partial", "dispensed", "cancelled"):
            with self.assertRaisesRegex(UserError, "by editing its state"):
                dispense.sudo().with_context(**FORGED_CONTEXT).write({"state": target})
            with self.assertRaises(UserError):
                dispense.with_user(self.pharmacist).write({"state": target})
        dispense.invalidate_recordset()
        self.assertEqual(dispense.state, "draft")
        self.assertFalse(dispense.charge_line_ids)

    def test_dispense_cannot_be_created_in_a_later_state(self):
        prescription, _dispense = self._prescription(confirm=False)
        with self.assertRaisesRegex(UserError, "created as a draft"):
            self.env["hospital.pharmacy.dispense"].sudo().create({
                "patient_id": prescription.patient_id.id,
                "state": "dispensed",
            })

    def test_dispense_identity_cannot_be_relinked(self):
        prescription, dispense = self._prescription()
        other_rx, _other = self._prescription(confirm=False)
        other_doctor = self.env["hospital.doctor"].sudo().create({"name": "PMA Relink"})
        for vals in (
            {"prescription_id": other_rx.id},
            {"patient_id": other_rx.patient_id.id},
            {"physician_id": other_doctor.id},
            {"appointment_id": other_rx.appointment_id.id},
            {"pharmacist_id": self.pharmacist.id},
        ):
            with self.assertRaisesRegex(UserError, "cannot be changed after the dispense is created"):
                dispense.sudo().with_context(**FORGED_CONTEXT).write(vals)
        # Echoing the same value and editing operational fields still work.
        dispense.with_user(self.pharmacist).write(
            {"patient_id": prescription.patient_id.id, "priority": "urgent", "notes": "x"}
        )

    def test_composition_does_not_stamp_the_prescriber_as_pharmacist(self):
        _prescription, dispense = self._prescription()
        self.assertFalse(dispense.pharmacist_id)
        self.assertEqual(dispense.create_uid, self.doctor_user)

    def test_doctor_still_cannot_write_or_operate(self):
        _prescription, dispense = self._prescription()
        with self.assertRaises(AccessError):
            dispense.line_ids.with_user(self.doctor_user).write({"dispensed_quantity": 5.0})
        with self.assertRaises(AccessError):
            dispense.with_user(self.doctor_user).action_mark_ready()

    # ==================================================================
    # Dispense lines
    # ==================================================================
    def test_line_identity_is_frozen_on_prescription_dispense(self):
        _prescription, dispense = self._prescription()
        line = dispense.line_ids
        other = self.env["hospital.pharmacy.medicine"].sudo().create({"name": "PMA Other Med"})
        for vals in ({"prescribed_quantity": 99.0}, {"medicine_id": other.id}):
            with self.assertRaisesRegex(UserError, "cannot be changed"):
                line.sudo().with_context(**FORGED_CONTEXT).write(vals)

    def test_lines_cannot_be_injected_into_a_prescription_dispense(self):
        _prescription, dispense = self._prescription()
        with self.assertRaisesRegex(UserError, "cannot be added"):
            self.env["hospital.pharmacy.dispense.line"].with_user(self.pharmacist).create({
                "dispense_id": dispense.id,
                "medicine_id": self.medicine.id,
                "prescribed_quantity": 5.0,
            })

    def test_intended_quantity_is_bounded_by_the_prescription(self):
        _prescription, dispense = self._prescription(qty=10.0)
        line = dispense.line_ids.with_user(self.pharmacist)
        with self.assertRaisesRegex(UserError, "exceeds the prescribed"):
            line.write({"dispensed_quantity": 11.0})
        with self.assertRaisesRegex(UserError, "cannot be negative"):
            line.write({"dispensed_quantity": -1.0})
        line.write({"dispensed_quantity": 4.0})
        self.assertEqual(line.dispensed_quantity, 4.0)

    def test_high_water_marks_cannot_be_written_directly(self):
        _prescription, dispense = self._prescription()
        line = dispense.line_ids
        charge = self.env["hospital.charge.line"].sudo().search([], limit=1)
        forged = line.sudo().with_context(**FORGED_CONTEXT)
        with self.assertRaisesRegex(UserError, "recorded by the billing workflow"):
            forged.write({"billing_delivered_quantity": 5.0})
        if charge:
            with self.assertRaisesRegex(UserError, "recorded by the billing workflow"):
                forged.write({"charge_line_id": charge.id})
        if "inventory_consumed_quantity" in line._fields:
            with self.assertRaisesRegex(UserError, "cannot be edited directly"):
                forged.write({"inventory_consumed_quantity": 5.0})
        with self.assertRaises(UserError):
            line.with_user(self.pharmacist).write({"billing_delivered_quantity": 5.0})
        line.invalidate_recordset()
        self.assertEqual(line.billing_delivered_quantity, 0.0)

    def test_partial_workflow_and_high_water_floor(self):
        """Mark Ready -> Validate (partial) -> raise intent -> Validate (full).

        The authorized workflow still writes every guarded fact, and after a
        delivery the intent can no longer be lowered beneath it."""
        if not self.inventory_ready:
            self.skipTest("hospital_inventory is not configured")
        prescription, dispense = self._prescription(qty=10.0)
        self._ready(dispense, 4.0)
        self.assertEqual(dispense.state, "ready")
        self.assertTrue(dispense.line_ids.charge_line_id)
        self.assertTrue(dispense.billing_blocked, "an unpaid ready dispense is blocked")
        self._validate(dispense)
        line = dispense.line_ids
        self.assertEqual(dispense.state, "partial")
        self.assertEqual(line.billing_delivered_quantity, 4.0)
        self.assertEqual(line.inventory_consumed_quantity, 4.0)

        with self.assertRaisesRegex(UserError, "already handed over"):
            line.with_user(self.pharmacist).write({"dispensed_quantity": 3.0})
        with self.assertRaises(UserError):
            line.sudo().write({"inventory_consumed_quantity": 0.0})

        line.with_user(self.pharmacist).write({"dispensed_quantity": 10.0})
        self.env.invalidate_all()
        self.assertTrue(dispense.billing_blocked, "an unbilled increment is blocked")
        # What cashier payment preparation runs: re-bill the raised intent.
        dispense.sudo()._ensure_pharmacy_billing()
        self.env.invalidate_all()
        self.assertTrue(dispense.billing_blocked, "billed but unpaid is still blocked")
        self._validate(dispense)
        self.assertEqual(dispense.state, "dispensed")
        self.assertEqual(line.billing_delivered_quantity, 10.0)
        self.assertEqual(line.inventory_consumed_quantity, 10.0)
        self.assertFalse(dispense.billing_blocked)
        self.assertEqual(prescription.state, "confirmed")
        with self.assertRaisesRegex(UserError, "cannot change"):
            line.with_user(self.pharmacist).write({"dispensed_quantity": 9.0})

    def test_mark_partial_is_refused_from_ready(self):
        _prescription, dispense = self._prescription()
        self._ready(dispense, 5.0)
        with self.assertRaisesRegex(UserError, "cannot be marked Partially Dispensed"):
            dispense.with_user(self.pharmacist).action_mark_partial()
        self.assertEqual(dispense.state, "ready")
        # Authorization is still decided first.
        with self.assertRaises(AccessError):
            dispense.with_user(self.doctor_user).action_mark_partial()

    def test_authorized_cancel_and_reopen_still_work(self):
        _prescription, dispense = self._prescription()
        dispense.with_user(self.pharmacist).action_cancel()
        self.assertEqual(dispense.state, "cancelled")
        dispense.with_user(self.pharmacist).action_reset_to_draft()
        self.assertEqual(dispense.state, "draft")

import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "pharmacy_prescription_handoff")
class TestPharmacyPrescriptionHandoff(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.doctor = cls.env["hospital.doctor"].sudo().create({"name": "Pharmacy Handoff Doctor"})
        cls.medicine = cls.env["hospital.pharmacy.medicine"].sudo().create({
            "name": "Handoff Test Medicine",
            "code": "HTM-%s" % uuid.uuid4().hex[:6],
            "strength": "500mg",
            "route": "oral",
        })

    def _patient(self, label="Patient"):
        partner = self.env["res.partner"].sudo().create({"name": "Pharmacy Handoff Partner %s" % uuid.uuid4().hex[:6]})
        return self.env["hospital.patient"].sudo().create({
            "name": "Pharmacy Handoff %s %s" % (label, uuid.uuid4().hex[:6]),
            "accounting_partner_id": partner.id,
        })

    def _appointment(self, patient):
        return self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": self.doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": "confirmed",
        })

    def _prescription(self, patient=None, appointment=None, qty=2.0):
        patient = patient or self._patient()
        appointment = appointment or self._appointment(patient)
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
                "frequency": "once a day",
                "duration": "5 days",
                "route": "oral",
                "instructions": "After food",
            })],
        })
        return prescription, encounter

    def _side_effect_counts(self):
        counts = {
            "charges": self.env["hospital.charge.line"].sudo().search_count([]),
            "bills": self.env["hospital.patient.bill"].sudo().search_count([]),
            "dispenses": self.env["hospital.pharmacy.dispense"].sudo().search_count([]),
        }
        if "hospital.charge.receipt" in self.env:
            counts["receipts"] = self.env["hospital.charge.receipt"].sudo().search_count([])
        if "hospital.fiscal.transaction" in self.env:
            counts["fiscal"] = self.env["hospital.fiscal.transaction"].sudo().search_count([])
        if "hospital.stock.consumption" in self.env:
            counts["consumption"] = self.env["hospital.stock.consumption"].sudo().search_count([])
        if "hospital.stock.movement" in self.env:
            counts["stock_moves"] = self.env["hospital.stock.movement"].sudo().search_count([])
        return counts

    def test_confirm_creates_one_linked_dispense_with_encounter_and_no_side_effects(self):
        prescription, encounter = self._prescription(qty=2.0)
        before = self._side_effect_counts()
        prescription.action_confirm()
        prescription.invalidate_recordset(["state", "pharmacy_dispense_count"])
        dispense = self.env["hospital.pharmacy.dispense"].sudo().search([("prescription_id", "=", prescription.id)])
        self.assertEqual(prescription.state, "confirmed")
        self.assertEqual(prescription.pharmacy_dispense_count, 1)
        self.assertEqual(len(dispense), 1)
        self.assertEqual(dispense.patient_id, prescription.patient_id)
        self.assertEqual(dispense.prescription_id, prescription)
        self.assertEqual(dispense.appointment_id, prescription.appointment_id)
        self.assertEqual(dispense.encounter_id, encounter)
        self.assertEqual(dispense.state, "draft")
        self.assertEqual(len(dispense.line_ids), 1)
        line = dispense.line_ids[:1]
        self.assertEqual(line.medicine_id, self.medicine)
        self.assertEqual(line.prescribed_quantity, 2.0)
        self.assertEqual(line.dispensed_quantity, 0.0)
        after = self._side_effect_counts()
        self.assertEqual(after["dispenses"], before["dispenses"] + 1)
        for key in set(after) - {"dispenses"}:
            self.assertEqual(after[key], before[key], "%s changed during prescription confirmation" % key)
        self.assertFalse(dispense.charge_line_ids)

    def test_repeated_confirm_and_smart_button_are_idempotent(self):
        prescription, _encounter = self._prescription(qty=1.0)
        prescription.action_confirm()
        dispense = prescription.pharmacy_dispense_ids[:1]
        prescription.action_confirm()
        action_one = prescription.action_view_pharmacy_dispense()
        action_two = prescription.action_view_pharmacy_dispense()
        prescription.invalidate_recordset(["pharmacy_dispense_count"])
        self.assertEqual(prescription.pharmacy_dispense_count, 1)
        self.assertEqual(action_one["res_id"], dispense.id)
        self.assertEqual(action_two["res_id"], dispense.id)
        self.assertEqual(
            self.env["hospital.pharmacy.dispense"].sudo().search_count([("prescription_id", "=", prescription.id)]),
            1,
        )

    def test_patient_appointment_mismatch_blocks_without_partial_records(self):
        patient = self._patient("Mismatch A")
        other_patient = self._patient("Mismatch B")
        appointment = self._appointment(other_patient)
        prescription = self.env["hospital.prescription"].sudo().create({
            "patient_id": patient.id,
            "physician_id": self.doctor.id,
            "appointment_id": appointment.id,
            "line_ids": [(0, 0, {
                "medicine_id": self.medicine.id,
                "medicine_name": self.medicine.name,
                "quantity": 1.0,
            })],
        })
        before = self._side_effect_counts()
        with self.assertRaisesRegex(UserError, "appointment belongs to another patient"):
            prescription.action_confirm()
        prescription.invalidate_recordset(["state", "pharmacy_dispense_count"])
        self.assertEqual(prescription.state, "draft")
        self.assertEqual(prescription.pharmacy_dispense_count, 0)
        self.assertEqual(self.env["hospital.pharmacy.dispense"].sudo().search_count([("prescription_id", "=", prescription.id)]), 0)
        self.assertEqual(self._side_effect_counts(), before)

    def test_already_confirmed_missing_dispense_can_be_recovered_by_safe_action(self):
        prescription, encounter = self._prescription(qty=3.0)
        prescription.with_context(skip_prescription_write_audit=True).write({"state": "confirmed"})
        self.assertEqual(prescription.pharmacy_dispense_count, 0)
        action = prescription.action_view_pharmacy_dispense()
        dispense = self.env["hospital.pharmacy.dispense"].sudo().browse(action["res_id"])
        prescription.invalidate_recordset(["pharmacy_dispense_count"])
        self.assertEqual(prescription.pharmacy_dispense_count, 1)
        self.assertEqual(dispense.prescription_id, prescription)
        self.assertEqual(dispense.encounter_id, encounter)
        self.assertEqual(dispense.state, "draft")
        self.assertFalse(dispense.charge_line_ids)

    def test_operational_payment_clears_pharmacy_dispense_even_when_accounting_unposted(self):
        uom = self.env["uom.uom"].sudo().search([], limit=1)
        service = self.env["hospital.billing.service"].sudo().create({
            "name": "Handoff Pharmacy Clearance Service",
            "code": "HPC-%s" % uuid.uuid4().hex[:6],
            "service_type": "pharmacy",
            "default_price": 200.0,
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
            "uom_id": uom.id,
            "prepayment_required": True,
            "tax_treatment": "exempt",
            "tax_rate": 0.0,
        })
        self.medicine.sudo().write({"billing_service_id": service.id})
        prescription, _encounter = self._prescription(qty=2.0)
        prescription.action_confirm()
        dispense = prescription.pharmacy_dispense_ids[:1]
        dispense.line_ids.sudo().write({"dispensed_quantity": 2.0})
        dispense._ensure_pharmacy_billing()
        charge = dispense.charge_line_ids[:1]
        self.assertAlmostEqual(charge.amount_due_for_clearance, 400.0, places=2)
        with self.assertRaisesRegex(UserError, "remaining"):
            dispense._assert_financially_cleared_for_dispense(persist=False)

        receipt = self.env["hospital.charge.receipt"].sudo().create({
            "payment_method": "cash",
            "received_at": fields.Datetime.now(),
            "state": "draft",
            "intake_token": uuid.uuid4().hex,
        })
        self.env["hospital.charge.receipt.allocation"].sudo().create({"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": 400.0})
        receipt.sudo().write({"state": "confirmed"})
        if "accounting_move_id" in receipt._fields:
            self.assertFalse(receipt.accounting_move_id)
        charge.invalidate_recordset(["amount_received", "amount_due_for_clearance"])
        self.assertAlmostEqual(charge.amount_due_for_clearance, 0.0, places=2)
        clearance = dispense._assert_financially_cleared_for_dispense(persist=True)
        self.assertTrue(clearance["cleared"])

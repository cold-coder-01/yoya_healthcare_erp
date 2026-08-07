import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "consultation_prepayment_gate")
class TestConsultationPrepaymentGate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.service = cls.env["hospital.billing.service"].sudo().get_default_consultation_service(cls.company)
        cls.user = cls.env.user

    def setUp(self):
        super().setUp()
        self.service.sudo().write(
            {
                "default_price": 300.0,
                "fixed_fee": True,
                "prepayment_required": True,
                "coverage_auth_required": False,
                "active": True,
                "is_default_consultation": True,
            }
        )

    def _new_confirmed_appointment(self, prepayment_required=True):
        self.service.sudo().write({"prepayment_required": prepayment_required})
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create({"name": "Prepay Gate Patient %s" % suffix})
        appointment = self.env["hospital.appointment"].sudo().create(
            {"patient_id": patient.id, "appointment_date": fields.Datetime.now()}
        )
        appointment.action_confirm()
        appointment.invalidate_recordset()
        charge = appointment.consultation_charge_id.sudo()
        charge.invalidate_recordset()
        return appointment, charge

    def _fund_charge(self, charge, amount):
        receipt = self.env["hospital.charge.receipt"].sudo().create(
            {
                "payment_method": "cash",
                "received_at": fields.Datetime.now(),
                "received_by_id": self.user.id,
                "state": "draft",
                "intake_token": uuid.uuid4().hex,
            }
        )
        self.env["hospital.charge.receipt.allocation"].sudo().create(
            {"receipt_id": receipt.id, "charge_line_id": charge.id, "amount": amount}
        )
        receipt.sudo().write({"state": "confirmed"})
        charge.invalidate_recordset()
        return receipt

    def test_fresh_prepaid_consultation_has_300_due_for_clearance(self):
        appointment, charge = self._new_confirmed_appointment(prepayment_required=True)
        self.assertEqual(appointment.state, "confirmed")
        self.assertEqual(charge.billing_basis, "prepaid")
        self.assertAlmostEqual(charge.qty_requested, 1.0, places=3)
        self.assertAlmostEqual(charge.amount_estimated, 300.0, places=2)
        self.assertAlmostEqual(charge.amount_received, 0.0, places=2)
        self.assertAlmostEqual(charge.amount_due_for_clearance, 300.0, places=2)

    def test_zero_funding_blocks_start_and_leaves_appointment_confirmed(self):
        appointment, charge = self._new_confirmed_appointment(prepayment_required=True)
        before_receipts = self.env["hospital.charge.receipt"].search_count([])
        before_moves = self.env["account.move"].search_count([])
        encounter_state = appointment.encounter_id.state
        with self.assertRaises(UserError):
            appointment.action_start_consultation()
        appointment.invalidate_recordset()
        charge.invalidate_recordset()
        self.assertEqual(appointment.state, "confirmed")
        self.assertEqual(appointment.encounter_id.state, encounter_state)
        self.assertEqual(charge.delivery_state, "pending")
        self.assertAlmostEqual(charge.amount_received, 0.0, places=2)
        self.assertEqual(self.env["hospital.charge.receipt"].search_count([]), before_receipts)
        self.assertEqual(self.env["account.move"].search_count([]), before_moves)

    def test_partial_funding_leaves_remaining_balance_and_blocks_start(self):
        appointment, charge = self._new_confirmed_appointment(prepayment_required=True)
        self._fund_charge(charge, 100.0)
        charge.invalidate_recordset()
        self.assertAlmostEqual(charge.amount_received, 100.0, places=2)
        self.assertAlmostEqual(charge.amount_due_for_clearance, 200.0, places=2)
        with self.assertRaises(UserError):
            appointment.action_start_consultation()
        appointment.invalidate_recordset()
        charge.invalidate_recordset()
        self.assertEqual(appointment.state, "confirmed")
        self.assertEqual(charge.delivery_state, "pending")

    def test_full_valid_funding_clears_and_permits_start(self):
        appointment, charge = self._new_confirmed_appointment(prepayment_required=True)
        self._fund_charge(charge, 300.0)
        charge.invalidate_recordset()
        self.assertAlmostEqual(charge.amount_due_for_clearance, 0.0, places=2)
        appointment.action_start_consultation()
        appointment.invalidate_recordset()
        charge.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertEqual(appointment.encounter_id.state, "active")
        self.assertEqual(charge.delivery_state, "in_progress")

    def test_delivery_basis_consultation_remains_unaffected(self):
        appointment, charge = self._new_confirmed_appointment(prepayment_required=False)
        self.assertEqual(charge.billing_basis, "delivery")
        self.assertAlmostEqual(charge.amount_due_for_clearance, 0.0, places=2)
        appointment.action_start_consultation()
        appointment.invalidate_recordset()
        charge.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertEqual(charge.delivery_state, "in_progress")

    def test_emergency_bypass_remains_separate_authorized_route(self):
        appointment, charge = self._new_confirmed_appointment(prepayment_required=True)
        appointment.encounter_id.sudo().write(
            {"emergency_bypass": True, "emergency_bypass_reason": "Emergency clinical need"}
        )
        charge.invalidate_recordset()
        self.assertAlmostEqual(charge.amount_due_for_clearance, 300.0, places=2)
        appointment.action_start_consultation()
        appointment.invalidate_recordset()
        charge.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertEqual(appointment.encounter_id.financial_clearance_state, "emergency_bypass")
        self.assertEqual(charge.delivery_state, "in_progress")

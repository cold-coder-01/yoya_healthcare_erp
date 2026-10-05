"""Admissions Slice 4: procedures on the unified charge engine.

The inpatient paths are exercised in hospital_admission's discharge suite.
These pin the bridge's other two shapes:

  * a procedure on an OUTPATIENT visit (reached through its appointment) is
    charged on that visit's billing account exactly like an inpatient one;
  * a procedure with NO visit keeps the legacy procedure bill, untouched.
"""
import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "procedure_billing_bridge")
class TestProcedureBillingBridge(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.kind = cls.env["hospital.procedure.type"].sudo().create(
            {"name": "Bridge Suture %s" % uuid.uuid4().hex[:4], "default_price": 400.0}
        )

    def _patient(self):
        return self.env["hospital.patient"].sudo().create(
            {"name": "Bridge Patient %s" % uuid.uuid4().hex[:6]}
        )

    def _procedure(self, patient, appointment=None):
        return self.env["hospital.procedure.request"].sudo().create({
            "patient_id": patient.id,
            "appointment_id": appointment.id if appointment else False,
            "procedure_type_id": self.kind.id,
        })

    def test_an_outpatient_procedure_is_charged_on_its_visit(self):
        patient = self._patient()
        doctor = self.env["hospital.doctor"].sudo().create({"name": "Bridge Doctor"})
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id, "doctor_id": doctor.id,
            "appointment_date": fields.Datetime.now(), "state": "confirmed",
        })
        encounter = self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id, "appointment_id": appointment.id,
            "company_id": self.env.company.id,
        })
        encounter.write({"state": "active"})

        procedure = self._procedure(patient, appointment)
        procedure.action_submit_request()
        charge = procedure._procedure_charge()
        self.assertEqual(charge.billing_account_id.encounter_id, encounter)
        self.assertEqual((charge.unit_price, charge.qty_requested), (400.0, 1.0))
        self.assertEqual(charge.delivery_state, "pending")
        self.assertEqual(
            charge.service_id, self.env.ref("hospital_procedure.billing_service_clinical_procedure")
        )
        self.assertEqual(
            charge.source_key, "hospital.procedure.request:%s:0:procedure" % procedure.id
        )

        procedure.action_mark_done()
        self.assertEqual(charge.delivery_state, "delivered")
        self.assertEqual(charge.qty_delivered, 1.0)
        with self.assertRaises(UserError):
            procedure.action_generate_procedure_bill()

    def test_no_visit_means_no_charge_through_submit_and_cancel(self):
        """The visit-bound cancel path is pinned in hospital_admission."""
        patient = self._patient()
        visitless = self._procedure(patient)
        visitless.action_submit_request()
        visitless.action_cancel()
        self.assertFalse(visitless._procedure_charge(), "no visit, no charge")

    def test_a_procedure_without_a_visit_keeps_the_legacy_bill(self):
        procedure = self._procedure(self._patient())
        procedure.action_submit_request()
        procedure.action_mark_done()
        self.assertFalse(procedure._procedure_charge())
        procedure.action_generate_procedure_bill()
        self.assertTrue(procedure.bill_id)
        self.assertEqual(procedure.bill_id.amount_total, 400.0)

"""The laboratory prepayment gate, and the unsafe default that opened it.

WHY THIS FILE EXISTS
--------------------
Slice 3 shipped laboratory ordering with a green suite and no test covering the
gate itself. LABREQ0211 then reached UAT reading READY FOR COLLECTION with its
150.00 charge unpaid, and action_mark_sample_collected() would have accepted the
specimen -- because service 9 had prepayment_required NULL, NULL is falsy, and a
falsy value snapshots billing_basis='delivery', which contributes 0.00 to
amount_due_for_clearance.

Every test below pins one link of that chain:

    service.prepayment_required
        -> charge.billing_basis          (snapshotted at creation)
            -> charge.amount_due_for_clearance
                -> check_financial_clearance()
                    -> action_mark_sample_collected() raises or does not

The first class asserts the gate holds. The second asserts the DEFAULT is now
the safe one, which is the actual fix -- a gate that works only when someone
remembers to configure it is the bug, not the feature.
"""
import uuid

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger


class LaboratoryGateCase(TransactionCase):
    """A billable laboratory request, ordered but unpaid."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Lab Gate Doctor %s" % uuid.uuid4().hex[:6]}
        )

    @classmethod
    def _service(cls, name, prepayment_required, price=150.0):
        values = {
            "name": name,
            "code": "T-GATE-%s" % uuid.uuid4().hex[:8].upper(),
            "service_type": "laboratory",
            "default_price": price,
            "company_id": cls.company.id,
            "currency_id": cls.company.currency_id.id,
            "uom_id": cls.uom.id,
            "tax_treatment": "exempt",
        }
        if prepayment_required is not None:
            values["prepayment_required"] = prepayment_required
        return cls.env["hospital.billing.service"].sudo().create(values)

    @classmethod
    def _test_for(cls, service, name="Gate Test"):
        return cls.env["hospital.laboratory.test"].sudo().create(
            {
                "name": "%s %s" % (name, uuid.uuid4().hex[:6]),
                "code": "GT%s" % uuid.uuid4().hex[:6].upper(),
                "sample_type": "blood",
                "billing_service_id": service.id,
            }
        )

    def _patient(self):
        return self.env["hospital.patient"].sudo().create(
            {"name": "Gate Patient %s" % uuid.uuid4().hex[:6]}
        )

    def _ordered_request(self, lab_test, patient=None):
        """A confirmed laboratory request: charges raised, nothing paid."""
        patient = patient or self._patient()
        request = self.env["hospital.laboratory.request"].sudo().create(
            {
                "patient_id": patient.id,
                "physician_id": self.doctor.id,
                "line_ids": [(0, 0, {"test_id": lab_test.id})],
            }
        )
        # No appointment, so the encounter must be established explicitly --
        # the model refuses to guess which visit a request belongs to.
        request.action_establish_standalone_encounter()
        request.action_confirm_request()
        return request


@tagged("post_install", "-at_install", "laboratory_prepayment_gate")
class TestLaboratoryPrepaymentGate(LaboratoryGateCase):
    """An unpaid prepaid request must not yield a specimen."""

    def test_an_unpaid_prepaid_request_cannot_be_marked_sample_collected(self):
        """THE regression Slice 3 shipped without.

        This is the assertion whose absence let LABREQ0211 reach UAT.
        """
        service = self._service("Gate Prepaid", True)
        request = self._ordered_request(self._test_for(service))

        self.assertEqual(request.state, "requested")
        self.assertTrue(request.charge_line_ids)
        self.assertEqual(request.charge_line_ids.billing_basis, "prepaid")

        with self.assertRaises(UserError):
            request.action_mark_sample_collected()

        # The refusal left the clinical state untouched.
        request.invalidate_recordset()
        self.assertEqual(request.state, "requested")

    def test_the_status_flag_agrees_with_the_gate(self):
        """billing_blocked is what the Doctor Desk renders. If it disagrees with
        action_mark_sample_collected the desk is lying in one direction or the
        other -- and READY FOR COLLECTION on a gated request is the dangerous
        direction."""
        service = self._service("Gate Agreement", True)
        request = self._ordered_request(self._test_for(service))

        self.assertTrue(request.billing_blocked)
        with self.assertRaises(UserError):
            request.action_mark_sample_collected()

    def test_a_delivery_based_service_is_collectable_unpaid_by_design(self):
        """The mirror image, asserted so the gate is not mistaken for a blanket
        prohibition. prepayment_required=False is a deliberate pay-after-service
        decision and must keep working -- the defect was never that delivery
        basis exists, only that it was what you got by saying nothing."""
        service = self._service("Gate Delivery", False)
        request = self._ordered_request(self._test_for(service))

        self.assertEqual(request.charge_line_ids.billing_basis, "delivery")
        self.assertFalse(request.billing_blocked)

        request.action_mark_sample_collected()

        request.invalidate_recordset()
        self.assertEqual(request.state, "sample_collected")

    def test_paying_the_charge_opens_the_gate(self):
        """The gate must be passable, or it is an outage rather than a control."""
        service = self._service("Gate Payable", True)
        request = self._ordered_request(self._test_for(service))
        account = request.encounter_id.billing_account_id.sudo()

        account.record_operational_payment(
            account.amount_due_for_clearance, "cash", intake_token=uuid.uuid4().hex
        )
        request.invalidate_recordset()

        self.assertFalse(request.billing_blocked)
        request.action_mark_sample_collected()
        request.invalidate_recordset()
        self.assertEqual(request.state, "sample_collected")


@tagged("post_install", "-at_install", "laboratory_prepayment_gate")
class TestPrepaymentRequiredDefault(LaboratoryGateCase):
    """The fix itself: the unsafe value is no longer the one you get for free."""

    def test_a_service_created_without_the_flag_requires_prepayment(self):
        """THE actual defect. Service 9 reached UAT unset, and unset meant
        'deliverable without payment'."""
        service = self._service("Gate Unset", None)

        self.assertTrue(
            service.prepayment_required,
            "an unconfigured service must default to requiring prepayment",
        )

    def test_an_unconfigured_service_produces_a_gated_charge(self):
        """The default has to survive the whole chain, not just the column."""
        service = self._service("Gate Unset Chain", None)
        request = self._ordered_request(self._test_for(service))

        self.assertEqual(request.charge_line_ids.billing_basis, "prepaid")
        self.assertGreater(request.charge_line_ids.amount_due_for_clearance, 0.0)
        self.assertTrue(request.billing_blocked)
        with self.assertRaises(UserError):
            request.action_mark_sample_collected()

    def test_the_column_rejects_null_at_the_database(self):
        """WHERE required=True ACTUALLY BITES, which is not where you'd guess.

        At the ORM layer a required Boolean buys nothing: Odoo coerces None to
        False, so `write({'prepayment_required': None})` stores False and raises
        nothing. Asserting a raise there would be asserting a fiction.

        The real guarantee is the NOT NULL constraint the required flag puts on
        the column. That is what stops the original defect returning through the
        routes it arrived by in the first place -- a SQL fix-up, a data import,
        or a module that adds a service row without touching this field. NULL is
        no longer a state the database will hold.
        """
        service = self._service("Gate Not Null", True)

        with self.assertRaises(Exception), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.env.cr.execute(
                    "UPDATE hospital_billing_service "
                    "SET prepayment_required = NULL WHERE id = %s",
                    (service.id,),
                )

    def test_writing_none_through_the_orm_lands_on_the_safe_value(self):
        """The complement of the test above: since Odoo coerces None, confirm it
        coerces DOWN to a definite value rather than leaving the column unset."""
        service = self._service("Gate Coerce", True)

        service.write({"prepayment_required": None})

        self.assertIs(service.prepayment_required, False)
        self.env.cr.execute(
            "SELECT prepayment_required FROM hospital_billing_service WHERE id = %s",
            (service.id,),
        )
        self.assertIsNotNone(
            self.env.cr.fetchone()[0], "the column must never hold NULL"
        )

    def test_an_explicit_false_is_still_honoured(self):
        """Safe-by-default must not become impossible-to-configure. A service
        that genuinely bills after delivery is a legitimate decision."""
        service = self._service("Gate Explicit False", False)

        self.assertFalse(service.prepayment_required)
        request = self._ordered_request(self._test_for(service))
        self.assertEqual(request.charge_line_ids.billing_basis, "delivery")

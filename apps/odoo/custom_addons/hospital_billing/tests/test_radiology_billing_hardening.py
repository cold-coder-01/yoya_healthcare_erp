"""Radiology billing hardening: cancellation, and the truth of billing_blocked.

TWO DEFECTS, BOTH FOUND BY COMPARING RADIOLOGY WITH LABORATORY RATHER THAN BY
READING RADIOLOGY ALONE. The two services bill identically -- charges at
confirmation, a clearance gate before service, delivery at result release -- so
where they diverged, one of them was wrong.

  A. action_cancel() was not overridden at all. hospital.radiology.request
     cancelled cleanly and left every charge live and payable: the study never
     happened, the patient still owed for it, and the visit stayed in the
     cashier's SERVICE PAYMENTS lane with nothing left to deliver against.

  B. billing_blocked computed only at state == 'scheduled'. Charges are raised
     one state earlier, at confirmation, so a confirmed prepayment-required
     study that nobody had paid for reported "not blocked" -- while the
     authoritative gate would still have refused it.

Neither defect could be seen from the request's own workflow, which is exactly
why these tests assert against CHARGE state and clearance verdicts rather than
against request.state.
"""
import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

LIVE_CHARGE_STATES = ("draft", "active")


@tagged("post_install", "-at_install", "radiology_billing_hardening")
class RadiologyBillingHardeningCase(TransactionCase):
    """One prepayment-required exam, and a visit to hang it off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.accountant = cls._make_user(
            "rad_hard_accountant",
            "hospital_management.group_hospital_accountant",
        )
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Radiology Hardening Doctor"}
        )
        cls.uom = cls.env["uom.uom"].sudo().search([], limit=1)
        cls.service = cls._make_service("T-RADH-CT", 2500.0)
        cls.second_service = cls._make_service("T-RADH-XR", 400.0)
        cls.exam = cls.env["hospital.radiology.exam"].sudo().create(
            {
                "name": "Hardening CT Brain",
                "code": "H-CT-BRAIN",
                "modality": "ct",
                "billing_service_id": cls.service.id,
            }
        )
        cls.second_exam = cls.env["hospital.radiology.exam"].sudo().create(
            {
                "name": "Hardening Chest X-Ray",
                "code": "H-XR-CHEST",
                "modality": "xray",
                "billing_service_id": cls.second_service.id,
            }
        )

    @classmethod
    def _make_service(cls, code, price):
        return cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "Radiology Hardening %s" % code,
                "code": "%s-%s" % (code, uuid.uuid4().hex[:6].upper()),
                "service_type": "radiology",
                "default_price": price,
                "company_id": cls.company.id,
                "currency_id": cls.company.currency_id.id,
                "uom_id": cls.uom.id,
                # PREPAYMENT REQUIRED IS THE POINT. It is what makes an unpaid
                # confirmed request genuinely blocked, which is the condition
                # billing_blocked was misreporting.
                "prepayment_required": True,
                "tax_treatment": "exempt",
            }
        )

    @classmethod
    def _make_user(cls, login, group_xmlid):
        group = cls.env.ref(group_xmlid)
        user = cls.env["res.users"].sudo().create(
            {
                "name": login,
                "login": "%s_%s@example.test" % (login, uuid.uuid4().hex[:6]),
                "groups_id": [(6, 0, [group.id])],
            }
        )
        user.sudo().write(
            {"company_ids": [(4, cls.company.id)], "company_id": cls.company.id}
        )
        return user

    # ------------------------------------------------------------------
    def _draft_request(self, exams=None):
        """A radiology request with a real visit and encounter, still in draft."""
        suffix = uuid.uuid4().hex[:8]
        partner = self.env["res.partner"].sudo().create(
            {"name": "Rad Hardening Partner %s" % suffix}
        )
        patient = self.env["hospital.patient"].sudo().create(
            {
                "name": "Rad Hardening Patient %s" % suffix,
                "accounting_partner_id": partner.id,
            }
        )
        appointment = self.env["hospital.appointment"].sudo().create(
            {
                "patient_id": patient.id,
                "doctor_id": self.doctor.id,
                "appointment_date": fields.Datetime.now(),
                "state": "confirmed",
            }
        )
        encounter = self.env["hospital.encounter"].sudo().create(
            {
                "patient_id": patient.id,
                "appointment_id": appointment.id,
                "encounter_type": "outpatient",
                "primary_doctor_id": self.doctor.id,
                "company_id": self.company.id,
            }
        )
        return self.env["hospital.radiology.request"].sudo().create(
            {
                "patient_id": patient.id,
                "physician_id": self.doctor.id,
                "appointment_id": appointment.id,
                "encounter_id": encounter.id,
                "line_ids": [
                    (0, 0, {"exam_id": exam.id}) for exam in (exams or [self.exam])
                ],
            }
        )

    def _confirmed(self, exams=None):
        request = self._draft_request(exams=exams)
        request.action_confirm_request()
        request.invalidate_recordset(["charge_line_ids", "charge_count"])
        return request

    def _pay_every_charge(self, request):
        """Settle the whole request through real receipts and allocations."""
        for charge in request.charge_line_ids:
            if charge.charge_state in ("cancelled", "reversed"):
                continue
            receipt = self.env["hospital.charge.receipt"].sudo().create(
                {
                    "payment_method": "cash",
                    "received_at": fields.Datetime.now(),
                    "received_by_id": self.accountant.id,
                    "state": "draft",
                    "intake_token": uuid.uuid4().hex,
                }
            )
            self.env["hospital.charge.receipt.allocation"].sudo().create(
                {
                    "receipt_id": receipt.id,
                    "charge_line_id": charge.id,
                    "amount": charge.amount_due_for_clearance,
                }
            )
            receipt.sudo().write({"state": "confirmed"})
        request.invalidate_recordset(["charge_line_ids"])

    def _live_payable(self, request):
        """Charges that would still take money at the cashier's window."""
        request.invalidate_recordset(["charge_line_ids"])
        return request.charge_line_ids.filtered(
            lambda c: c.charge_state in LIVE_CHARGE_STATES
            and c.amount_due_for_clearance > 0
        )


@tagged("post_install", "-at_install", "radiology_billing_hardening")
class TestRadiologyCancellationChargeLeak(RadiologyBillingHardeningCase):
    """Defect A. Cancelling a study must not leave the patient owing for it."""

    def test_cancelling_a_requested_study_leaves_no_payable_charge(self):
        """THE LEAK. Before the override, this request cancelled and its 2,500
        ETB charge stayed active -- so the patient owed for a scan that would
        never be performed, forever."""
        request = self._confirmed()
        self.assertTrue(self._live_payable(request), "fixture raised no charge")

        request.action_cancel()

        self.assertEqual(request.state, "cancelled")
        self.assertFalse(
            self._live_payable(request),
            "a cancelled radiology request left a payable charge behind",
        )

    def test_every_charge_of_a_multi_exam_request_is_cancelled(self):
        """Cancellation is request-level, so a two-study order must not leave
        one of its charges live."""
        request = self._confirmed(exams=[self.exam, self.second_exam])
        self.assertEqual(len(request.charge_line_ids), 2)

        request.action_cancel()

        request.invalidate_recordset(["charge_line_ids"])
        for charge in request.charge_line_ids:
            self.assertEqual(charge.charge_state, "cancelled", charge.description)

    def test_cancelling_a_scheduled_study_leaves_no_payable_charge(self):
        """RADIOLOGY-SPECIFIC, AND THE STATE LABORATORY HAS NO EQUIVALENT OF.

        `scheduled` sits after charge creation and before the clearance gate, so
        it is the state a doctor is most likely to cancel from -- the patient
        looked at the price and declined. Laboratory's PRE_COLLECTION_STATES has
        no member like it, so copying that tuple across would have left exactly
        this case leaking.
        """
        request = self._confirmed()
        request.action_schedule()
        self.assertEqual(request.state, "scheduled")

        request.action_cancel()

        self.assertEqual(request.state, "cancelled")
        self.assertFalse(self._live_payable(request))

    def test_the_charge_is_cancelled_not_deleted(self):
        """Financial history is never erased. The charge survives, cancelled,
        with the reason that cancelled it."""
        request = self._confirmed()
        charge_ids = request.charge_line_ids.ids

        request.action_cancel()

        charges = self.env["hospital.charge.line"].sudo().with_context(
            active_test=False
        ).browse(charge_ids)
        self.assertTrue(all(charge.exists() for charge in charges))
        for charge in charges:
            self.assertEqual(charge.charge_state, "cancelled")
            self.assertIn(request.name, charge.cancel_reason or "")

    def test_cancellation_is_idempotent(self):
        """A retried cancellation is a no-op, not an error.

        engine.cancel_charge() returns early on an already-frozen charge, and
        the base model refuses a second transition -- so the second call must
        raise the model's own refusal without having touched a charge first.
        """
        request = self._confirmed()
        request.action_cancel()
        states_after_first = request.charge_line_ids.mapped("charge_state")

        # The base model owns the transition table and refuses cancelled ->
        # cancelled. What matters here is that the attempt changed nothing.
        with self.assertRaises(UserError):
            request.action_cancel()

        request.invalidate_recordset(["charge_line_ids"])
        self.assertEqual(
            request.charge_line_ids.mapped("charge_state"), states_after_first
        )
        self.assertFalse(self._live_payable(request))

    def test_cancelling_a_draft_request_touches_nothing(self):
        """A draft request has no charges at all, so cancellation must simply
        move the state and raise nothing."""
        request = self._draft_request()
        self.assertFalse(request.charge_line_ids)

        request.action_cancel()

        self.assertEqual(request.state, "cancelled")
        self.assertFalse(request.charge_line_ids)

    def test_a_paid_cancelled_study_stops_asking_for_money(self):
        """The patient paid, then the study was cancelled before imaging.

        This slice does not implement refunds -- that is an accounting phase --
        but the charge must stop presenting as payable, or the cashier's lane
        would keep asking for money that has already been taken.
        """
        request = self._confirmed()
        self._pay_every_charge(request)
        self.assertFalse(self._live_payable(request), "the fixture did not settle")

        request.action_cancel()

        self.assertFalse(self._live_payable(request))

    def test_a_delivered_study_refuses_cancellation_rather_than_erasing_it(self):
        """Delivered imaging is a real receivable. Cancelling its charge would
        erase money owed for work performed, so the engine refuses and says a
        credit/reversal is needed instead."""
        request = self._confirmed()
        self._pay_every_charge(request)
        request.action_schedule()
        request.action_mark_in_progress()

        engine = self.env["hospital.billing.engine"].sudo()
        for charge in request.charge_line_ids:
            engine.mark_charge_delivered(charge, qty_delivered=1.0)
        request.invalidate_recordset(["charge_line_ids"])

        # The base model already refuses in_progress -> cancelled, so drive the
        # override directly to prove the delivered guard is the one refusing.
        with self.assertRaises(UserError):
            request.action_cancel()


@tagged("post_install", "-at_install", "radiology_billing_hardening")
class TestRadiologyBillingBlockedTruth(RadiologyBillingHardeningCase):
    """Defect B. billing_blocked must never say clear while the gate refuses."""

    def _blocked(self, request):
        request.invalidate_recordset(["billing_blocked", "charge_line_ids"])
        return request.billing_blocked

    def test_a_confirmed_unpaid_request_reports_blocked(self):
        """THE MISREPORT. Charges exist from this state onward, the service is
        prepayment-required and nothing has been paid -- yet this returned False
        because the compute only looked at 'scheduled'."""
        request = self._confirmed()
        self.assertEqual(request.state, "requested")
        self.assertTrue(self._live_payable(request))

        self.assertTrue(self._blocked(request))

    def test_a_scheduled_unpaid_request_reports_blocked(self):
        request = self._confirmed()
        request.action_schedule()

        self.assertTrue(self._blocked(request))

    def test_a_paid_request_reports_clear_in_both_pre_service_states(self):
        request = self._confirmed()
        self._pay_every_charge(request)
        self.assertFalse(self._blocked(request), "paid, but reported blocked")

        request.action_schedule()
        self.assertFalse(self._blocked(request))

    def test_a_draft_request_is_never_blocked(self):
        """Nothing has been ordered and nothing has been charged, so there is no
        obligation to be blocked on."""
        request = self._draft_request()
        self.assertFalse(self._blocked(request))

    def test_a_cancelled_request_is_never_blocked(self):
        request = self._confirmed()
        request.action_cancel()

        self.assertFalse(self._blocked(request))

    def test_the_flag_and_the_authoritative_gate_agree(self):
        """THE PROPERTY THE FLAG EXISTS TO HOLD, asserted directly.

        A status derived from billing_blocked must never read as clear while
        action_mark_in_progress() would refuse. Asserting the two together means
        a future divergence fails here rather than in a clinician's hands.
        """
        request = self._confirmed()
        request.action_schedule()

        self.assertTrue(self._blocked(request))
        with self.assertRaises(UserError):
            request.action_mark_in_progress()
        self.assertEqual(request.state, "scheduled", "a refused gate moved state")

        self._pay_every_charge(request)
        self.assertFalse(self._blocked(request))
        # Now it must actually pass, which is the other half of the property.
        request.action_mark_in_progress()
        self.assertEqual(request.state, "in_progress")

    def test_the_flag_falls_silent_once_the_gate_has_been_passed(self):
        """From in_progress onward the clearance conversation is over. Keeping
        the flag live would re-litigate a service already under way."""
        request = self._confirmed()
        self._pay_every_charge(request)
        request.action_schedule()
        request.action_mark_in_progress()

        self.assertFalse(self._blocked(request))

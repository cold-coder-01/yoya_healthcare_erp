"""Admissions Slice 3: the inpatient financial summary -- ACTUAL delivered care
compared with the money available against it.

  H. prescribed 10, dispensed 4        -> only 4 is actual
  I. the remaining 6 delivered later   -> actual becomes 10
  J. ordered but undelivered           -> not actual
  K. prepayment greater than actual    -> refundable, refund_due; NO cash refund
  L. actual greater than prepayment    -> due, settlement_required
  M. equal                             -> covered

Charges are raised and delivered through hospital.billing.engine -- the same
calls the pharmacy (dispensed quantity), laboratory (validated result) and
radiology (released result) workflows make -- so these tests pin the contract
the summary reads, not a copy of it. Those modules' own suites prove that
their workflows write delivery into the engine.
"""
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .test_admission_stay_billing import StayCase

H = timedelta(hours=1)


@tagged("post_install", "-at_install", "admission_financials")
class FinancialCase(StayCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.free_bed = cls._priced_bed("S3 Free", 0.0)
        cls.engine = cls.env["hospital.billing.engine"].sudo()
        cls.pharmacy_service = cls._service("S3 Medicine", "pharmacy", prepaid=True)
        cls.lab_service = cls._service("S3 Lab", "laboratory", prepaid=False)

    @classmethod
    def _service(cls, name, kind, prepaid):
        return cls.env["hospital.billing.service"].sudo().create({
            "name": "%s %s" % (name, uuid.uuid4().hex[:6]),
            "service_type": kind,
            "default_price": 100.0,
            "prepayment_required": prepaid,
        })

    def _inpatient(self, bed=None):
        admission = self._admitted(bed=bed or self.free_bed)
        return admission, admission.encounter_id

    def _charge(self, admission, qty, price, service=None, source="hospital.pharmacy.dispense"):
        charge = self.engine.create_or_update_charge(
            admission.encounter_id,
            source,
            admission.id,
            "s3_test",
            "S3 test charge",
            source_line_id=int(uuid.uuid4().int % 10**8),
            service=service or self.pharmacy_service,
            qty_requested=qty,
            unit_price=price,
        )
        self.engine.activate_charge(charge)
        return charge

    def _deliver(self, charge, qty):
        self.engine.mark_charge_delivered(charge, qty_delivered=qty)

    def _pay(self, admission, amount):
        account = self.engine.get_or_create_billing_account(admission.encounter_id)
        return account.sudo().record_operational_payment(
            amount, "cash", intake_token=uuid.uuid4().hex
        )

    def _summary(self, admission, now=None):
        self.env.invalidate_all()
        return admission._inpatient_financial_summary(now)

    def _status(self, admission):
        self.env.invalidate_all()
        return admission._inpatient_financial_status()


@tagged("post_install", "-at_install", "admission_financials")
class TestActualDeliveredCharges(FinancialCase):
    def test_h_only_the_dispensed_quantity_is_actual(self):
        admission, _ = self._inpatient()
        charge = self._charge(admission, 10, 100.0)
        self._deliver(charge, 4)
        summary = self._summary(admission)
        self.assertEqual(summary["charge_engine_actual"], 400.0)
        self.assertEqual(summary["actual_delivered"], 400.0)
        self.assertEqual(summary["estimated_or_authorized"], 1000.0)
        self.assertTrue(summary["pending_delivery"])

    def test_i_the_rest_delivered_later_becomes_actual(self):
        admission, _ = self._inpatient()
        charge = self._charge(admission, 10, 100.0)
        self._deliver(charge, 4)
        self._deliver(charge, 10)
        summary = self._summary(admission)
        self.assertEqual(summary["actual_delivered"], 1000.0)
        self.assertFalse(summary["pending_delivery"])

    def test_j_an_ordered_but_undelivered_service_is_not_actual(self):
        admission, _ = self._inpatient()
        self._charge(admission, 1, 300.0, service=self.lab_service,
                     source="hospital.laboratory.request")
        summary = self._summary(admission)
        self.assertEqual(summary["actual_delivered"], 0.0)
        self.assertEqual(summary["estimated_or_authorized"], 300.0)
        self.assertEqual(summary["financial_state"], "pending")

    def test_a_cancelled_undelivered_order_is_not_actual(self):
        admission, _ = self._inpatient()
        charge = self._charge(admission, 1, 300.0, service=self.lab_service)
        self.engine.cancel_charge(charge, reason="order withdrawn")
        summary = self._summary(admission)
        self.assertEqual(summary["actual_delivered"], 0.0)
        self.assertEqual(summary["estimated_or_authorized"], 0.0)

    def test_the_bed_stay_and_charges_are_summed_once(self):
        admission, _ = self._inpatient(self.cheap_bed)
        now = fields.Datetime.now()
        self._set_times(admission, now - 50 * H)          # 3 days x 100
        self._deliver(self._charge(admission, 2, 50.0), 2)
        summary = self._summary(admission, now)
        self.assertEqual(summary["bed_stay"], 300.0)
        self.assertEqual(summary["charge_engine_actual"], 100.0)
        self.assertEqual(summary["actual_delivered"], 400.0)

    def test_done_procedures_count_and_open_ones_do_not(self):
        if "hospital.procedure.request" not in self.env:
            self.skipTest("hospital_procedure is not installed")
        admission, _ = self._inpatient()
        kind = self.env["hospital.procedure.type"].sudo().create(
            {"name": "S3 Dressing %s" % uuid.uuid4().hex[:4], "default_price": 250.0}
        )
        Procedure = self.env["hospital.procedure.request"].sudo()
        done = Procedure.create({"patient_id": admission.patient_id.id,
                                 "admission_id": admission.id, "procedure_type_id": kind.id})
        done.action_submit_request()
        done.action_mark_done()
        pending = Procedure.create({"patient_id": admission.patient_id.id,
                                    "admission_id": admission.id, "procedure_type_id": kind.id})
        pending.action_submit_request()
        self.assertEqual(done.state, "done")
        summary = self._summary(admission)
        # Slice 4: the procedure is on the visit's billing account, counted
        # once there -- not again as a legacy procedure.
        self.assertEqual(summary["charge_engine_actual"], 250.0)
        self.assertEqual(summary["procedures_actual"], 0.0)
        self.assertEqual(summary["actual_delivered"], 250.0)


@tagged("post_install", "-at_install", "admission_financials")
class TestPrepaymentComparison(FinancialCase):
    def test_k_prepayment_greater_than_actual_is_refundable(self):
        admission, _ = self._inpatient()
        charge = self._charge(admission, 10, 100.0)
        receipt = self._pay(admission, 1000.0)
        self._deliver(charge, 4)

        # IN CARE: the excess is patient credit held toward ongoing care.
        summary = self._summary(admission)
        self.assertEqual(summary["prepayment_available"], 1000.0)
        self.assertEqual(summary["actual_delivered"], 400.0)
        self.assertEqual(summary["patient_credit"], 600.0)
        self.assertEqual(summary["refundable_balance"], 0.0)
        self.assertEqual(summary["financial_state"], "credit")
        status = self._status(admission)
        self.assertTrue(status["patient_credit"])
        self.assertFalse(status["refund_due"])

        # MEDICALLY READY: the same 600 is now the patient's to be given back.
        admission.with_user(self.doctor_user)._desk_request_medical_discharge(
            "Well; home.", str(uuid.uuid4()), admission.sudo().workflow_revision
        )
        summary = self._summary(admission)
        self.assertEqual(summary["refundable_balance"], 600.0)
        self.assertEqual(summary["remaining_due"], 0.0)
        self.assertEqual(summary["financial_state"], "refundable")

        status = self._status(admission)
        self.assertEqual(status["financial_state"], "refundable")
        self.assertTrue(status["refund_due"])
        self.assertFalse(status["patient_credit"])
        self.assertFalse(status["settlement_required"])
        self.assertFalse(status["billing_blocked"])

        # DERIVED, NOT PAID. No refund was recorded and nothing was consumed:
        # the money is still held against the charge for the Cashier.
        line = charge.sudo()
        line.invalidate_recordset()
        self.assertEqual(line.amount_refunded, 0.0)
        self.assertEqual(line.amount_received, 1000.0)
        receipts = self.env["hospital.charge.receipt"].sudo().search(
            [("billing_account_id", "=", line.billing_account_id.id)]
        )
        self.assertEqual(receipts, receipt)

    def test_l_actual_greater_than_prepayment_is_due(self):
        admission, _ = self._inpatient(self.dear_bed)
        now = fields.Datetime.now()
        self._set_times(admission, now - 50 * H)          # 3 days x 900
        charge = self._charge(admission, 10, 100.0)
        self._pay(admission, 1000.0)
        self._deliver(charge, 10)
        summary = self._summary(admission, now)
        self.assertEqual(summary["actual_delivered"], 2700.0 + 1000.0)
        self.assertEqual(summary["remaining_due"], 2700.0)
        self.assertEqual(summary["financial_state"], "due")
        status = self._status(admission)
        self.assertTrue(status["settlement_required"])
        self.assertFalse(status["refund_due"])

    def test_m_equal_is_covered(self):
        admission, _ = self._inpatient()
        charge = self._charge(admission, 10, 100.0)
        self._pay(admission, 1000.0)
        self._deliver(charge, 10)
        summary = self._summary(admission)
        self.assertEqual(summary["remaining_due"], 0.0)
        self.assertEqual(summary["refundable_balance"], 0.0)
        self.assertEqual(summary["financial_state"], "covered")
        status = self._status(admission)
        self.assertEqual(
            (status["settlement_required"], status["refund_due"], status["billing_blocked"]),
            (False, False, False),
        )

    def test_nothing_delivered_and_nothing_paid_is_pending(self):
        admission, _ = self._inpatient()
        self.assertEqual(self._status(admission)["financial_state"], "pending")

    def test_a_draft_or_cancelled_admission_is_not_applicable(self):
        draft = self._draft(bed=self.free_bed)
        self.assertEqual(self._status(draft)["financial_state"], "not_applicable")
        draft.action_cancel()
        self.assertEqual(self._status(draft)["financial_state"], "not_applicable")

    def test_a_legacy_payer_visit_follows_the_clearance_authoritys_whole_bill_credit(self):
        """Slice 4: the stay is on the charge engine, so a legacy third-party
        visit is judged exactly as check_financial_clearance() judges it --
        whole-bill payer credit, no cash from the patient."""
        admission, encounter = self._inpatient(self.cheap_bed)
        self._raw("UPDATE hospital_encounter SET payer_type = 'insurance' WHERE id = %s", (encounter.id,))
        summary = self._summary(admission)
        self.assertEqual(summary["payer_authorized"], summary["actual_delivered"])
        self.assertEqual(summary["patient_responsibility"], 0.0)
        status = self._status(admission)
        self.assertEqual(status["financial_state"], "covered")
        self.assertFalse(status["settlement_required"])

    def test_the_status_carries_no_amount(self):
        admission, _ = self._inpatient(self.dear_bed)
        status = self._status(admission)
        self.assertEqual(
            set(status),
            {"financial_state", "billing_blocked", "settlement_required", "refund_due",
             "patient_credit", "review_reasons"},
        )
        for value in (status["financial_state"], status["billing_blocked"],
                      status["settlement_required"], status["refund_due"]):
            self.assertIsInstance(value, (str, bool))
        for reason in status["review_reasons"]:
            self.assertEqual(set(reason), {"code", "message"})
            self.assertNotRegex(reason["message"], r"\d")

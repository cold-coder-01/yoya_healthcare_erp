"""Inpatient settlement at the Cashier: the model authority.

  Q. prescribed 10, delivered 4       -> only 4 in the breakdown and the due
  R. ordered lab, not completed       -> in no bucket, never collected
  S. delivered service                -> in its category
  T. bed/stay                         -> bed_stay, posted before settling
  U. payer-covered                    -> not the patient's due
  V. refundable                       -> refund_due lane once medically ready
  H/I/J. exact, partial, remainder    -> due -> part_paid -> settled; the
                                         discharge gate follows by itself
  K. more than due                    -> refused, nothing charged
  L. zero / negative                  -> refused
  M. replay                           -> same receipt; reused key refused
  N. refusal after the stay posting   -> the posting rolls back with it

Money moves through record_operational_payment() as the CASHIER; the figures
are _inpatient_financial_summary() -- the discharge gate's own.
"""
import uuid

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from ..models.admission_cashier import CashierSettlementError
from .test_admission_financials import H, FinancialCase

G_CASHIER = "hospital_billing.group_hospital_cashier"


@tagged("post_install", "-at_install", "admission_cashier")
class CashierSettlementCase(FinancialCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cashier = cls._make_user("adm_cashier", [G_CASHIER])

    def _settle(self, admission, amount, user=None, key=None, quote=None, **kwargs):
        """Settle quoting the figures as they stand (what the desk shows),
        unless a test passes its own quote."""
        if quote is None:
            self.env.invalidate_all()
            quote = admission.sudo()._inpatient_financial_summary()["quote"]
        receipt, replayed = admission.with_user(user or self.cashier)._cashier_record_settlement(
            amount, kwargs.pop("method", "cash"), idempotency_key=key or uuid.uuid4().hex,
            quote=quote, **kwargs
        )
        self.env.invalidate_all()
        return receipt, replayed

    def _ready(self, admission):
        """The physician declares the patient medically ready."""
        self.env.invalidate_all()
        admission.with_user(self.doctor_user)._desk_request_medical_discharge(
            "Well; home.", str(uuid.uuid4()), admission.sudo().workflow_revision
        )
        self.env.invalidate_all()

    def _facts(self, admission, now=None, user=None):
        self.env.invalidate_all()
        return admission.with_user(user or self.cashier)._cashier_detail(now)

    def _due_stay(self, days_hours=50):
        """A dear-bed stay, 3 bed-days x 900 unposted, 1000 of medication
        prepaid and delivered: 2700 due. A fresh bed each time, so a test
        may hold several such stays at once."""
        admission, _ = self._inpatient(self._priced_bed("S3 Due %s" % uuid.uuid4().hex[:4], 900.0))
        self._set_times(admission, fields.Datetime.now() - days_hours * H)
        charge = self._charge(admission, 10, 100.0)
        self._pay(admission, 1000.0)
        self._deliver(charge, 10)
        return admission

    def _receipts(self, admission):
        return self.env["hospital.charge.receipt"].sudo().search(
            [("billing_account_id.encounter_id", "=", admission.encounter_id.id)]
        )


@tagged("post_install", "-at_install", "admission_cashier")
class TestDeliveredBreakdown(CashierSettlementCase):
    def test_q_r_s_t_the_breakdown_partitions_the_actual(self):
        admission, _ = self._inpatient(self.dear_bed)
        now = fields.Datetime.now()
        self._set_times(admission, now - 50 * H)                     # T: 3 x 900
        medicine = self._charge(admission, 10, 100.0)                # Q: 10 prescribed
        self._deliver(medicine, 4)                                   #    4 delivered
        self._charge(admission, 1, 300.0, service=self.lab_service,  # R: ordered only
                     source="hospital.laboratory.request")
        done_lab = self._charge(admission, 1, 150.0, service=self.lab_service,
                                source="hospital.laboratory.request")
        self._deliver(done_lab, 1)                                   # S: delivered

        summary = self._summary(admission, now)
        by = summary["delivered_by_category"]
        self.assertEqual(by["bed_stay"], 2700.0)
        self.assertEqual(by["medication"], 400.0)
        self.assertEqual(by["laboratory"], 150.0)
        self.assertEqual(by["radiology"], 0.0)
        self.assertEqual(sum(by.values()), summary["actual_delivered"])
        self.assertEqual(summary["actual_delivered"], 3250.0)
        self.assertEqual(summary["remaining_due"], 3250.0)
        self.assertTrue(summary["pending_delivery"])

        facts = self._facts(admission, now)
        self.assertEqual(facts["lane"], "due")
        self.assertEqual(facts["source"], "inpatient_settlement")

    def test_u_a_payer_covered_stay_is_not_the_patients_due(self):
        admission, encounter = self._inpatient(self.cheap_bed)
        self._raw("UPDATE hospital_encounter SET payer_type = 'insurance' WHERE id = %s", (encounter.id,))
        facts = self._facts(admission)
        self.assertEqual(facts["summary"]["patient_responsibility"], 0.0)
        self.assertEqual(facts["summary"]["remaining_due"], 0.0)
        self.assertEqual(facts["lane"], "settled")
        rows, _ = self.env["hospital.admission"].with_user(self.cashier)._cashier_inpatient_census(
            search=admission.patient_id.name
        )
        self.assertFalse(rows)


@tagged("post_install", "-at_install", "admission_cashier")
class TestSettlementPayment(CashierSettlementCase):
    def test_h_exact_settlement_covers_the_stay_and_opens_the_gate(self):
        admission = self._due_stay()
        before = self._facts(admission)
        # The stay is not posted yet; the summary still owes it.
        self.assertEqual(before["summary"]["remaining_due"], 2700.0)
        self.assertEqual(before["summary"]["stay_unposted"], 2700.0)
        self.assertEqual(
            admission.sudo()._discharge_financial_refusal(before["summary"]),
            "admission_settlement_required",
        )

        receipt, replayed = self._settle(admission, 2700.0)
        self.assertFalse(replayed)
        self.assertEqual(receipt.state, "confirmed")
        self.assertEqual(receipt.received_by_id, self.cashier)
        self.assertEqual(receipt.amount, 2700.0)

        after = self._facts(admission)
        self.assertEqual(after["summary"]["remaining_due"], 0.0)
        self.assertEqual(after["summary"]["financial_state"], "covered")
        self.assertEqual(after["summary"]["stay_unposted"], 0.0)
        self.assertEqual(after["lane"], "settled")
        self.assertEqual(after["settlement_paid"], 2700.0)
        status = admission.sudo()._inpatient_financial_status()
        self.assertFalse(status["settlement_required"])
        self.assertIsNone(admission.sudo()._discharge_financial_refusal(after["summary"]))

    def test_i_j_partial_then_the_remainder(self):
        admission = self._due_stay()
        self._settle(admission, 1000.0)
        partial = self._facts(admission)
        self.assertEqual(partial["summary"]["remaining_due"], 1700.0)
        self.assertEqual(partial["lane"], "part_paid")
        self.assertTrue(admission.sudo()._inpatient_financial_status()["settlement_required"])
        self.assertEqual(
            admission.sudo()._discharge_financial_refusal(partial["summary"]),
            "admission_settlement_required",
        )

        self._settle(admission, 1700.0)
        done = self._facts(admission)
        self.assertEqual(done["summary"]["remaining_due"], 0.0)
        self.assertEqual(done["lane"], "settled")
        self.assertEqual(len(done["settlement_receipts"]), 2)

    def test_the_cash_lands_on_delivered_charges_first(self):
        admission, _ = self._inpatient(self.free_bed)
        # Created FIRST (lower id): an ordered, unpaid, undelivered lab.
        ordered = self._charge(admission, 1, 300.0, service=self.lab_service,
                               source="hospital.laboratory.request")
        given = self._charge(admission, 2, 100.0)
        self._deliver(given, 2)
        self.assertEqual(self._facts(admission)["summary"]["remaining_due"], 200.0)

        receipt, _ = self._settle(admission, 200.0)
        self.assertEqual(receipt.allocation_ids.charge_line_id, given)
        ordered.invalidate_recordset()
        self.assertEqual(ordered.sudo().amount_received, 0.0)

    def test_k_more_than_due_is_refused_and_nothing_is_charged(self):
        admission = self._due_stay()
        receipts = self._receipts(admission)
        with self.assertRaises(CashierSettlementError) as caught:
            with self.env.cr.savepoint():
                self._settle(admission, 2700.01)
        self.assertEqual(caught.exception.code, "inpatient_payment_exceeds_due")
        self.assertEqual(self._receipts(admission), receipts)

    def test_l_zero_and_negative_are_refused(self):
        admission = self._due_stay()
        for amount in (0, 0.0, -5.0, float("nan"), True, "100"):
            with self.assertRaises(CashierSettlementError) as caught:
                self._settle(admission, amount)
            self.assertEqual(caught.exception.code, "inpatient_invalid_amount")

    def test_m_a_replay_returns_the_same_receipt(self):
        admission = self._due_stay()
        key = uuid.uuid4().hex
        first, replayed = self._settle(admission, 500.0, key=key)
        self.assertFalse(replayed)
        again, replayed = self._settle(admission, 500.0, key=key)
        self.assertTrue(replayed)
        self.assertEqual(again, first)
        self.assertEqual(self._facts(admission)["summary"]["remaining_due"], 2200.0)
        # The same key with different terms is refused by the canonical intake.
        with self.assertRaises(ValidationError):
            self._settle(admission, 600.0, key=key)

    def test_m_a_key_is_scoped_to_its_admission(self):
        one, other = self._due_stay(), self._due_stay()
        key = uuid.uuid4().hex
        first, _ = self._settle(one, 500.0, key=key)
        second, replayed = self._settle(other, 500.0, key=key)
        self.assertFalse(replayed)
        self.assertNotEqual(first, second)

    def test_n_a_refusal_rolls_the_stay_posting_back(self):
        admission = self._due_stay()
        stay_before = admission.sudo()._stay_charges()
        self.assertFalse(stay_before)
        with self.assertRaises(CashierSettlementError):
            with self.env.cr.savepoint():
                self._settle(admission, 99999.0)
        self.env.invalidate_all()
        self.assertFalse(admission.sudo()._stay_charges())

    def test_v_refundable_is_its_own_lane_and_not_collectable(self):
        admission, _ = self._inpatient()
        charge = self._charge(admission, 10, 100.0)
        self._pay(admission, 1000.0)
        self._deliver(charge, 4)
        # Credit DURING care is the advance still working, not a refund owed.
        self.assertEqual(self._facts(admission)["lane"], "settled")
        self._ready(admission)
        facts = self._facts(admission)
        self.assertEqual(facts["lane"], "refund_due")
        self.assertEqual(facts["source"], "refund_due")
        self.assertEqual(facts["summary"]["refundable_balance"], 600.0)
        with self.assertRaises(CashierSettlementError) as caught:
            self._settle(admission, 10.0)
        self.assertEqual(caught.exception.code, "inpatient_nothing_due")

    def test_needs_review_refuses_payment(self):
        admission, _ = self._inpatient(self.cheap_bed)
        now = fields.Datetime.now()
        self._set_times(admission, now - 50 * H)
        admission.sudo()._sync_stay_charges(now)           # 3 bed-days posted
        self._set_times(admission, now - 1 * H)             # the stay is now 1 day
        facts = self._facts(admission, now)
        self.assertEqual(facts["lane"], "needs_review")
        self.assertEqual(facts["source"], "needs_review")
        with self.assertRaises(CashierSettlementError) as caught:
            self._settle(admission, 50.0)
        self.assertEqual(caught.exception.code, "inpatient_financial_review_required")


@tagged("post_install", "-at_install", "admission_cashier")
class TestSettlementAuthority(CashierSettlementCase):
    def test_money_roles_may_settle(self):
        for user in (self.cashier, self.accountant, self.manager, self.admin):
            admission = self._due_stay()
            receipt, _ = self._settle(admission, 100.0, user=user)
            self.assertEqual(receipt.received_by_id, user)

    def test_other_roles_may_neither_see_nor_settle(self):
        admission = self._due_stay()
        for user in (self.receptionist, self.nurse, self.doctor_user, self.pharmacist, self.lab_tech):
            with self.assertRaises(AccessError):
                self.env["hospital.admission"].with_user(user)._cashier_inpatient_census()
            with self.assertRaises(AccessError):
                self._settle(admission, 100.0, user=user)
        self.assertFalse(self._facts(admission)["settlement_receipts"])

    def test_a_sudo_record_does_not_authorize_itself(self):
        admission = self._due_stay()
        with self.assertRaises(AccessError):
            admission.with_user(self.nurse).sudo()._cashier_record_settlement(
                100.0, "cash", idempotency_key=uuid.uuid4().hex
            )

    def test_the_census_includes_a_prior_day_active_stay(self):
        admission = self._due_stay(days_hours=5 * 24)
        rows, truncated = self.env["hospital.admission"].with_user(self.cashier)._cashier_inpatient_census(
            search=admission.patient_id.name
        )
        self.assertFalse(truncated)
        self.assertEqual([row["admission"]["id"] for row in rows], [admission.id])
        self.assertEqual(rows[0]["lane"], "due")
        # Cashier-safe by construction: no clinical key anywhere.
        flat = repr(rows[0]).lower()
        for word in ("diagnos", "discharge_summary", "physician", "clinical", "admission_reason"):
            self.assertNotIn(word, flat)

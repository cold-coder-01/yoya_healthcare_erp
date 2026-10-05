"""Inpatient advance + final settlement: the model authority.

  A. estimate 50,000, advance 50,000, actual 48,000 -> refund due 2,000
  B.                                    actual 57,000 -> due 7,000
  C.                                    actual 50,000 -> even
  D. advance 30,000, actual 50,000                  -> due 20,000
  E. advance 60,000, actual 50,000                  -> refund 10,000
  F. partial settlement                             -> discharge still blocked
  G. full settlement                                -> discharge happens
  H. ordered, undelivered lab                       -> not actual
  I. prescribed 10, dispensed 4                     -> 4 billed
  J. transferred patient                            -> bed-days per segment
  K. a new bed-day before payment                   -> stale quote refused
  L. repeated key                                   -> same receipt
  M. two cashiers, one balance                      -> no double collection
  N. the doctor cannot take an advance
  O. the cashier cannot touch the estimate
  P. the ward nurse sees no amount
  Q. a refund never touches delivered care, invoices or revenue

The deposit charge is money, never care: it never enters the actual, the
estimate of care, "pending delivery" or the payer share.
"""
import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from ..models.admission_authority import AdmissionDeskError, AdmissionWorkflowError
from ..models.admission_cashier import CashierSettlementError
from .test_admission_cashier_settlement import CashierSettlementCase

H = timedelta(hours=1)


@tagged("post_install", "-at_install", "admission_advance")
class AdvanceCase(CashierSettlementCase):
    def _estimate(self, admission, amount, user=None, reason="Expected surgical stay"):
        self.env.invalidate_all()
        result = admission.with_user(user or self.doctor_user)._desk_set_estimate(
            amount, reason, str(uuid.uuid4()), admission.sudo().workflow_revision
        )
        self.env.invalidate_all()
        return result

    def _advance(self, admission, amount, user=None, key=None):
        receipt, replayed = admission.with_user(user or self.cashier)._cashier_record_advance(
            amount, "cash", idempotency_key=key or uuid.uuid4().hex
        )
        self.env.invalidate_all()
        return receipt, replayed

    def _refund(self, admission, amount, user=None, key=None, reason="Unused advance returned"):
        result = admission.with_user(user or self.accountant)._cashier_record_refund(
            amount, reason, key or str(uuid.uuid4())
        )
        self.env.invalidate_all()
        return result

    def _care(self, admission, amount):
        """`amount` of care actually delivered (a dispensed medicine)."""
        charge = self._charge(admission, 1, amount)
        self._deliver(charge, 1)
        return charge

    def _stay(self, estimate=50000.0, advance=50000.0, actual=0.0):
        admission, _ = self._inpatient(self._priced_bed("ADV %s" % uuid.uuid4().hex[:4], 0.0))
        if estimate:
            self._estimate(admission, estimate)
        if advance:
            self._advance(admission, advance)
        if actual:
            self._care(admission, actual)
        return admission

    def _summary(self, admission, now=None):
        self.env.invalidate_all()
        return admission.sudo()._inpatient_financial_summary(now)


@tagged("post_install", "-at_install", "admission_advance")
class TestSettlementScenarios(AdvanceCase):
    def test_a_advance_exceeds_actual_refund_due(self):
        admission = self._stay(actual=48000.0)
        # In care: the 2,000 is the advance still working, not a refund.
        summary = self._summary(admission)
        self.assertEqual(summary["settlement_state"], "credit")
        self.assertEqual(summary["patient_credit"], 2000.0)
        self.assertEqual(summary["refundable_balance"], 0.0)
        self._ready(admission)
        summary = self._summary(admission)
        self.assertEqual(summary["estimate_amount"], 50000.0)
        self.assertEqual(summary["advance_received"], 50000.0)
        self.assertEqual(summary["actual_delivered"], 48000.0)
        self.assertEqual(summary["advance_applied"], 48000.0)
        self.assertEqual(summary["unapplied_credit"], 2000.0)
        self.assertEqual(summary["refundable_balance"], 2000.0)
        self.assertEqual(summary["settlement_difference"], 2000.0)
        self.assertEqual(summary["settlement_state"], "refund_due")
        self.assertIsNone(admission.sudo()._discharge_financial_refusal(summary))

    def test_b_actual_exceeds_advance_due(self):
        summary = self._summary(self._stay(actual=57000.0))
        self.assertEqual(summary["remaining_due"], 7000.0)
        self.assertEqual(summary["settlement_difference"], -7000.0)
        self.assertEqual(summary["settlement_state"], "due")

    def test_the_care_stage_decides_what_an_excess_means(self):
        """The UAT defect (Hana, ENC13994): initial-clearance money on
        undelivered lines exceeded the first, still-unposted bed-day. In care
        that is credit toward care -- never "refund due" on any desk."""
        bed = self._priced_bed("CR %s" % uuid.uuid4().hex[:4], 1200.0)
        admission, _ = self._inpatient(bed)
        self._set_times(admission, fields.Datetime.now() - 2 * H)       # 1st bed-day
        self._charge(admission, 1, 1500.0, service=self.lab_service,       # ordered,
                     source="hospital.laboratory.request")                 # undelivered
        self._pay(admission, 1500.0)                                       # prepaid
        summary = self._summary(admission)
        self.assertEqual(summary["actual_delivered"], 1200.0)
        self.assertEqual(summary["patient_funds"], 1500.0)
        self.assertEqual(summary["patient_credit"], 300.0)                 # money kept
        self.assertEqual(summary["refundable_balance"], 0.0)
        self.assertEqual(summary["financial_state"], "credit")
        self.assertEqual(summary["settlement_state"], "credit")
        status = admission.sudo()._inpatient_financial_status()
        self.assertEqual((status["refund_due"], status["patient_credit"]), (False, True))
        self.assertEqual(self._facts(admission)["lane"], "settled")
        with self.assertRaises(CashierSettlementError):
            self._refund(admission, 300.0)
        self._ready(admission)
        status = admission.sudo()._inpatient_financial_status()
        self.assertEqual(
            (status["financial_state"], status["refund_due"], status["patient_credit"]),
            ("refundable", True, False),
        )
        self.assertEqual(self._facts(admission)["lane"], "refund_due")

    def test_even_and_due_after_medical_readiness(self):
        even = self._stay(actual=50000.0)
        due = self._stay(actual=57000.0)
        self._ready(even)
        self._ready(due)
        self.assertEqual(self._summary(even)["settlement_state"], "even")
        self.assertEqual(self._summary(due)["settlement_state"], "due")
        self.assertEqual(self._summary(due)["remaining_due"], 7000.0)

    def test_c_even(self):
        summary = self._summary(self._stay(actual=50000.0))
        self.assertEqual(summary["remaining_due"], 0.0)
        self.assertEqual(summary["refundable_balance"], 0.0)
        self.assertEqual(summary["settlement_state"], "even")

    def test_d_part_advance(self):
        summary = self._summary(self._stay(advance=30000.0, actual=50000.0))
        self.assertEqual(summary["advance_received"], 30000.0)
        self.assertEqual(summary["remaining_due"], 20000.0)

    def test_e_large_advance(self):
        admission = self._stay(estimate=60000.0, advance=60000.0, actual=50000.0)
        self.assertEqual(self._summary(admission)["settlement_state"], "credit")
        self._ready(admission)
        summary = self._summary(admission)
        self.assertEqual(summary["refundable_balance"], 10000.0)
        self.assertEqual(summary["settlement_state"], "refund_due")

    def test_f_g_partial_keeps_discharge_blocked_then_full_discharges(self):
        admission = self._stay(actual=57000.0)
        self._ready(admission)
        self._settle(admission, 3000.0)
        summary = self._summary(admission)
        self.assertEqual(summary["remaining_due"], 4000.0)
        self.assertEqual(summary["settlement_paid"], 3000.0)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            with self.env.cr.savepoint():
                admission.with_user(self.receptionist)._finalize_discharge()
        self.assertEqual(caught.exception.code, "admission_settlement_required")

        self._settle(admission, 4000.0)
        summary = self._summary(admission)
        self.assertEqual(summary["settlement_state"], "even")
        self.assertEqual(summary["patient_funds"], 57000.0)
        self.assertEqual(summary["advance_received"], 50000.0)
        self.assertEqual(summary["settlement_paid"], 7000.0)
        admission.with_user(self.receptionist)._finalize_discharge()
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "discharged")
        self.assertEqual(admission.sudo().encounter_id.state, "completed")

    def test_h_i_only_delivered_care_counts(self):
        admission = self._stay(actual=0.0)
        self._charge(admission, 1, 300.0, service=self.lab_service,
                     source="hospital.laboratory.request")          # H: ordered only
        medicine = self._charge(admission, 10, 100.0)                  # I: 10 prescribed
        self._deliver(medicine, 4)                                     #    4 dispensed
        summary = self._summary(admission)
        self.assertEqual(summary["actual_delivered"], 400.0)
        self.assertEqual(summary["delivered_by_category"]["laboratory"], 0.0)
        self.assertEqual(summary["delivered_by_category"]["medication"], 400.0)

    def test_j_transfer_keeps_bed_day_segments(self):
        admission, _ = self._inpatient(self.dear_bed)                 # 900 / day
        self._estimate(admission, 5000.0)
        now = fields.Datetime.now()
        self._move(admission, self.cheap_bed)                          # 100 / day
        self._set_times(admission, now - 50 * H, moves=[now - 26 * H])
        summary = self._summary(admission, now)
        expected = admission.sudo()._bed_stay_breakdown(now)["bed_total"]
        self.assertEqual(summary["delivered_by_category"]["bed_stay"], expected)
        # Periods start at -50h (dear ward) and at -26h and -2h (after the move,
        # cheap ward): each period is priced by the segment it STARTS in.
        self.assertEqual(expected, 900.0 + 100.0 + 100.0)
        stay_stage = next(stage for stage in summary["stages"] if stage["key"] == "stay")
        self.assertEqual(stay_stage["amount"], expected)
        self.assertEqual(stay_stage["status"], "complete")

    def test_k_a_new_bed_day_makes_the_quote_stale(self):
        admission, _ = self._inpatient(self._priced_bed("K %s" % uuid.uuid4().hex[:4], 800.0))
        now = fields.Datetime.now()
        self._set_times(admission, now - 20 * H)                       # 1 bed-day
        quote = self._summary(admission)["quote"]
        self.assertEqual(self._summary(admission)["remaining_due"], 800.0)
        self._set_times(admission, now - 30 * H)                       # a 2nd day starts
        with self.assertRaises(CashierSettlementError) as caught:
            with self.env.cr.savepoint():
                self._settle(admission, 800.0, quote=quote)
        self.assertEqual(caught.exception.code, "inpatient_quote_stale")
        fresh = self._summary(admission)
        self.assertEqual(fresh["remaining_due"], 1600.0)
        self._settle(admission, 1600.0, quote=fresh["quote"])
        self.assertEqual(self._summary(admission)["remaining_due"], 0.0)

    def test_k_a_settlement_must_quote(self):
        admission = self._stay(actual=57000.0)
        with self.assertRaises(CashierSettlementError) as caught:
            admission.with_user(self.cashier)._cashier_record_settlement(
                100.0, "cash", idempotency_key=uuid.uuid4().hex, quote=None
            )
        self.assertEqual(caught.exception.code, "inpatient_quote_required")

    def test_l_repeated_keys_never_take_money_twice(self):
        admission = self._stay(advance=0.0, actual=57000.0)
        key = uuid.uuid4().hex
        first, replayed = self._advance(admission, 20000.0, key=key)
        again, replayed_again = self._advance(admission, 20000.0, key=key)
        self.assertEqual((first, replayed, replayed_again), (again, False, True))
        self.assertEqual(self._summary(admission)["advance_received"], 20000.0)

        key = uuid.uuid4().hex
        one, _ = self._settle(admission, 1000.0, key=key)
        two, replayed = self._settle(admission, 1000.0, key=key)
        self.assertEqual(one, two)
        self.assertTrue(replayed)
        self.assertEqual(self._summary(admission)["remaining_due"], 57000.0 - 20000.0 - 1000.0)

    def test_m_two_cashiers_cannot_collect_one_balance_twice(self):
        admission = self._stay(actual=57000.0)
        quote = self._summary(admission)["quote"]
        self._settle(admission, 7000.0, quote=quote)
        # The second cashier was shown the same 7,000 and the same quote.
        with self.assertRaises(CashierSettlementError) as caught:
            with self.env.cr.savepoint():
                self._settle(admission, 7000.0, quote=quote, user=self.accountant)
        self.assertIn(caught.exception.code, ("inpatient_quote_stale", "inpatient_nothing_due"))
        self.assertEqual(self._summary(admission)["patient_funds"], 57000.0)


@tagged("post_install", "-at_install", "admission_advance")
class TestAdvanceAndEstimate(AdvanceCase):
    def test_the_deposit_is_money_never_care(self):
        admission = self._stay(actual=0.0)
        summary = self._summary(admission)
        self.assertEqual(summary["actual_delivered"], 0.0)
        self.assertEqual(summary["estimated_or_authorized"], 0.0)
        self.assertFalse(summary["pending_delivery"])
        self.assertEqual(summary["patient_funds"], 50000.0)
        deposit = admission.sudo()._deposit_charge()
        self.assertEqual(deposit.amount_estimated, 50000.0)
        self.assertEqual(deposit.qty_delivered, 0.0)
        self.assertEqual(deposit.billing_basis, "delivery")

    def test_the_advance_is_capped_by_the_estimate_and_follows_revisions(self):
        admission = self._stay(estimate=10000.0, advance=6000.0)
        with self.assertRaises(CashierSettlementError) as caught:
            self._advance(admission, 4000.01)
        self.assertEqual(caught.exception.code, "inpatient_advance_exceeds_estimate")
        self._estimate(admission, 15000.0)                              # revised up
        self.assertEqual(admission.sudo().estimate_revision, 2)
        self._advance(admission, 9000.0)
        self.assertEqual(self._summary(admission)["advance_received"], 15000.0)
        facts = self._facts(admission)
        self.assertEqual(facts["advance"]["outstanding"], 0.0)

    def test_no_estimate_no_advance_and_not_after_medical_discharge(self):
        admission, _ = self._inpatient()
        with self.assertRaises(CashierSettlementError) as caught:
            self._advance(admission, 100.0)
        self.assertEqual(caught.exception.code, "inpatient_no_estimate")
        self._estimate(admission, 1000.0)
        self._ready(admission)
        with self.assertRaises(CashierSettlementError) as caught:
            self._advance(admission, 100.0)
        self.assertEqual(caught.exception.code, "inpatient_advance_not_open")

    def test_the_advance_lane(self):
        admission = self._stay(estimate=10000.0, advance=0.0)
        facts = self._facts(admission)
        self.assertEqual(facts["lane"], "advance_required")
        self.assertEqual(facts["source"], "advance")
        self.assertEqual(facts["advance"]["outstanding"], 10000.0)
        self._advance(admission, 10000.0)
        self.assertEqual(self._facts(admission)["lane"], "settled")

    def test_the_estimate_is_audited_and_replayable(self):
        admission, _ = self._inpatient()
        token = str(uuid.uuid4())
        revision = admission.sudo().workflow_revision
        admission.with_user(self.doctor_user)._desk_set_estimate(1000.0, "Plan", token, revision)
        _, replayed = admission.with_user(self.doctor_user)._desk_set_estimate(
            1000.0, "Plan", token, revision
        )
        self.assertTrue(replayed)
        self.env.invalidate_all()
        record = admission.sudo()
        self.assertEqual(
            (record.estimated_amount, record.estimate_revision, record.estimated_by_id),
            (1000.0, 1, self.doctor_user),
        )
        self.assertTrue(record.estimated_at)
        # An estimate moves no bed or state: it never turns another desk's
        # pending transfer / discharge (revision-checked) stale.
        self.assertEqual(record.workflow_revision, revision)

    def test_n_the_doctor_cannot_take_money(self):
        admission = self._stay(advance=0.0)
        with self.assertRaises(AccessError):
            self._advance(admission, 100.0, user=self.doctor_user)
        with self.assertRaises(AccessError):
            self._settle(admission, 100.0, user=self.doctor_user)

    def test_o_the_cashier_cannot_touch_the_estimate(self):
        admission = self._stay(advance=0.0)
        with self.assertRaises(AdmissionDeskError) as caught:
            self._estimate(admission, 1.0, user=self.cashier)
        self.assertEqual(caught.exception.code, "admission_not_authorized")
        for user in (self.cashier, self.manager, self.admin):
            with self.assertRaises(AdmissionWorkflowError) as caught:
                admission.with_user(user).sudo().write({"estimated_amount": 1.0})
            self.assertEqual(caught.exception.code, "admission_estimate_write_refused")
        self.assertEqual(admission.sudo().estimated_amount, 50000.0)

    def test_p_the_ward_nurse_sees_no_amount(self):
        admission = self._stay(advance=0.0)
        with self.assertRaises(AccessError):
            admission.with_user(self.nurse).read(["estimated_amount"])
        with self.assertRaises(AccessError):
            self.env["hospital.admission"].with_user(self.nurse)._cashier_inpatient_census()
        with self.assertRaises(AccessError):
            self.env["hospital.admission"].with_user(self.nurse)._admissions_settlement_find(admission.id)


@tagged("post_install", "-at_install", "admission_advance")
class TestEstimateRevisionsAndLock(AdvanceCase):
    """The estimate is a RUNNING FORECAST during care and is LOCKED only once
    the doctor's Request discharge has recorded medical readiness (UAT:
    Tesema, ADM00004). Every revision is an immutable history row.

      A  pending 50k, unpaid            -> revise to 45k (test_admission_clearance)
      B  50k paid 50k, admitted         -> revise to 70k, 20k additional
      C  Cashier takes the 20k          -> 70k held, requirement cleared
      D  70k paid, revised down to 60k  -> no refund in care, 10k credit
      E  care delivered across revisions-> actual unaffected
      F  Request discharge              -> locked
      G  revision after readiness       -> refused
      H  discharged                     -> refused
      I  final settlement on actual care, not the latest estimate
      J  every revision kept
      K  no receipt, charge or revenue from a revision
    """

    def _pending(self):
        return self._draft(bed=self._priced_bed("EST %s" % uuid.uuid4().hex[:4], 0.0))

    def _history(self, admission):
        self.env.invalidate_all()
        return admission.sudo().estimate_revision_ids

    def _outstanding(self, admission):
        self.env.invalidate_all()
        return admission.sudo()._cashier_advance_outstanding()

    def _snapshot(self, admission):
        """Everything a refused revision must leave alone."""
        self.env.invalidate_all()
        record = admission.sudo()
        deposit = record._deposit_charge()
        return {
            "estimate": record.read(
                ["estimated_amount", "estimate_reason", "estimated_by_id", "estimated_at", "estimate_revision"]
            )[0],
            "history": record.estimate_revision_ids.ids,
            "deposit": (deposit.id, deposit.qty_requested, deposit.amount_received, deposit.qty_delivered),
            "receipts": record._cashier_advance_receipts().ids,
            "clearance": record._admission_financial_clearance(),
            "workflow_revision": record.workflow_revision,
        }

    def _refused(self, admission, code, amount=99000.0):
        before = self._snapshot(admission)
        with self.assertRaises(AdmissionDeskError) as caught:
            with self.env.cr.savepoint():
                self._estimate(admission, amount)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self._snapshot(admission), before)

    # -- A: in test_admission_clearance.py (a pending request on a real visit).

    # -- B / C ---------------------------------------------------------
    def test_b_c_revised_up_in_care_collects_only_the_difference(self):
        admission = self._stay(estimate=50000.0, advance=50000.0)
        self.assertEqual(admission.sudo().state, "admitted")
        self.assertFalse(admission.sudo()._estimate_additional_advance_required())

        self._estimate(admission, 70000.0, reason="Second procedure planned")      # B
        facts = self._facts(admission)
        self.assertEqual(
            (facts["advance"]["requested"], facts["advance"]["received"], facts["advance"]["outstanding"]),
            (70000.0, 50000.0, 20000.0),
        )
        self.assertEqual(facts["lane"], "advance_required")
        self.assertTrue(admission.sudo()._estimate_additional_advance_required())
        # The stay goes on: nothing ejects or blocks the admission.
        self.assertEqual(admission.sudo().state, "admitted")

        with self.assertRaises(CashierSettlementError) as caught:                 # never more
            self._advance(admission, 20000.01)
        self.assertEqual(caught.exception.code, "inpatient_advance_exceeds_estimate")
        self._advance(admission, 20000.0)                                           # C
        facts = self._facts(admission)
        self.assertEqual((facts["advance"]["received"], facts["advance"]["outstanding"]), (70000.0, 0.0))
        self.assertEqual(facts["lane"], "settled")
        self.assertFalse(admission.sudo()._estimate_additional_advance_required())
        # The first 50,000 is still its own receipt.
        self.assertEqual(
            sorted(admission.sudo()._cashier_advance_receipts().mapped("amount")), [20000.0, 50000.0]
        )

    # -- D -------------------------------------------------------------
    def test_d_revised_down_in_care_is_credit_not_refund(self):
        admission = self._stay(estimate=70000.0, advance=70000.0)
        self._estimate(admission, 60000.0, reason="Procedure cancelled")
        summary = self._summary(admission)
        self.assertEqual(summary["advance_received"], 70000.0)          # nothing given back
        self.assertEqual(summary["patient_funds"], 70000.0)
        self.assertEqual(summary["refundable_balance"], 0.0)
        self.assertEqual(summary["settlement_state"], "credit")
        self.assertEqual(summary["advance_received"] - summary["estimate_amount"], 10000.0)
        self.assertEqual(self._outstanding(admission), 0.0)
        self.assertFalse(admission.sudo()._estimate_additional_advance_required())
        with self.assertRaises(CashierSettlementError):                  # no refund in care
            self._refund(admission, 10000.0)

    # -- E -------------------------------------------------------------
    def test_e_care_is_unaffected_by_estimate_history(self):
        admission = self._stay(estimate=50000.0, advance=50000.0)
        self._care(admission, 12000.0)
        self._estimate(admission, 80000.0)
        self._care(admission, 3000.0)
        self._estimate(admission, 40000.0)
        summary = self._summary(admission)
        self.assertEqual(summary["actual_delivered"], 15000.0)
        # The care figures are the care's own: the deposit and the estimate
        # history never enter them.
        self.assertEqual(summary["estimated_or_authorized"], 15000.0)
        self.assertEqual(summary["estimate_amount"], 40000.0)
        self.assertEqual(admission.sudo().estimate_revision, 3)

    # -- F / G ---------------------------------------------------------
    def test_f_g_request_discharge_locks_it(self):
        admission = self._stay(estimate=50000.0, advance=50000.0)
        # Up to the discharge request (the review is only a screen) it is open.
        self.assertIsNone(admission.sudo()._estimate_lock())
        self._estimate(admission, 52000.0, reason="Final adjustment before discharge")
        self._ready(admission)                                                      # F
        self.assertEqual(admission.sudo()._estimate_lock(), "medically_ready")
        self.assertFalse(admission.sudo()._estimate_additional_advance_required())
        self._refused(admission, "admission_estimate_locked", amount=70000.0)       # G
        self._refused(admission, "admission_estimate_locked", amount=30000.0)
        # And the Cashier takes no further advance against it.
        with self.assertRaises(CashierSettlementError) as caught:
            self._advance(admission, 1000.0)
        self.assertEqual(caught.exception.code, "inpatient_advance_not_open")

    # -- H -------------------------------------------------------------
    def test_h_discharged_is_refused(self):
        admission = self._stay(estimate=1000.0, advance=0.0)
        self._ready(admission)
        self.env.invalidate_all()
        admission.with_user(self.receptionist)._desk_finalize_discharge(
            str(uuid.uuid4()), admission.sudo().workflow_revision
        )
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "discharged")
        self._refused(admission, "admission_invalid_state")

    # -- I -------------------------------------------------------------
    def test_i_final_settlement_is_actual_care_not_the_estimate(self):
        admission = self._stay(estimate=50000.0, advance=50000.0)
        self._estimate(admission, 70000.0)
        self._advance(admission, 20000.0)
        self._care(admission, 64000.0)
        self._ready(admission)
        summary = self._summary(admission)
        self.assertEqual(summary["estimate_amount"], 70000.0)          # planning history
        self.assertEqual(summary["actual_delivered"], 64000.0)
        self.assertEqual(summary["refundable_balance"], 6000.0)
        self.assertEqual(summary["settlement_state"], "refund_due")

    # -- J -------------------------------------------------------------
    def test_j_every_revision_is_kept_complete_and_immutable(self):
        admission = self._stay(estimate=50000.0, advance=50000.0)
        self._estimate(admission, 70000.0, reason="Second procedure planned")
        self._estimate(admission, 60000.0, user=self.manager, reason="Oversight review")
        rows = self._history(admission)
        self.assertEqual(
            [(r.revision, r.amount, r.reason, r.estimated_by_id, r.is_baseline) for r in rows],
            [
                (1, 50000.0, "Expected surgical stay", self.doctor_user, False),
                (2, 70000.0, "Second procedure planned", self.doctor_user, False),
                (3, 60000.0, "Oversight review", self.manager, False),
            ],
        )
        self.assertTrue(all(rows.mapped("estimated_at")))
        record = admission.sudo()
        self.assertEqual(
            (record.estimated_amount, record.estimate_revision, record.estimated_at),
            (60000.0, 3, rows[-1].estimated_at),
        )
        # Not editable, not deletable, not forgeable -- not even by sudo().
        with self.assertRaises(AdmissionWorkflowError):
            with self.env.cr.savepoint():
                rows[0].sudo().write({"amount": 1.0})
        with self.assertRaises(AdmissionWorkflowError):
            with self.env.cr.savepoint():
                rows[0].sudo().unlink()
        with self.assertRaises(AdmissionWorkflowError):
            with self.env.cr.savepoint():
                self.env["hospital.admission.estimate.revision"].sudo().create({
                    "admission_id": admission.id, "revision": 9, "amount": 1.0,
                })
        # The doctor reads history only through the estimate payload.
        with self.assertRaises(AccessError):
            self.env["hospital.admission.estimate.revision"].with_user(self.doctor_user).search([])
        self.assertEqual(len(self._history(admission)), 3)

    def test_j_the_estimate_facts_carry_the_history_and_no_cashier_figure(self):
        admission = self._stay(estimate=50000.0, advance=50000.0)
        self._estimate(admission, 70000.0, reason="Second procedure planned")
        facts = admission.with_user(self.doctor_user)._estimate_facts()
        self.assertEqual([row["amount"] for row in facts["history"]], [50000.0, 70000.0])
        self.assertIsNone(facts["locked_reason"])
        for forbidden in ("advance_received", "remaining", "received", "outstanding"):
            self.assertNotIn(forbidden, facts)

    # -- K -------------------------------------------------------------
    def test_k_a_revision_moves_no_money_and_recognises_nothing(self):
        admission = self._stay(estimate=50000.0, advance=50000.0, actual=5000.0)
        Receipt = self.env["hospital.charge.receipt"].sudo()
        Charge = self.env["hospital.charge.line"].sudo()
        receipts = Receipt.search_count([])
        deposits = Charge.search_count([
            ("source_model", "=", admission.STAY_SOURCE_MODEL),
            ("source_res_id", "=", admission.id),
            ("source_event", "=", "inpatient_deposit"),
        ])
        before = self._summary(admission)
        for amount in (70000.0, 30000.0, 55000.0):
            self._estimate(admission, amount)
        self.assertEqual(Receipt.search_count([]), receipts)
        self.assertEqual(deposits, 1)
        deposit = admission.sudo()._deposit_charge()
        self.assertEqual(
            Charge.search_count([
                ("source_model", "=", admission.STAY_SOURCE_MODEL),
                ("source_res_id", "=", admission.id),
                ("source_event", "=", "inpatient_deposit"),
            ]),
            1,
        )
        self.assertEqual((deposit.qty_requested, deposit.qty_delivered), (55000.0, 0.0))
        self.assertEqual(deposit.amount_received, 50000.0)
        after = self._summary(admission)
        for key in ("actual_delivered", "advance_received", "patient_funds", "advance_applied"):
            self.assertEqual(after[key], before[key], key)

    # -- Idempotency ---------------------------------------------------
    def test_a_replay_records_one_revision_even_after_the_lock(self):
        admission = self._stay(estimate=50000.0, advance=50000.0)
        token = str(uuid.uuid4())
        revision = admission.sudo().workflow_revision
        admission.with_user(self.doctor_user)._desk_set_estimate(70000.0, "Plan B", token, revision)
        _, replayed = admission.with_user(self.doctor_user)._desk_set_estimate(
            70000.0, "Plan B", token, revision
        )
        self.assertTrue(replayed)
        self.assertEqual(len(self._history(admission)), 2)
        # An unknown-outcome retry that lands after readiness is still the
        # SAME act: it replays, records nothing, and is not refused.
        self._ready(admission)
        self.env.invalidate_all()
        _, replayed = admission.with_user(self.doctor_user)._desk_set_estimate(
            70000.0, "Plan B", token, revision
        )
        self.assertTrue(replayed)
        self.assertEqual(len(self._history(admission)), 2)
        # A NEW token after readiness is a new revision, and is refused.
        self._refused(admission, "admission_estimate_locked", amount=70000.0)

    def test_a_stale_screen_cannot_revise_with_an_old_revision(self):
        admission = self._stay(estimate=50000.0, advance=0.0)
        stale = admission.sudo().workflow_revision
        self._ready(admission)                                  # bumps the workflow revision
        before = self._snapshot(admission)
        with self.assertRaises(AdmissionDeskError) as caught:
            with self.env.cr.savepoint():
                admission.with_user(self.doctor_user)._desk_set_estimate(
                    60000.0, "Late", str(uuid.uuid4()), stale
                )
        self.assertIn(caught.exception.code, ("admission_revision_conflict", "admission_estimate_locked"))
        self.assertEqual(self._snapshot(admission), before)

    # -- Refused-revision integrity -----------------------------------
    def test_refused_revisions_change_nothing(self):
        admission = self._stay(estimate=50000.0, advance=30000.0, actual=2000.0)
        self._ready(admission)
        self._refused(admission, "admission_estimate_locked", amount=80000.0)
        # Not authorised either: the cashier, the clerk and another doctor.
        for user in (self.cashier, self.receptionist, self.other_doctor_user):
            before = self._snapshot(admission)
            with self.assertRaises(AdmissionDeskError) as caught:
                with self.env.cr.savepoint():
                    self._estimate(admission, 1.0, user=user)
            self.assertEqual(caught.exception.code, "admission_not_authorized")
            self.assertEqual(self._snapshot(admission), before)


@tagged("post_install", "-at_install", "admission_advance")
class TestRefund(AdvanceCase):
    def test_q_refund_is_accounting_only_and_never_touches_revenue(self):
        admission = self._stay(actual=48000.0)
        care = admission.sudo()._billing_account().charge_line_ids.filtered(
            lambda line: line.amount_eligible > 0
        )
        eligible_before = care.mapped("amount_eligible")

        # Credit during care is not refundable yet.
        with self.assertRaises(CashierSettlementError) as caught:
            self._refund(admission, 2000.0)
        self.assertEqual(caught.exception.code, "inpatient_nothing_refundable")
        self._ready(admission)
        self.assertEqual(self._facts(admission)["lane"], "refund_due")

        # The Cashier sees it and cannot record it.
        with self.assertRaises(AccessError):
            self._refund(admission, 2000.0, user=self.cashier)
        with self.assertRaises(CashierSettlementError) as caught:
            self._refund(admission, 2000.01)
        self.assertEqual(caught.exception.code, "inpatient_refund_exceeds_credit")

        key = str(uuid.uuid4())
        self._refund(admission, 2000.0, key=key)
        _, replayed = self._refund(admission, 2000.0, key=key)
        self.assertTrue(replayed)

        summary = self._summary(admission)
        self.assertEqual(summary["settlement_state"], "even")
        self.assertEqual(summary["actual_delivered"], 48000.0)
        self.assertEqual(summary["patient_funds"], 48000.0)
        care.invalidate_recordset()
        self.assertEqual(care.mapped("amount_eligible"), eligible_before)
        self.assertEqual(sum(care.mapped("amount_invoiced")), 0.0)
        self.assertEqual(sum(care.mapped("amount_credited")), 0.0)
        deposit = admission.sudo()._deposit_charge()
        self.assertEqual(deposit.amount_refunded_from_advance, 2000.0)
        # Discharge with credit returned.
        admission.with_user(self.receptionist)._finalize_discharge()
        self.assertEqual(admission.sudo().state, "discharged")

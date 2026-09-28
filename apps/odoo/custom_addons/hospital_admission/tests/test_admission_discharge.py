"""Admissions Slice 4: medical discharge, the stay on the charge engine, and
the administrative final discharge -- at the model.

Every desk call runs inside a savepoint, the way the HTTP layer runs it, so a
refused call is checked for leaving NOTHING behind: no state, no bed, no
charge, no revision, no ledger row, no completed visit.

FINANCIAL SCENARIOS (the slice's section 34)
  A exact coverage          B underfunded, then settled     C overfunded
  D mixed payer             E transfer at different rates   F partial medication
  G ordered not delivered   H same-day stay                 I active-stay freeze
  J catalogue rate changed after the stay
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    AdmissionWorkflowError,
)
from odoo.addons.hospital_billing.models.encounter_payer import payer_identity_capability

from .test_admission_desk_transfer import DeskTransferCase

H = timedelta(hours=1)


def token():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "admission_discharge")
class DischargeCase(DeskTransferCase):
    """bed_a / bed_b / bed_c: ward fee 500, 800 per day (common fixture)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.engine = cls.env["hospital.billing.engine"].sudo()
        cls.free_ward = cls._make_ward("S4 Free", cls.department)
        cls.free_ward.sudo().write({"daily_ward_rate": 0.0, "admission_fee": 0.0})
        cls.free_room = cls._make_room(cls.free_ward, "S4-F")
        cls.free_bed = cls._make_bed(cls.free_room, "S4-F-1")
        cls.free_bed_2 = cls._make_bed(cls.free_room, "S4-F-2")
        cls.dear_ward = cls._make_ward("S4 Dear", cls.department)
        cls.dear_ward.sudo().write({"daily_ward_rate": 2000.0, "admission_fee": 0.0})
        cls.dear_room = cls._make_room(cls.dear_ward, "S4-D")
        cls.dear_bed = cls._make_bed(cls.dear_room, "S4-D-1")
        cls.med_service = cls.env["hospital.billing.service"].sudo().create({
            "name": "S4 Medicine %s" % uuid.uuid4().hex[:4],
            "service_type": "pharmacy",
            "default_price": 100.0,
            "prepayment_required": False,
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _admitted_at(self, admission, hours_ago):
        self._raw(
            "UPDATE hospital_admission SET admission_date = %s WHERE id = %s",
            (fields.Datetime.now() - hours_ago * H, admission.id),
        )
        admission.invalidate_recordset()

    def _ready(self, admission, user=None, op=None, revision=None, summary="Recovered; home on oral antibiotics."):
        user = user or self.doctor_user
        with self.env.cr.savepoint():
            return admission.with_user(user)._desk_request_medical_discharge(
                summary, op or token(),
                admission.workflow_revision if revision is None else revision,
            )

    def _finalize(self, admission, user=None, op=None, revision=None):
        user = user or self.receptionist
        with self.env.cr.savepoint():
            return admission.with_user(user)._desk_finalize_discharge(
                op or token(),
                admission.workflow_revision if revision is None else revision,
            )

    def _account(self, admission):
        return self.engine.get_or_create_billing_account(admission.encounter_id)

    def _pay(self, admission, amount):
        self._account(admission).sudo().record_operational_payment(
            amount, "cash", intake_token=uuid.uuid4().hex
        )
        self.env.invalidate_all()

    def _summary(self, admission, now=None):
        self.env.invalidate_all()
        return admission._inpatient_financial_summary(now)

    def _stay_lines(self, admission):
        return admission._stay_charges().sorted(lambda c: (c.source_event, c.source_line_id))

    def _med(self, admission, qty, price=100.0):
        charge = self.engine.create_or_update_charge(
            admission.encounter_id, "hospital.pharmacy.dispense", admission.id, "s4_med",
            "S4 medicine", source_line_id=int(uuid.uuid4().int % 10**8),
            service=self.med_service, qty_requested=qty, unit_price=price,
        )
        self.engine.activate_charge(charge)
        return charge

    def _assert_still_admitted(self, admission, bed, revision):
        self.env.invalidate_all()
        self.assertIn(admission.state, ("admitted", "transferred"))
        self.assertEqual(admission.bed_id, bed)
        self.assertEqual(bed.state, "occupied")
        self.assertEqual(bed.current_admission_id, admission)
        self.assertEqual(admission.workflow_revision, revision)
        self.assertFalse(admission.discharge_date)
        self.assertEqual(admission.encounter_id.state, "active")


# ======================================================================
# Medical discharge
# ======================================================================
@tagged("post_install", "-at_install", "admission_discharge")
class TestMedicalDischarge(DischargeCase):
    def test_the_doctor_declares_readiness_without_freeing_the_bed(self):
        admission, _, encounter = self._inpatient(self.bed_a)
        _, replayed = self._ready(admission)
        self.assertFalse(replayed)
        self.env.invalidate_all()
        self.assertTrue(admission.medical_discharge_ready)
        self.assertEqual(admission.medical_discharge_by_id, self.doctor_user)
        self.assertTrue(admission.medical_discharge_at)
        self.assertEqual(admission.discharge_summary, "Recovered; home on oral antibiotics.")
        self.assertEqual(admission.workflow_revision, 3)
        self.assertIn("medical_discharge", self._ops(admission).mapped("operation_type"))
        # Still an inpatient in a bed, on an open visit.
        self._assert_still_admitted(admission, self.bed_a, 3)
        self.assertEqual(encounter.state, "active")

    def test_only_the_responsible_doctor_or_oversight(self):
        admission, _, _ = self._inpatient(self.bed_a)
        for user in (self.other_doctor_user, self.nurse, self.receptionist, self.pharmacist,
                     self.lab_tech, self.accountant, self.cashier, self.dpo):
            with self.subTest(user=user.login):
                self.assertEqual(self._code(self._ready, admission, user=user), "admission_not_authorized")
        self.assertFalse(admission.medical_discharge_ready)
        self._ready(admission, user=self.manager)
        other, _, _ = self._inpatient(self.bed_b)
        self._ready(other, user=self.admin)
        self.assertTrue(other.medical_discharge_ready)

    def test_replay_conflict_stale_and_a_second_doctor(self):
        admission, _, _ = self._inpatient(self.bed_a)
        op = token()
        self._ready(admission, op=op, revision=2)
        _, replayed = self._ready(admission, op=op, revision=2)
        self.assertTrue(replayed)
        self.assertEqual(admission.workflow_revision, 3)
        self.assertEqual(
            self._code(self._ready, admission, op=op, revision=2, summary="Different words."),
            "admission_operation_conflict",
        )
        # A second doctor who loaded revision 2 is told it changed; one who
        # reloads is told it is already done.
        self.assertEqual(self._code(self._ready, admission, user=self.manager, revision=2),
                         "admission_revision_conflict")
        self.assertEqual(self._code(self._ready, admission, user=self.manager, revision=3),
                         "admission_invalid_state")
        self.assertEqual(
            len(self._ops(admission).filtered(lambda o: o.operation_type == "medical_discharge")), 1
        )

    def test_the_payload_is_validated(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self.assertEqual(self._code(self._ready, admission, summary="   "), "admission_invalid_payload")
        self.assertEqual(self._code(self._ready, admission, op="nope"), "admission_invalid_payload")
        self.assertEqual(self._code(self._ready, admission, revision=-1), "admission_invalid_payload")

    def test_a_draft_cannot_be_declared_ready(self):
        appointment, _ = self._visit()
        draft, _ = self._request(appointment)
        self.assertEqual(self._code(self._ready, draft.sudo()), "admission_invalid_state")

    def test_readiness_cannot_be_written_directly(self):
        admission, _, _ = self._inpatient(self.bed_a)
        for vals in ({"medical_discharge_ready": True},
                     {"medical_discharge_at": fields.Datetime.now()},
                     {"medical_discharge_by_id": self.doctor_user.id}):
            with self.subTest(vals=vals), self.assertRaises(AdmissionWorkflowError) as caught:
                admission.sudo().write(vals)
            self.assertEqual(caught.exception.code, "admission_medical_discharge_write_refused")


# ======================================================================
# The stay on the charge engine
# ======================================================================
@tagged("post_install", "-at_install", "admission_discharge")
class TestStayPosting(DischargeCase):
    def test_one_delivered_charge_per_started_period_and_one_fee(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self._admitted_at(admission, 50)                    # three started periods
        self._ready(admission)
        lines = self._stay_lines(admission)
        fee = lines.filtered(lambda c: c.source_event == "admission_fee")
        days = lines.filtered(lambda c: c.source_event == "bed_day")
        self.assertEqual(len(fee), 1)
        self.assertEqual((fee.unit_price, fee.qty_delivered), (500.0, 1.0))
        self.assertEqual(days.mapped("source_line_id"), [0, 1, 2])
        self.assertEqual(set(days.mapped("unit_price")), {800.0})
        self.assertEqual(set(days.mapped("delivery_state")), {"delivered"})
        self.assertEqual(set(days.mapped("billing_basis")), {"delivery"})
        self.assertEqual(days[0].source_key, "hospital.admission:%s:0:bed_day" % admission.id)
        self.assertEqual(
            days.service_id, self.env.ref("hospital_admission.billing_service_inpatient_bed_day")
        )
        self.assertEqual(days.billing_account_id, self._account(admission))

    def test_posting_is_idempotent_across_retries(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self._admitted_at(admission, 50)
        admission._sync_stay_charges()
        first = self._stay_lines(admission)
        admission._sync_stay_charges()
        admission._sync_stay_charges()
        self.assertEqual(self._stay_lines(admission), first)
        # A new period that STARTS later adds exactly one charge.
        admission._sync_stay_charges(fields.Datetime.now() + 24 * H)
        self.assertEqual(len(self._stay_lines(admission)), len(first) + 1)

    def test_e_a_transfer_prices_only_later_periods_and_j_the_catalogue_cannot_reprice(self):
        admission, _, _ = self._inpatient(self.bed_a)      # 800 per day
        self._admitted_at(admission, 30)                   # periods +0 and +24 started
        admission._sync_stay_charges()
        self._transfer(admission, self.dear_bed)           # 2000 per day from now
        # Periods +48 and +72 (i.e. now+18h, now+42h) start after the move.
        admission._sync_stay_charges(fields.Datetime.now() + 45 * H)
        days = self._stay_lines(admission).filtered(lambda c: c.source_event == "bed_day")
        self.assertEqual(days.mapped("unit_price"), [800.0, 800.0, 2000.0, 2000.0])

        # J: the catalogue changes after the stay; nothing posted moves.
        self.ward.sudo().write({"daily_ward_rate": 9999.0, "admission_fee": 9999.0})
        self.dear_ward.sudo().write({"daily_ward_rate": 9999.0})
        admission._sync_stay_charges(fields.Datetime.now() + 45 * H)
        days = self._stay_lines(admission).filtered(lambda c: c.source_event == "bed_day")
        self.assertEqual(days.mapped("unit_price"), [800.0, 800.0, 2000.0, 2000.0])

    def test_h_a_same_day_stay_is_one_billed_day(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self._admitted_at(admission, 3)
        self._ready(admission)
        self._pay(admission, 500.0 + 800.0)
        self._finalize(admission)
        days = self._stay_lines(admission).filtered(lambda c: c.source_event == "bed_day")
        self.assertEqual(len(days), 1)

    def test_i_the_final_segment_freezes_at_discharge(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self._admitted_at(admission, 26)                   # two started periods
        self._ready(admission)
        self._pay(admission, 500.0 + 2 * 800.0)
        self._finalize(admission)
        self.env.invalidate_all()
        posted = self._stay_lines(admission)
        self.assertEqual(len(posted.filtered(lambda c: c.source_event == "bed_day")), 2)
        # Nothing moves after the discharge, whenever it is asked.
        later = fields.Datetime.now() + 72 * H
        admission._sync_stay_charges(later)
        self.assertEqual(self._stay_lines(admission), posted)
        summary = self._summary(admission, later)
        self.assertEqual(summary["stay_unposted"], 0.0)
        self.assertEqual(summary["bed_stay"], 500.0 + 2 * 800.0)
        self.assertEqual(summary["financial_state"], "covered")

    def test_an_active_stay_accrues_before_it_is_posted_and_is_counted_once(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self._admitted_at(admission, 50)
        before = self._summary(admission)
        self.assertEqual(before["stay_unposted"], 500.0 + 3 * 800.0)
        self.assertEqual(before["actual_delivered"], 500.0 + 3 * 800.0)
        admission._sync_stay_charges()
        after = self._summary(admission)
        self.assertEqual(after["stay_unposted"], 0.0)
        self.assertEqual(after["actual_delivered"], before["actual_delivered"])


# ======================================================================
# Procedures on the charge engine (G and the double-count guard)
# ======================================================================
@tagged("post_install", "-at_install", "admission_discharge")
class TestProcedures(DischargeCase):
    def setUp(self):
        super().setUp()
        if "hospital.procedure.request" not in self.env:
            self.skipTest("hospital_procedure is not installed")
        self.kind = self.env["hospital.procedure.type"].sudo().create(
            {"name": "S4 Wound care %s" % uuid.uuid4().hex[:4], "default_price": 300.0}
        )

    def _procedure(self, admission):
        return self.env["hospital.procedure.request"].sudo().create({
            "patient_id": admission.patient_id.id,
            "admission_id": admission.id,
            "procedure_type_id": self.kind.id,
        })

    def test_g_ordered_is_not_delivered_done_counts_once_cancelled_not_at_all(self):
        admission, _, _ = self._inpatient(self.free_bed)
        ordered, done, cancelled = (self._procedure(admission) for _ in range(3))
        for procedure in (ordered, done, cancelled):
            procedure.action_submit_request()
        done.action_mark_done()
        cancelled.action_cancel()

        charges = {p: p._procedure_charge() for p in (ordered, done, cancelled)}
        self.assertEqual(charges[ordered].delivery_state, "pending")
        self.assertEqual(charges[done].delivery_state, "delivered")
        self.assertEqual(charges[cancelled].charge_state, "cancelled")
        summary = self._summary(admission)
        self.assertEqual(summary["actual_delivered"], 300.0)
        self.assertEqual(summary["procedures_actual"], 0.0, "not counted again as legacy")

        # A second, legacy bill for the same procedure is refused.
        with self.assertRaises(UserError):
            done.action_generate_procedure_bill()
        # Completing again never makes a second charge.
        done.action_mark_done()
        self.assertEqual(
            self.env["hospital.charge.line"].sudo().search_count(
                [("source_model", "=", "hospital.procedure.request"), ("source_res_id", "=", done.id)]
            ),
            1,
        )

    def test_an_unfinished_procedure_is_a_warning_not_a_block(self):
        admission, _, _ = self._inpatient(self.free_bed)
        self._procedure(admission).action_submit_request()
        self._ready(admission)
        blocking, warnings = admission._discharge_checks()
        self.assertEqual(blocking, [])
        self.assertIn("pending_procedures", warnings)
        self._finalize(admission)
        self.assertEqual(admission.state, "discharged")


# ======================================================================
# Final discharge: the financial scenarios
# ======================================================================
@tagged("post_install", "-at_install", "admission_discharge")
class TestFinalDischargeFinancials(DischargeCase):
    def _assert_discharged(self, admission, bed, encounter, revision):
        self.env.invalidate_all()
        self.assertEqual(admission.state, "discharged")
        self.assertTrue(admission.discharge_date)
        self.assertEqual(bed.state, "available")
        self.assertFalse(bed.current_admission_id)
        self.assertEqual(encounter.state, "completed")
        self.assertEqual(admission.workflow_revision, revision)
        self.assertEqual(
            len(self._ops(admission).filtered(lambda o: o.operation_type == "final_discharge")), 1
        )

    def test_a_exact_coverage_discharges(self):
        admission, _, encounter = self._inpatient(self.bed_a)
        self._admitted_at(admission, 26)                   # fee + 2 days
        self._ready(admission)
        self._pay(admission, 500.0 + 2 * 800.0)
        status = admission._inpatient_financial_status()
        self.assertEqual(status["financial_state"], "covered")
        self.assertFalse(status["settlement_required"])
        self.assertFalse(status["refund_due"])
        _, replayed = self._finalize(admission)
        self.assertFalse(replayed)
        self._assert_discharged(admission, self.bed_a, encounter, 4)

    def test_b_underfunded_is_blocked_then_settles_and_discharges(self):
        admission, _, encounter = self._inpatient(self.bed_a)
        self._admitted_at(admission, 26)
        self._ready(admission)
        status = admission._inpatient_financial_status()
        self.assertEqual(status["financial_state"], "due")
        self.assertTrue(status["settlement_required"])
        self.assertEqual(self._code(self._finalize, admission), "admission_settlement_required")
        self._assert_still_admitted(admission, self.bed_a, 3)

        self._pay(admission, 500.0 + 2 * 800.0)
        self._finalize(admission)
        self._assert_discharged(admission, self.bed_a, encounter, 4)

    def test_c_overfunded_discharges_and_preserves_the_refund(self):
        """Paid for ten tablets, four dispensed: 600 is the patient's money.
        (Cash intake refuses more than a charge's value, so over-funding is
        always an order paid in full and delivered in part.)"""
        admission, _, encounter = self._inpatient(self.free_bed)
        charge = self._med(admission, 10)
        self._pay(admission, 1000.0)
        self.engine.mark_charge_delivered(charge, qty_delivered=4)
        self._ready(admission)
        self._finalize(admission)
        self._assert_discharged(admission, self.free_bed, encounter, 4)

        summary = self._summary(admission)
        self.assertEqual(summary["refundable_balance"], 600.0)
        status = admission._inpatient_financial_status()
        self.assertEqual(status["financial_state"], "refundable")
        self.assertTrue(status["refund_due"])
        # NO automatic payout, NOTHING consumed: the cash is still held for the
        # Cashier, and no refund has been recorded.
        account = admission.encounter_id.billing_account_id.sudo()
        account.invalidate_recordset()
        self.assertEqual(account.amount_received, 1000.0)
        self.assertEqual(account.amount_refunded, 0.0)

    def test_d_mixed_payer_shares_the_stay_and_procedures(self):
        """80% of every bed-day and procedure to the sponsor, by the agreement's
        own default policy, decided per charge as it is posted."""
        company = self.env.company
        company.sudo().write({"payer_responsibility_mode": "enforce"})
        partner = self.env["res.partner"].sudo().create({"name": "S4 Payer"})
        payer = self.env["hospital.payer"].sudo().create({
            "name": "S4 Payer %s" % uuid.uuid4().hex[:4], "payer_type": "insurance",
            "partner_id": partner.id, "company_id": company.id,
        })
        today = fields.Date.context_today(payer)
        agreement = self.env["hospital.payer.agreement"].sudo().create({
            "payer_id": payer.id, "agreement_number": "S4-%s" % uuid.uuid4().hex[:6],
            "company_id": company.id, "effective_from": today - timedelta(days=30),
            "limit_scope": "unlimited", "default_coverage_policy": "default_percentage",
            "default_coverage_percent": 80.0,
        })
        agreement.action_activate()

        admission, _, encounter = self._inpatient(self.dear_bed)   # 2000 per day
        eligibility = self.env["hospital.patient.payer"].sudo().create({
            "patient_id": admission.patient_id.id, "agreement_id": agreement.id,
            "effective_from": today,
        })
        eligibility.action_activate()
        with payer_identity_capability():
            encounter.sudo().write({"patient_payer_id": eligibility.id})

        if "hospital.procedure.request" in self.env:
            kind = self.env["hospital.procedure.type"].sudo().create(
                {"name": "S4 Drain %s" % uuid.uuid4().hex[:4], "default_price": 500.0}
            )
            procedure = self.env["hospital.procedure.request"].sudo().create({
                "patient_id": admission.patient_id.id, "admission_id": admission.id,
                "procedure_type_id": kind.id,
            })
            procedure.action_submit_request()
            procedure.action_mark_done()
            procedure_value = 500.0
        else:
            procedure_value = 0.0

        self._admitted_at(admission, 26)                   # two bed-days
        self._ready(admission)
        summary = self._summary(admission)
        actual = 2 * 2000.0 + procedure_value
        self.assertEqual(summary["actual_delivered"], actual)
        self.assertEqual(summary["payer_authorized"], actual * 0.8)
        self.assertEqual(summary["patient_responsibility"], actual * 0.2)
        self.assertEqual(summary["financial_state"], "due")

        self._pay(admission, actual * 0.2)
        self._finalize(admission)
        self._assert_discharged(admission, self.dear_bed, encounter, 4)

    def test_f_partial_medication_counts_only_what_was_dispensed(self):
        admission, _, _ = self._inpatient(self.free_bed)
        charge = self._med(admission, 10)
        self.engine.mark_charge_delivered(charge, qty_delivered=4)
        self.assertEqual(self._summary(admission)["actual_delivered"], 400.0)
        self.engine.mark_charge_delivered(charge, qty_delivered=10)
        self.assertEqual(self._summary(admission)["actual_delivered"], 1000.0)

    def test_care_delivered_after_readiness_reopens_the_gate(self):
        """Settlement is judged at the final discharge, not at readiness."""
        admission, _, _ = self._inpatient(self.free_bed)
        self._ready(admission)
        self.assertEqual(admission._inpatient_financial_status()["financial_state"], "pending")
        self.engine.mark_charge_delivered(self._med(admission, 2), qty_delivered=2)
        self.assertEqual(self._code(self._finalize, admission), "admission_settlement_required")
        self._pay(admission, 200.0)
        self._finalize(admission)
        self.assertEqual(admission.state, "discharged")

    def test_a_crossed_period_after_settlement_is_billed_and_blocks_again(self):
        """The accepted bed-day policy holds to the minute of discharge: a
        period that starts while the patient still occupies the bed is billed,
        so settling early does not buy the next day."""
        admission, _, _ = self._inpatient(self.bed_a)
        self._admitted_at(admission, 23)
        self._ready(admission)
        self._pay(admission, 500.0 + 800.0)
        self._admitted_at(admission, 25)                   # a second period has started
        self.assertEqual(self._code(self._finalize, admission), "admission_settlement_required")
        # The refusal rolled its own posting back; the desk posts the stay to
        # date on its own so the cashier has the new day to collect against.
        self.assertEqual(len(self._stay_lines(admission).filtered(lambda c: c.source_event == "bed_day")), 1)
        admission.with_user(self.receptionist)._desk_post_stay_to_date()
        self.assertEqual(len(self._stay_lines(admission).filtered(lambda c: c.source_event == "bed_day")), 2)
        self.assertEqual(admission.workflow_revision, 3, "posting is not a workflow step")
        self._pay(admission, 800.0)
        self._finalize(admission)
        self.assertEqual(admission.state, "discharged")


# ======================================================================
# Final discharge: authority, idempotency, concurrency, rollback
# ======================================================================
@tagged("post_install", "-at_install", "admission_discharge")
class TestFinalDischargeAuthority(DischargeCase):
    def _ready_free(self):
        admission, appointment, encounter = self._inpatient(self.free_bed)
        self._ready(admission)
        return admission, encounter

    def test_not_medically_ready_is_refused_and_nothing_moves(self):
        admission, _, _ = self._inpatient(self.free_bed)
        self.assertEqual(self._code(self._finalize, admission), "admission_not_medically_ready")
        self._assert_still_admitted(admission, self.free_bed, 2)

    def test_only_the_clerk_manager_and_admin_finalize(self):
        admission, _ = self._ready_free()
        for user in (self.doctor_user, self.nurse, self.pharmacist, self.lab_tech,
                     self.accountant, self.cashier, self.dpo):
            with self.subTest(user=user.login):
                self.assertEqual(self._code(self._finalize, admission, user=user), "admission_not_authorized")
        self._assert_still_admitted(admission, self.free_bed, 3)
        self._finalize(admission, user=self.manager)
        self.assertEqual(admission.state, "discharged")
        other, _, _ = self._inpatient(self.bed_b)
        self._ready(other)
        self._pay(other, 500.0 + 800.0)
        self._finalize(other, user=self.admin)
        self.assertEqual(other.state, "discharged")

    def test_replay_conflict_and_two_admins(self):
        admission, _ = self._ready_free()
        op = token()
        self._finalize(admission, op=op, revision=3)
        _, replayed = self._finalize(admission, op=op, revision=3)
        self.assertTrue(replayed)
        self.assertEqual(admission.workflow_revision, 4)
        self.assertEqual(self._code(self._finalize, admission, op=op, revision=4),
                         "admission_operation_conflict")
        # The second admin loaded revision 3 as well.
        self.assertEqual(self._code(self._finalize, admission, user=self.admin, revision=3),
                         "admission_revision_conflict")
        self.assertEqual(self._code(self._finalize, admission, user=self.admin, revision=4),
                         "admission_invalid_state")

    def test_final_discharge_against_a_transfer(self):
        admission, _ = self._ready_free()
        # To another unpriced bed: this test is about the revision race, not
        # money. (A move within the admission's own second opens period 0 in
        # the destination, which a priced bed would rightly bill.)
        self._transfer(admission, self.free_bed_2)         # revision 3 -> 4
        self.assertEqual(self._code(self._finalize, admission, revision=3), "admission_revision_conflict")
        self._assert_still_admitted(admission, self.free_bed_2, 4)
        self._finalize(admission, revision=4)
        self.env.invalidate_all()
        self.assertEqual(self.free_bed_2.state, "available")
        self.assertEqual(self._code(self._transfer, admission, self.bed_b), "admission_invalid_state")

    def test_a_bed_owned_by_another_admission_is_not_released(self):
        admission, _ = self._ready_free()
        other, _, _ = self._inpatient(self.bed_b)
        self._raw("UPDATE hospital_bed SET current_admission_id = %s WHERE id = %s",
                  (other.id, self.free_bed.id))
        self.assertEqual(self._code(self._finalize, admission), "admission_integrity_error")
        self.env.invalidate_all()
        self.assertEqual(self.free_bed.current_admission_id, other)
        self.assertEqual(admission.state, "admitted")

    def test_a_visit_changed_before_finalization_is_refused(self):
        admission, encounter = self._ready_free()
        self._raw("UPDATE hospital_encounter SET state = 'checked_in' WHERE id = %s", (encounter.id,))
        self.assertEqual(self._code(self._finalize, admission), "admission_encounter_mismatch")
        self.env.invalidate_all()
        self.assertEqual(admission.state, "admitted")
        self.assertEqual(self.free_bed.state, "occupied")

    # -- rollback ------------------------------------------------------
    def _assert_rolled_back(self, admission, encounter, charges_before):
        self._assert_still_admitted(admission, self.bed_a, 3)
        self.assertEqual(self._stay_lines(admission), charges_before)
        self.assertNotIn("final_discharge", self._ops(admission).mapped("operation_type"))

    def _ready_priced(self):
        admission, _, encounter = self._inpatient(self.bed_a)
        self._admitted_at(admission, 3)
        self._ready(admission)
        self._pay(admission, 1300.0)
        return admission, encounter, self._stay_lines(admission)

    def test_failure_after_the_stay_charge_update_rolls_back(self):
        admission, encounter, before = self._ready_priced()
        self._admitted_at(admission, 26)                   # a period will be posted...
        Admission = type(self.env["hospital.admission"])
        with patch.object(Admission, "_inpatient_financial_summary", side_effect=UserError("boom")):
            with self.assertRaises(UserError):
                self._finalize(admission)
        # ...and is gone with the rest.
        self.env.invalidate_all()
        self.assertEqual(self._stay_lines(admission), before)
        self.assertEqual(admission.state, "admitted")
        self.assertEqual(self.bed_a.state, "occupied")

    def test_failure_before_bed_release_rolls_back(self):
        admission, encounter, before = self._ready_priced()
        Admission = type(self.env["hospital.admission"])
        with patch.object(Admission, "_release_bed", side_effect=UserError("boom")):
            with self.assertRaises(UserError):
                self._finalize(admission)
        self._assert_rolled_back(admission, encounter, before)

    def test_failure_after_the_bed_release_attempt_rolls_back(self):
        admission, encounter, before = self._ready_priced()
        Admission = type(self.env["hospital.admission"])
        with patch.object(Admission, "_write_state", side_effect=UserError("boom")):
            with self.assertRaises(UserError):
                self._finalize(admission)
        self._assert_rolled_back(admission, encounter, before)

    def test_failure_after_the_admission_state_update_rolls_back(self):
        admission, encounter, before = self._ready_priced()
        Encounter = type(self.env["hospital.encounter"])
        with patch.object(Encounter, "action_complete", side_effect=UserError("boom")):
            with self.assertRaises(UserError):
                self._finalize(admission)
        self._assert_rolled_back(admission, encounter, before)

    def test_an_operation_ledger_failure_rolls_back(self):
        admission, encounter, before = self._ready_priced()
        Admission = type(self.env["hospital.admission"])
        with patch.object(Admission, "_desk_record_operation", side_effect=UserError("boom")):
            with self.assertRaises(UserError):
                self._finalize(admission)
        self._assert_rolled_back(admission, encounter, before)

    # -- handoffs ------------------------------------------------------
    def test_the_ward_nurse_keeps_the_patient_until_the_final_discharge(self):
        admission, _, _ = self._inpatient(self.bed_a)
        Admission = self.env["hospital.admission"].with_user(self.nurse)
        self._admitted_at(admission, 3)
        self._ready(admission)
        census = Admission.search([("id", "=", admission.id), ("state", "in", ("admitted", "transferred"))])
        self.assertEqual(census, admission, "medically ready is still on the census")
        self._pay(admission, 1300.0)
        self._finalize(admission)
        self.assertFalse(
            Admission.search([("id", "=", admission.id), ("state", "in", ("admitted", "transferred"))])
        )
        self.assertEqual(Admission.search([("id", "=", admission.id)]).state, "discharged")

    def test_a_legacy_discharged_stay_without_a_visit_is_not_applicable(self):
        admission, _ = self._ready_free()
        self._finalize(admission)
        self._raw("UPDATE hospital_admission SET encounter_id = NULL WHERE id = %s", (admission.id,))
        status = admission._inpatient_financial_status()
        self.assertEqual(status["financial_state"], "not_applicable")

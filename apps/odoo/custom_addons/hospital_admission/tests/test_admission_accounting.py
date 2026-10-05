"""Accountant Desk: inpatient refund follow-up over the settlement authority.

The UAT shape (Tesema, ADM00004): estimate 50,000, advance 50,000, care
6,600 delivered, medically ready, DISCHARGED with 43,400 refund due. The
discharge is not blocked by a refund due; the refund survives the discharge
and the Accountant records it later.

  C  refund-due, medically ready      -> on the worklist
  D  refund-due, DISCHARGED           -> on the worklist
  E  Tesema-shaped                    -> on the worklist with 43,400
  F  detail: settlement, care, history
  G  full refund                      -> refundable 0, lane refunded
  H  partial refund                   -> rest stays due
  I  replay                           -> one refund
  J  a second attempt on spent credit -> refused, no double refund
  K-N doctor / cashier / nurse / reception cannot refund (or open the desk)
  O-R care, workflow, bed, receipts unchanged by the refund
  S  no clinical detail in the facts
  T  queryable after the visit is completed, regardless of its date
"""
import json
import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import tagged

from ..models.admission_cashier import CashierSettlementError
from .test_admission_advance_settlement import AdvanceCase


@tagged("post_install", "-at_install", "admission_accounting")
class TestAccountantDesk(AdvanceCase):

    def _discharged_refund_due(self, estimate=50000.0, advance=50000.0, care=6600.0):
        admission = self._stay(estimate=estimate, advance=advance, actual=care)
        self._ready(admission)
        self.env.invalidate_all()
        admission.with_user(self.receptionist)._desk_finalize_discharge(
            str(uuid.uuid4()), admission.sudo().workflow_revision
        )
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "discharged")
        return admission

    def _census(self, user=None, **kwargs):
        self.env.invalidate_all()
        rows, _ = self.env["hospital.admission"].with_user(user or self.accountant)._accounting_census(**kwargs)
        return {facts["admission"]["id"]: facts for facts in rows}

    def _detail(self, admission, user=None):
        self.env.invalidate_all()
        found = self.env["hospital.admission"].with_user(user or self.accountant)._accounting_find(admission.id)
        return found._accounting_detail()

    def _refund_as(self, admission, amount, user=None, key=None, reason="Unused inpatient advance"):
        found = self.env["hospital.admission"].with_user(user or self.accountant)._accounting_find(admission.id)
        result = found.with_user(user or self.accountant)._cashier_record_refund(
            amount, reason, key or str(uuid.uuid4())
        )
        self.env.invalidate_all()
        return result

    def _world(self, admission):
        """What a refund must never move."""
        self.env.invalidate_all()
        record = admission.sudo()
        summary = record._inpatient_financial_summary()
        account = record._billing_account()
        return {
            "state": record.state,
            "discharge_date": record.discharge_date,
            "encounter_state": record.encounter_id.state,
            "bed": (record.bed_id.id, record.bed_id.state, record.bed_id.current_admission_id.id),
            "actual_delivered": summary["actual_delivered"],
            "delivered_by_category": summary["delivered_by_category"],
            "estimate": (record.estimated_amount, record.estimate_revision, record.estimate_revision_ids.ids),
            "receipts": self.env["hospital.charge.receipt"].sudo().search(
                [("billing_account_id", "=", account.id)]
            ).ids,
            "charges": [
                (line.id, line.qty_delivered, line.amount_estimated)
                for line in self.env["hospital.charge.line"].sudo().search(
                    [("billing_account_id", "=", account.id)], order="id"
                )
            ],
        }

    # -- C / D / E / T ---------------------------------------------------
    def test_c_medically_ready_refund_due_is_on_the_worklist(self):
        admission = self._stay(estimate=50000.0, advance=50000.0, actual=6600.0)
        self._ready(admission)
        row = self._census()[admission.id]
        self.assertEqual(row["accounting_lane"], "refund_due")
        self.assertEqual(row["summary"]["refundable_balance"], 43400.0)

    def test_d_e_t_a_discharged_refund_stays_on_the_worklist(self):
        admission = self._discharged_refund_due()
        record = admission.sudo()
        self.assertEqual(record.encounter_id.state, "completed")
        # T: weeks later, outside any "recent" window, it is still found.
        self._raw(
            "UPDATE hospital_admission SET admission_date = %s, discharge_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=60), fields.Datetime.now() - timedelta(days=45), admission.id),
        )
        rows = self._census()
        self.assertIn(admission.id, rows)
        row = rows[admission.id]
        self.assertEqual(row["accounting_lane"], "refund_due")
        self.assertEqual(row["admission"]["state"], "discharged")
        self.assertEqual(row["summary"]["refundable_balance"], 43400.0)
        # Found by MRN / visit / reference search too.
        self.assertIn(admission.id, self._census(search=record.name))
        self.assertIn(admission.id, self._census(search=record.encounter_id.name))

    def test_the_cashier_lanes_stay_out(self):
        """Advance, due and settled stays are the Cashier's, not Finance's."""
        advance = self._stay(estimate=10000.0, advance=0.0)
        settled = self._stay(estimate=10000.0, advance=10000.0)
        rows = self._census()
        self.assertNotIn(advance.id, rows)
        self.assertNotIn(settled.id, rows)

    # -- F --------------------------------------------------------------
    def test_f_detail_carries_settlement_care_and_history(self):
        admission = self._discharged_refund_due()
        detail = self._detail(admission)
        summary = detail["summary"]
        self.assertEqual(
            (summary["estimate_amount"], summary["advance_received"], summary["actual_delivered"],
             summary["refundable_balance"], summary["financial_state"]),
            (50000.0, 50000.0, 6600.0, 43400.0, "refundable"),
        )
        self.assertEqual(detail["accounting_lane"], "refund_due")
        self.assertEqual(detail["physician"], self.doctor.name)
        self.assertEqual([row["kind"] for row in detail["payments_in"]], ["advance"])
        self.assertEqual(detail["payments_in"][0]["amount"], 50000.0)
        self.assertEqual(detail["refunds"], [])

    # -- G / O / P / Q / R ---------------------------------------------
    def test_g_o_p_q_r_full_refund_after_discharge_moves_only_money(self):
        admission = self._discharged_refund_due()
        before = self._world(admission)
        _, replayed = self._refund_as(admission, 43400.0, reason="Refund of unused inpatient advance after final inpatient settlement.")
        self.assertFalse(replayed)
        self.assertEqual(self._world(admission), before)          # O P Q R
        record = admission.sudo()
        summary = record._inpatient_financial_summary()
        self.assertEqual(summary["refundable_balance"], 0.0)
        # The patient's funds fall by exactly the refund; nothing else does.
        self.assertEqual(summary["patient_funds"], 50000.0 - 43400.0)
        detail = self._detail(admission)
        self.assertEqual(detail["accounting_lane"], "refunded")
        [refund] = detail["refunds"]
        self.assertEqual(
            (refund["amount"], refund["refundable_before"], refund["refundable_after"], refund["actor"]),
            (43400.0, 43400.0, 0.0, self.accountant.name),
        )
        self.assertEqual(refund["reason"], "Refund of unused inpatient advance after final inpatient settlement.")
        self.assertFalse(refund["accounting_posted"])
        self.assertEqual(refund["reference"], "%s/RF1" % record.name)
        # It leaves Refund Due and appears under Refunded.
        row = self._census()[admission.id]
        self.assertEqual(row["accounting_lane"], "refunded")
        self.assertNotIn(admission.id, self._census(lanes=("refund_due",)))

    # -- H --------------------------------------------------------------
    def test_h_a_partial_refund_leaves_the_rest_due(self):
        admission = self._discharged_refund_due()
        self._refund_as(admission, 10000.0)
        row = self._census()[admission.id]
        self.assertEqual(row["accounting_lane"], "refund_due")
        self.assertEqual(row["summary"]["refundable_balance"], 33400.0)
        with self.assertRaises(CashierSettlementError) as caught:
            self._refund_as(admission, 33400.01)
        self.assertEqual(caught.exception.code, "inpatient_refund_exceeds_credit")
        self._refund_as(admission, 33400.0)
        self.assertEqual(self._census()[admission.id]["accounting_lane"], "refunded")
        self.assertEqual([r["amount"] for r in self._detail(admission)["refunds"]], [10000.0, 33400.0])

    # -- I / J ----------------------------------------------------------
    def test_i_a_replay_refunds_once(self):
        admission = self._discharged_refund_due()
        key = str(uuid.uuid4())
        self._refund_as(admission, 43400.0, key=key)
        _, replayed = self._refund_as(admission, 43400.0, key=key)
        self.assertTrue(replayed)
        self.assertEqual(len(self._detail(admission)["refunds"]), 1)
        self.assertEqual(admission.sudo()._inpatient_financial_summary()["refundable_balance"], 0.0)

    def test_j_a_second_accountant_cannot_refund_the_same_credit(self):
        other = self._make_user("adm_accountant_two", ["hospital_management.group_hospital_accountant"])
        admission = self._discharged_refund_due()
        self._refund_as(admission, 43400.0, user=self.accountant)
        with self.assertRaises(CashierSettlementError) as caught:
            self._refund_as(admission, 43400.0, user=other)       # a NEW key: not a replay
        self.assertEqual(caught.exception.code, "inpatient_nothing_refundable")
        self.assertEqual(len(self._detail(admission)["refunds"]), 1)

    # -- K / L / M / N --------------------------------------------------
    def test_k_l_m_n_no_other_role_opens_the_desk_or_refunds(self):
        admission = self._discharged_refund_due()
        for user in (self.doctor_user, self.cashier, self.nurse, self.receptionist):
            self.assertFalse(self.env["hospital.admission"].with_user(user)._accounting_may_desk(), user.login)
            with self.assertRaises(AccessError):
                self._census(user=user)
            with self.assertRaises(AccessError):
                self.env["hospital.admission"].with_user(user)._accounting_find(admission.id)
            with self.assertRaises(AccessError):
                admission.with_user(user)._cashier_record_refund(1.0, "x", str(uuid.uuid4()))
        self.assertEqual(admission.sudo()._inpatient_financial_summary()["refundable_balance"], 43400.0)
        # Oversight may open it.
        for user in (self.manager,):
            self.assertTrue(self.env["hospital.admission"].with_user(user)._accounting_may_desk())

    def test_no_refund_while_in_care(self):
        admission = self._stay(estimate=50000.0, advance=50000.0, actual=6600.0)
        self.assertNotIn(admission.id, self._census())
        with self.assertRaises(CashierSettlementError):
            self._refund_as(admission, 100.0)

    # -- S --------------------------------------------------------------
    def test_s_no_clinical_detail(self):
        admission = self._discharged_refund_due()
        detail = self._detail(admission)
        text = json.dumps(detail, default=str)
        for forbidden in ("diagnosis", "discharge_summary", "medical_discharge_summary", "admission_reason", "Well; home."):
            self.assertNotIn(forbidden, text, forbidden)

    def test_the_refund_audit_row_is_structured(self):
        admission = self._discharged_refund_due()
        self._refund_as(admission, 43400.0, reason="Returned")
        log = self.env["hospital.audit.log"].sudo().search(
            [("model_name", "=", "hospital.admission"), ("record_id", "=", admission.id),
             ("description", "=like", "Refund of %")]
        )
        self.assertEqual(len(log), 1)
        data = json.loads(log.new_value)
        self.assertEqual(
            (data["kind"], data["amount"], data["refundable_before"], data["refundable_after"], data["reason"]),
            ("inpatient_refund", 43400.0, 43400.0, 0.0, "Returned"),
        )
        self.assertEqual(log.user_id, self.accountant)

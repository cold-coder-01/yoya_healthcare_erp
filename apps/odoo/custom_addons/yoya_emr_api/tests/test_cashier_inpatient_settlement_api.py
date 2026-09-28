"""Cashier inpatient settlement over real HTTP, with real users.

  A. the outpatient lanes keep their keys and shape
  B. an inpatient due from a PRIOR-DAY appointment appears
  C. ... whatever the appointment's stage
  D. ... although the generic billing-account outstanding reads zero
  E. a settled stay leaves the active lanes
  F. a refundable stay appears in refund_due
  G. non-money roles are refused every endpoint
  H/O/P. exact settlement -> Admissions reads covered -> final discharge
  I/J. partial, then the remainder
  K. more than due, L. zero, M. replay, forbidden body keys
  detail 404 for missing / non-stay admissions; no clinical keys anywhere.
"""
import json
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .test_admissions_desk_api import DETAIL
from .test_admissions_desk_discharge_api import AdmissionsDischargeCase

CASHIER = "/yoya-emr/api/v1/cashier"
CASHIER_WORKLIST = CASHIER + "/worklist"
INPATIENT = CASHIER + "/admissions/%s"
INPATIENT_PAY = INPATIENT + "/payment"

# ("reason" alone is NOT here: it is the collect verdict's own sentence --
# why the cashier may not take money -- and never clinical.)
CLINICAL_KEYS = frozenset({
    "physician", "physician_id", "doctor", "doctor_id", "diagnosis", "diagnosis_id",
    "discharge_summary", "admission_reason", "clinical_notes", "notes",
    "evaluation", "evaluation_ids",
})


def _keys(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


@tagged("post_install", "-at_install", "cashier_inpatient")
class CashierInpatientCase(AdmissionsDischargeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cashier = cls.denied["cashier"]
        cls.accountant = cls.denied["accountant"]

    def _pay(self, admission, amount, user=None, key=None, **extra):
        body = {"amount": amount, "payment_method": "cash", "idempotency_key": key or uuid.uuid4().hex}
        body.update(extra)
        return self._post(INPATIENT_PAY % admission.id, body, user or self.cashier)

    def _inpatient_rows(self, admission, user=None, **params):
        params.setdefault("q", admission.sudo().patient_id.name)
        data = self._ok(CASHIER_WORKLIST, user or self.cashier, **params)
        return [row for row in data["inpatient_settlement"] if row["admission"]["id"] == admission.id], data

    def _prior_day_due(self):
        """Admitted through the real flow, then placed on the clock: the visit
        opened five days ago, the stay began 50 hours ago (3 x 800 + 500)."""
        appointment, encounter, admission = self._inpatient(self.bed_a9)
        now = fields.Datetime.now()
        self._raw("UPDATE hospital_appointment SET appointment_date = %s WHERE id = %s",
                  (now - timedelta(days=5), appointment.id))
        self._raw("UPDATE hospital_admission SET admission_date = %s WHERE id = %s",
                  (now - timedelta(hours=50), admission.id))
        return appointment, encounter, admission

    def _remaining(self, admission):
        self.env.invalidate_all()
        return admission.sudo()._inpatient_financial_summary()["remaining_due"]


@tagged("post_install", "-at_install", "cashier_inpatient")
class TestInpatientQueue(CashierInpatientCase):
    def test_a_the_outpatient_lanes_are_unchanged(self):
        data = self._ok(CASHIER_WORKLIST, self.cashier)
        for key in ("rows", "initial_clearance", "active_service_clearance", "lane_counts",
                    "active_service_lane_counts", "truncated", "active_service_truncated"):
            self.assertIn(key, data)
        self.assertEqual(data["rows"], data["initial_clearance"])
        self.assertIn("inpatient_settlement", data)

    def test_b_c_d_a_prior_day_inpatient_due_appears(self):
        appointment, encounter, admission = self._prior_day_due()
        # D: post the stay so the account holds it -- the generic,
        # estimate-oriented account figure still reads nothing to collect.
        admission.sudo()._sync_stay_charges(fields.Datetime.now())
        account = encounter.sudo().billing_account_id
        account.invalidate_recordset()
        self.assertEqual(account.amount_patient_outstanding, 0.0)

        due = self._remaining(admission)
        self.assertEqual(due, 3 * 800.0 + 500.0)
        [row], data = self._inpatient_rows(admission)
        self.assertEqual(row["lane"], "due")
        self.assertEqual(row["source"], "inpatient_settlement")
        self.assertEqual(row["financial_state"], "due")
        self.assertEqual(row["remaining_due"], due)
        self.assertEqual(row["patient"]["name"], admission.sudo().patient_id.name)
        self.assertEqual(row["encounter"]["name"], encounter.sudo().name)
        self.assertEqual(row["admission"]["name"], admission.sudo().name)
        self.assertEqual(row["location"]["bed_code"], self.bed_a9.code)
        # B: not today's appointment, and still in the queue -- on any date.
        self.assertNotIn(appointment.id, [r["appointment_id"] for r in data["initial_clearance"]])
        [again], _ = self._inpatient_rows(admission, date="2000-01-01")
        self.assertEqual(again["remaining_due"], due)
        # C: whatever the stage says.
        stage = appointment.sudo().front_desk_stage
        self.assertNotEqual(stage, "awaiting_cashier")

    def test_e_a_settled_stay_leaves_the_active_lanes(self):
        _, _, admission = self._prior_day_due()
        response, payload = self._pay(admission, self._remaining(admission))
        self.assertEqual(response.status_code, 200, payload)
        rows, _ = self._inpatient_rows(admission)
        self.assertEqual(rows, [])
        [row], _ = self._inpatient_rows(admission, inpatient_lane="settled")
        self.assertEqual(row["lane"], "settled")

    def test_f_a_refundable_stay_is_in_refund_due(self):
        # A ward that charges nothing for the stay, so the medication is the
        # whole actual.
        self.ward_b.sudo().write({"daily_ward_rate": 0.0, "admission_fee": 0.0})
        room = self._room(self.ward_b, "F1")
        bed = self._bed(room, "F1-1")
        _, encounter, admission = self._inpatient(bed)
        engine = self.env["hospital.billing.engine"].sudo()
        service = self.env["hospital.billing.service"].sudo().create({
            "name": "Cashier F medicine %s" % uuid.uuid4().hex[:5],
            "service_type": "pharmacy", "default_price": 100.0, "prepayment_required": True,
        })
        charge = engine.create_or_update_charge(
            encounter.sudo(), "hospital.pharmacy.dispense", admission.id, "cashier_f", "F test",
            source_line_id=int(uuid.uuid4().int % 10**8), service=service,
            qty_requested=10, unit_price=100.0,
        )
        engine.activate_charge(charge)
        engine.get_or_create_billing_account(encounter.sudo()).record_operational_payment(
            1000.0, "cash", intake_token=uuid.uuid4().hex
        )
        engine.mark_charge_delivered(charge, qty_delivered=4)

        [row], _ = self._inpatient_rows(admission)
        self.assertEqual(row["lane"], "refund_due")
        self.assertEqual(row["source"], "refund_due")
        self.assertEqual(row["refundable_balance"], 600.0)
        detail = self._ok(INPATIENT % admission.id, self.cashier)
        self.assertFalse(detail["collectability"]["collectable"])
        self.assertEqual(detail["collectability"]["reason_code"], "refund_due")
        self._assert_refused(*self._pay(admission, 10.0), 409, "inpatient_nothing_due")

    def test_g_non_money_roles_are_refused(self):
        _, _, admission = self._prior_day_due()
        for user in (self.receptionist, self.nurse_a, self.doctor_user, self.denied["pharmacist"],
                     self.denied["lab"], self.denied["radiology"]):
            response, payload = self._get(CASHIER_WORKLIST, user)
            self.assertEqual(response.status_code, 403, payload)
            response, payload = self._get(INPATIENT % admission.id, user)
            self.assertEqual(response.status_code, 403, payload)
            response, payload = self._pay(admission, 100.0, user=user)
            self.assertEqual(response.status_code, 403, payload)
        self.assertEqual(self._remaining(admission), 3 * 800.0 + 500.0)

    def test_detail_404_for_missing_and_non_stay_admissions(self):
        for admission_id in (self.adm_draft_bed.id, self.adm_cancelled.id, 987654321):
            response, payload = self._get(INPATIENT % admission_id, self.cashier)
            self.assertEqual(response.status_code, 404, payload)
            self.assertEqual(payload["error"]["code"], "inpatient_not_found")

    def test_the_detail_is_cashier_safe_and_categorised(self):
        _, _, admission = self._prior_day_due()
        self._raw(
            "UPDATE hospital_admission SET admission_reason = %s, notes = %s WHERE id = %s",
            ("SECRET-CLINICAL-REASON", "SECRET-CLINICAL-REASON", admission.id),
        )
        detail = self._ok(INPATIENT % admission.id, self.cashier)
        self.assertFalse(set(_keys(detail)) & CLINICAL_KEYS)
        self.assertNotIn("SECRET-CLINICAL-REASON", json.dumps(detail))
        by = {entry["key"]: entry["amount"] for entry in detail["delivered_by_category"]}
        self.assertEqual(by["bed_stay"], 3 * 800.0 + 500.0)
        self.assertEqual(sum(by.values()), detail["financial"]["actual_delivered"])
        self.assertTrue(detail["collectability"]["collectable"])
        self.assertEqual(detail["collectability"]["max_amount"], detail["financial"]["remaining_due"])


@tagged("post_install", "-at_install", "cashier_inpatient")
class TestInpatientPayment(CashierInpatientCase):
    def test_h_o_p_exact_settlement_then_final_discharge(self):
        appointment, encounter, admission = self._prior_day_due()
        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        self._assert_refused(*self._http_finalize(admission), 409, "admission_settlement_required")
        self.assertEqual(
            self._ok(DETAIL % admission.id, self.receptionist)["admission"]["financial"]["financial_state"],
            "due",
        )

        due = self._remaining(admission)
        response, payload = self._pay(admission, due)
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]
        self.assertFalse(data["replayed"])
        self.assertEqual(data["receipt"]["amount"], due)
        self.assertEqual(data["financial"]["remaining_due"], 0.0)
        self.assertEqual(data["financial"]["financial_state"], "covered")
        self.assertEqual(data["lane"], "settled")
        self.assertFalse(data["receipt"]["accounting"]["posted"])
        self.assertFalse(set(_keys(data)) & CLINICAL_KEYS)

        # Admissions reads the SAME authority; nothing was pushed to it.
        financial = self._ok(DETAIL % admission.id, self.receptionist)["admission"]["financial"]
        self.assertEqual(financial["financial_state"], "covered")
        self.assertFalse(financial["settlement_required"])

        response, payload = self._http_finalize(admission)
        self.assertEqual(response.status_code, 200, payload)
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "discharged")
        self.assertEqual(self.bed_a9.sudo().state, "available")
        self.assertEqual(encounter.sudo().state, "completed")

    def test_i_j_partial_then_the_remainder(self):
        _, _, admission = self._prior_day_due()
        due = self._remaining(admission)
        response, payload = self._pay(admission, 1000.0)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["lane"], "part_paid")
        self.assertEqual(payload["data"]["financial"]["remaining_due"], due - 1000.0)
        [row], _ = self._inpatient_rows(admission)
        self.assertEqual(row["lane"], "part_paid")
        financial = self._ok(DETAIL % admission.id, self.receptionist)["admission"]["financial"]
        self.assertTrue(financial["settlement_required"])

        response, payload = self._pay(admission, due - 1000.0, user=self.accountant)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["financial"]["remaining_due"], 0.0)
        self.assertEqual(len(payload["data"]["settlement_receipts"]), 2)

    def test_k_l_amount_rules(self):
        _, _, admission = self._prior_day_due()
        due = self._remaining(admission)
        self._assert_refused(*self._pay(admission, due + 0.01), 400, "inpatient_payment_exceeds_due")
        response, payload = self._pay(admission, 0)
        self.assertEqual(response.status_code, 400, payload)
        self.assertEqual(payload["error"]["code"], "invalid_amount")
        response, payload = self._pay(admission, -10)
        self.assertEqual(response.status_code, 400, payload)
        self.assertEqual(self._remaining(admission), due)

    def test_m_replay_returns_the_same_receipt(self):
        _, _, admission = self._prior_day_due()
        due = self._remaining(admission)
        key = uuid.uuid4().hex
        first = self._pay(admission, 700.0, key=key)[1]["data"]
        response, payload = self._pay(admission, 700.0, key=key)
        self.assertEqual(response.status_code, 200, payload)
        self.assertTrue(payload["data"]["replayed"])
        self.assertEqual(payload["data"]["receipt"]["id"], first["receipt"]["id"])
        self.assertEqual(self._remaining(admission), due - 700.0)
        response, payload = self._pay(admission, 800.0, key=key)
        self.assertEqual(response.status_code, 409, payload)

    def test_the_browser_cannot_steer_the_balance(self):
        _, _, admission = self._prior_day_due()
        for key in ("remaining_due", "billing_account_id", "encounter_id", "financial_state",
                    "patient_responsibility", "admission_id"):
            response, payload = self._pay(admission, 100.0, **{key: 1})
            self.assertEqual(response.status_code, 400, payload)
            self.assertEqual(payload["error"]["code"], "payment_validation_failed")
        self.assertEqual(self._remaining(admission), 3 * 800.0 + 500.0)

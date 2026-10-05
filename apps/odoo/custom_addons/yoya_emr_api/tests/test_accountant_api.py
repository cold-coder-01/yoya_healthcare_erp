"""Accountant Desk over real HTTP, with real users.

The UAT shape (Tesema, ADM00004): estimate 50,000, advance 50,000, 6,600 of
care, medically ready, DISCHARGED with 43,400 refund due -- the refund
survives the discharge and Finance records it from /accountant.
"""
import json
import uuid

from odoo.tests import tagged

from .test_inpatient_advance_api import AdvanceApiCase

ACCOUNTANT = "/yoya-emr/api/v1/accountant"
SESSION = ACCOUNTANT + "/session"
WORKLIST = ACCOUNTANT + "/worklist"
ACC_DETAIL = ACCOUNTANT + "/admissions/%s"
ACC_REFUND = ACC_DETAIL + "/refund"
RECEPTION_SESSION = "/yoya-emr/api/v1/reception/session"
REASON = "Refund of unused inpatient advance after final inpatient settlement."


@tagged("post_install", "-at_install", "accountant_desk")
class TestAccountantDeskApi(AdvanceApiCase):

    def _discharged_refund_due(self):
        appointment, encounter, admission = self._free_inpatient()
        self._estimate(appointment, admission, 50000)
        self._advance(admission, 50000)
        self._care(admission, 6600)
        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        # Refund due does NOT block the discharge.
        response, payload = self._http_finalize(admission)
        self.assertEqual(response.status_code, 200, payload)
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "discharged")
        self.assertEqual(encounter.sudo().state, "completed")
        return appointment, encounter, admission

    def _refund(self, admission, amount, user=None, key=None, **extra):
        body = {"amount": amount, "reason": REASON, "idempotency_key": key or str(uuid.uuid4())}
        body.update(extra)
        return self._post(ACC_REFUND % admission.id, body, user or self.accountant)

    def _rows(self, user=None, **params):
        data = self._ok(WORKLIST, user or self.accountant, **params)
        return {row["admission"]["id"]: row for row in data["rows"]}, data

    # -- A / B ----------------------------------------------------------
    def test_a_b_the_accountant_session_and_routing_flags(self):
        data = self._ok(SESSION, self.accountant)
        self.assertEqual(data["capabilities"], {"accountant_desk": True, "record_refund": True})
        roles = self._ok(RECEPTION_SESSION, self.accountant)["roles"]
        # The narrow flag the front end routes a pure accountant to /accountant on.
        self.assertTrue(roles["accountant"])
        self.assertFalse(roles["cashier"])
        for user in (self.cashier, self.doctor_user, self.receptionist, self.nurse_a):
            caps = self._ok(SESSION, user)["capabilities"]
            self.assertEqual(caps, {"accountant_desk": False, "record_refund": False}, user.login)

    # -- D / E / T / F --------------------------------------------------
    def test_d_e_f_t_a_discharged_refund_is_listed_and_detailed(self):
        _, encounter, admission = self._discharged_refund_due()
        rows, data = self._rows()
        self.assertIn(admission.id, rows)
        row = rows[admission.id]
        self.assertEqual((row["lane"], row["lane_label"]), ("refund_due", "Refund due"))
        self.assertEqual(row["admission"]["state"], "discharged")
        self.assertEqual(row["refundable_balance"], 43400.0)
        self.assertGreaterEqual(data["counts"]["refund_due"], 1)
        refund_only, _ = self._rows(lane="refund_due")
        self.assertIn(admission.id, refund_only)

        detail = self._ok(ACC_DETAIL % admission.id, self.accountant)
        settlement = detail["settlement"]
        self.assertEqual(
            (settlement["estimate_amount"], settlement["advance_received"], settlement["actual_delivered"],
             settlement["refundable_balance"], settlement["financial_state"]),
            (50000.0, 50000.0, 6600.0, 43400.0, "refundable"),
        )
        self.assertEqual(detail["encounter"]["name"], encounter.sudo().name)
        self.assertTrue(detail["admission"]["discharge_date"])
        self.assertEqual(detail["admission"]["physician"], admission.sudo().physician_id.name)
        self.assertEqual([p["kind"] for p in detail["payments_in"]], ["advance"])
        self.assertTrue(all(p["direction"] == "in" for p in detail["payments_in"]))
        self.assertEqual(detail["refunds_out"], [])
        self.assertEqual(
            detail["refund"], {"refund_due": True, "may_record": True, "max_amount": 43400.0, "reason": None}
        )

    # -- G / O / P / Q / R / I ------------------------------------------
    def test_g_i_o_p_q_r_full_refund_after_discharge(self):
        _, encounter, admission = self._discharged_refund_due()
        record = admission.sudo()
        bed = record.bed_id
        bed_before = (bed.state, bed.current_admission_id.id)
        account = record._billing_account()
        receipts_before = self.env["hospital.charge.receipt"].sudo().search_count(
            [("billing_account_id", "=", account.id)]
        )
        key = str(uuid.uuid4())
        response, payload = self._refund(admission, 43400, key=key)
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]
        self.assertFalse(data["replayed"])
        self.assertEqual(data["settlement"]["refundable_balance"], 0.0)
        self.assertEqual(data["settlement"]["actual_delivered"], 6600.0)     # O
        self.assertEqual(data["lane"], "refunded")
        [out] = data["refunds_out"]
        self.assertEqual((out["direction"], out["amount"], out["reason"]), ("out", 43400.0, REASON))
        self.assertFalse(out["accounting_posted"])
        self.assertEqual(out["accounting_note"], "Operational refund recorded. Accounting journal posting pending.")
        self.assertEqual(data["refund"]["may_record"], False)

        # I: the same key replays and refunds nothing twice.
        response, payload = self._refund(admission, 43400, key=key)
        self.assertEqual(response.status_code, 200, payload)
        self.assertTrue(payload["data"]["replayed"])
        self.assertEqual(len(payload["data"]["refunds_out"]), 1)

        self.env.invalidate_all()
        record = admission.sudo()
        self.assertEqual(record.state, "discharged")                          # P
        self.assertEqual(encounter.sudo().state, "completed")
        self.assertEqual((bed.state, bed.current_admission_id.id), bed_before)  # Q
        self.assertEqual(
            self.env["hospital.charge.receipt"].sudo().search_count([("billing_account_id", "=", account.id)]),
            receipts_before,                                                  # R
        )
        rows, _ = self._rows(lane="refunded")
        self.assertIn(admission.id, rows)
        rows, _ = self._rows(lane="refund_due")
        self.assertNotIn(admission.id, rows)

    # -- H / J ----------------------------------------------------------
    def test_h_j_partial_then_no_double_refund(self):
        _, _, admission = self._discharged_refund_due()
        response, payload = self._refund(admission, 40000)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["settlement"]["refundable_balance"], 3400.0)
        self.assertEqual(payload["data"]["lane"], "refund_due")
        self._assert_refused(*self._refund(admission, 3400.01), 400, "inpatient_refund_exceeds_credit")
        response, payload = self._refund(admission, 3400)
        self.assertEqual(response.status_code, 200, payload)
        # A second accountant, a NEW key, the same (now spent) credit.
        self._assert_refused(*self._refund(admission, 3400), 409, "inpatient_nothing_refundable")
        detail = self._ok(ACC_DETAIL % admission.id, self.accountant)
        self.assertEqual([r["amount"] for r in detail["refunds_out"]], [40000.0, 3400.0])

    def test_the_body_is_exact_and_the_amount_is_the_servers(self):
        _, _, admission = self._discharged_refund_due()
        self._assert_refused(*self._refund(admission, 100, admission_id=admission.id), 400,
                             "refund_validation_failed")
        self._assert_refused(*self._refund(admission, 50000), 400, "inpatient_refund_exceeds_credit")
        self._assert_refused(*self._refund(admission, 100, reason=" "), 400, "inpatient_refund_reason_required")
        self.env.invalidate_all()
        self.assertEqual(admission.sudo()._inpatient_financial_summary()["refundable_balance"], 43400.0)

    # -- K / L / M / N --------------------------------------------------
    def test_k_l_m_n_other_roles_are_refused(self):
        _, _, admission = self._discharged_refund_due()
        for user in (self.doctor_user, self.cashier, self.nurse_a, self.receptionist):
            response, payload = self._get(WORKLIST, user)
            self.assertEqual(response.status_code, 403, (user.login, payload))
            response, payload = self._get(ACC_DETAIL % admission.id, user)
            self.assertEqual(response.status_code, 403, (user.login, payload))
            response, payload = self._refund(admission, 100, user=user)
            self.assertEqual(response.status_code, 403, (user.login, payload))
            self.assertEqual(payload["error"]["code"], "accountant_desk_not_authorized")
        # The Cashier's own refund route still refuses the Cashier (unchanged policy).
        response, payload = self._post(
            "/yoya-emr/api/v1/cashier/admissions/%s/refund" % admission.id,
            {"amount": 100, "reason": "x", "idempotency_key": str(uuid.uuid4())}, self.cashier,
        )
        self.assertEqual(response.status_code, 403, payload)
        self.env.invalidate_all()
        self.assertEqual(admission.sudo()._inpatient_financial_summary()["refundable_balance"], 43400.0)

    def test_oversight_may_open_the_desk(self):
        _, _, admission = self._discharged_refund_due()
        rows, _ = self._rows(user=self.manager)
        self.assertIn(admission.id, rows)

    # -- S --------------------------------------------------------------
    def test_s_no_clinical_detail_leaks(self):
        _, _, admission = self._discharged_refund_due()
        text = json.dumps(self._ok(ACC_DETAIL % admission.id, self.accountant))
        for forbidden in ("diagnosis", "summary\": \"Well", "Well; home.", "admission_reason", "discharge_summary"):
            self.assertNotIn(forbidden, text, forbidden)
        rows, _ = self._rows()
        self.assertNotIn("Well; home.", json.dumps(rows[admission.id]))

    def test_an_unknown_lane_is_400(self):
        response, payload = self._get(WORKLIST, self.accountant, lane="advance_required")
        self.assertEqual(response.status_code, 400, payload)
        self.assertEqual(payload["error"]["code"], "invalid_lane")

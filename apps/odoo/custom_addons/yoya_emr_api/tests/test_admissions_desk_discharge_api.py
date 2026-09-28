"""Admissions Slice 4: medical discharge (Doctor Desk) and final discharge
(Admissions Desk) over real HTTP, with real users.

  1. THE FLOW. Admitted -> the doctor declares readiness -> Ready for discharge
     on the census, bed still occupied -> the clerk is refused while the stay
     is unpaid -> the cashier settles -> the clerk finalizes -> bed free, visit
     completed, ward census updated, the doctor sees Discharged.
  2. ROLES. Only the admission's physician (or oversight) declares readiness;
     only the clerk, manager and admin finalize.
  3. THE CONTRACT. Exact bodies, fixed codes, replay, stale revisions.
  4. MONEY. Operational states only -- no amount in any payload.
"""
import json
import re
import uuid

from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    DESK_FINAL_DISCHARGE_GROUPS,
)

from ..services.reception_scope import ADMISSIONS_FINAL_DISCHARGE_GROUPS
from .test_admissions_desk_api import (
    AMOUNT_FREE_FLAG_KEYS,
    BEDS,
    DETAIL,
    FORBIDDEN_KEY_WORDS,
    SESSION,
    _walk_keys,
)
from .test_admissions_desk_mutations_api import VISIT
from .test_admissions_desk_transfer_api import AdmissionsTransferCase

DISCHARGE_REQUEST = VISIT + "/discharge-request"
FINALIZE = DETAIL + "/finalize-discharge"


def token():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "admissions_desk_discharge")
class AdmissionsDischargeCase(AdmissionsTransferCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # This class's own Ward A charges for the stay, so the settlement gate
        # has something to gate. (Fixture wards are created per class.)
        cls.ward_a.sudo().write({"daily_ward_rate": 800.0, "admission_fee": 500.0})

    def _http_ready(self, appointment, admission, user=None, op=None, revision=None, summary="Well; home.", **extra):
        body = {
            "operation_token": op or token(),
            "expected_revision": admission.sudo().workflow_revision if revision is None else revision,
            "summary": summary,
        }
        body.update(extra)
        return self._post(DISCHARGE_REQUEST % appointment.id, body, user or self.doctor_user)

    def _http_finalize(self, admission, user=None, op=None, revision=None, **extra):
        body = {
            "operation_token": op or token(),
            "expected_revision": admission.sudo().workflow_revision if revision is None else revision,
        }
        body.update(extra)
        return self._post(FINALIZE % admission.id, body, user or self.receptionist)

    def _settle(self, admission):
        """The cashier settles the patient's share through the EXISTING intake
        (hospital.billing.account.record_operational_payment)."""
        account = admission.sudo().encounter_id.billing_account_id
        summary = admission.sudo()._inpatient_financial_summary()
        if summary["remaining_due"] > 0:
            account.with_user(self.denied["cashier"]).record_operational_payment(
                summary["remaining_due"], "cash", intake_token=uuid.uuid4().hex
            )
        self.env.invalidate_all()

    def _assert_amount_free(self, payload):
        for key in _walk_keys(payload):
            if key in AMOUNT_FREE_FLAG_KEYS:
                continue
            self.assertFalse(set(key.lower().split("_")) & FORBIDDEN_KEY_WORDS, key)


@tagged("post_install", "-at_install", "admissions_desk_discharge")
class TestDischargeFlow(AdmissionsDischargeCase):
    def test_the_groups_agree_with_the_model(self):
        self.assertEqual(set(ADMISSIONS_FINAL_DISCHARGE_GROUPS), set(DESK_FINAL_DISCHARGE_GROUPS))

    def test_request_discharge_then_finalize_end_to_end(self):
        appointment, encounter, admission = self._inpatient(self.bed_a9)
        visit = self._ok(VISIT % appointment.id, self.doctor_user)["admission"]
        self.assertEqual(visit["status"], "admitted")
        self.assertTrue(visit["can_request_discharge"])

        # 1. The doctor declares readiness.
        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["operation"]["type"], "medical_discharge")
        block = payload["data"]["admission"]
        self.assertEqual(block["status"], "discharge_pending")
        self.assertFalse(block["can_request_discharge"])
        self._assert_amount_free(payload)

        # 2. The census: Ready for discharge, bed still occupied, still on the
        #    ward nurse's list.
        row = self._row(admission, self.receptionist)
        self.assertEqual(row["lane"], "discharge_pending")
        self.assertTrue(row["medical_discharge_ready"])
        self.assertIsNotNone(self._row(admission, self.nurse_a))
        beds = {r["id"]: r for r in self._ok(BEDS, self.receptionist)["beds"]}
        self.assertEqual(beds[self.bed_a9.id]["state"], "occupied")

        detail = self._ok(DETAIL % admission.id, self.receptionist)["admission"]
        self.assertTrue(detail["discharge"]["medical_ready"])
        self.assertEqual(detail["discharge"]["blocking"], [])
        self.assertTrue(detail["discharge"]["can_finalize_discharge"])

        # 3. The stay is unpaid: the clerk is refused, nothing moves.
        if detail["financial"]["financial_state"] == "due":
            self.assertTrue(detail["financial"]["settlement_required"])
            self._assert_refused(*self._http_finalize(admission), 409, "admission_settlement_required")
            admission.invalidate_recordset()
            self.assertEqual(admission.sudo().state, "admitted")
            self._settle(admission)

        # 4. Settled: the clerk finalizes.
        response, payload = self._http_finalize(admission)
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["operation"]["type"], "final_discharge")
        done = payload["data"]["admission"]
        self.assertEqual(done["state"], "discharged")
        self.assertEqual(done["lane"], "discharged")
        self.assertIsNone(done["discharge"])
        self._assert_amount_free(payload)

        beds = {r["id"]: r for r in self._ok(BEDS, self.receptionist)["beds"]}
        self.assertEqual(beds[self.bed_a9.id]["state"], "available")
        encounter.invalidate_recordset()
        self.assertEqual(encounter.state, "completed")
        # Off the active census, into history.
        active = self._worklist(self.nurse_a, lane="needs_review,discharge_pending,admitted,transferred")
        self.assertNotIn(admission.id, self._ids(active))
        self.assertEqual(self._row(admission, self.nurse_a)["lane"], "discharged")
        self.assertEqual(self._ok(VISIT % appointment.id, self.doctor_user)["admission"]["status"], "discharged")

    def test_an_unpaid_refusal_posts_the_stay_for_the_cashier(self):
        appointment, _, admission = self._inpatient(self.bed_a9)
        self._http_ready(appointment, admission)
        account = admission.sudo().encounter_id.billing_account_id
        before = account.charge_line_ids.filtered(lambda c: c.source_event == "bed_day")
        # A second period starts while the patient waits.
        self._raw("UPDATE hospital_admission SET admission_date = admission_date - interval '25 hours' "
                  "WHERE id = %s", (admission.id,))
        self._assert_refused(*self._http_finalize(admission), 409, "admission_settlement_required")
        account.invalidate_recordset()
        after = account.charge_line_ids.filtered(lambda c: c.source_event == "bed_day")
        self.assertGreater(len(after), len(before), "the new day is there to collect")
        self.assertEqual(admission.sudo().state, "admitted")
        self._settle(admission)
        self.assertEqual(self._http_finalize(admission)[0].status_code, 200)


@tagged("post_install", "-at_install", "admissions_desk_discharge")
class TestDischargeContract(AdmissionsDischargeCase):
    def test_who_declares_readiness(self):
        appointment, _, admission = self._inpatient(self.bed_a9)
        # Another doctor cannot reach this visit at all.
        response, payload = self._http_ready(appointment, admission, user=self.other_doctor_user)
        self.assertIn(response.status_code, (403, 404), payload)
        for user in (self.nurse_a, self.receptionist, *self.denied.values()):
            with self.subTest(user=user.login):
                response, payload = self._http_ready(appointment, admission, user=user)
                self.assertEqual(response.status_code, 403, payload)
        admission.invalidate_recordset()
        self.assertFalse(admission.sudo().medical_discharge_ready)

    def test_who_finalizes_and_the_gate_precedes_existence(self):
        appointment, _, admission = self._inpatient(self.bed_a9)
        self._http_ready(appointment, admission)
        self._settle(admission)
        for user in (self.doctor_user, self.nurse_a, self.front_desk, *self.denied.values()):
            with self.subTest(user=user.login):
                self._assert_refused(*self._http_finalize(admission, user=user), 403, "admission_not_authorized")
                response, _ = self._post(FINALIZE % 999999999,
                                         {"operation_token": token(), "expected_revision": 0}, user)
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self._http_finalize(admission, user=self.manager)[0].status_code, 200)

    def test_not_ready_replay_and_stale(self):
        appointment, _, admission = self._inpatient(self.bed_a9)
        self._assert_refused(*self._http_finalize(admission), 409, "admission_not_medically_ready")
        op = token()
        self.assertEqual(self._http_ready(appointment, admission, op=op, revision=2)[0].status_code, 200)
        response, payload = self._http_ready(appointment, admission, op=op, revision=2)
        self.assertTrue(payload["data"]["operation"]["replayed"])
        self._assert_refused(*self._http_ready(appointment, admission, revision=2), 409,
                             "admission_revision_conflict")
        self._settle(admission)
        op = token()
        self.assertEqual(self._http_finalize(admission, op=op, revision=3)[0].status_code, 200)
        response, payload = self._http_finalize(admission, op=op, revision=3)
        self.assertEqual(response.status_code, 200, payload)
        self.assertTrue(payload["data"]["operation"]["replayed"])
        self.assertEqual(payload["data"]["workflow_revision"], 4)
        self._assert_refused(*self._http_finalize(admission, revision=4), 409, "admission_invalid_state")

    def test_bodies_are_exact(self):
        appointment, _, admission = self._inpatient(self.bed_a9)
        for extra in ({"bed_id": 1}, {"summary": "   "}):
            with self.subTest(extra=extra):
                self._assert_refused(*self._http_ready(appointment, admission, **extra), 400,
                                     "admission_invalid_payload")
        self._assert_refused(*self._http_finalize(admission, discharge_date="2020-01-01"), 400,
                             "admission_invalid_payload")

    def test_session_capabilities(self):
        for user in (self.receptionist, self.manager, self.sysadmin):
            self.assertIs(self._ok(SESSION, user)["capabilities"]["discharge"], True, user.login)
        for user in (self.doctor_user, self.nurse_a, self.front_desk):
            self.assertIs(self._ok(SESSION, user)["capabilities"]["discharge"], False, user.login)

    def test_no_discharge_payload_carries_a_number(self):
        appointment, _, admission = self._inpatient(self.bed_a9)
        self._http_ready(appointment, admission)
        for user in (self.receptionist, self.nurse_a, self.doctor_user):
            with self.subTest(user=user.login):
                data = self._ok(DETAIL % admission.id, user)
                self._assert_amount_free(data)
                block = data["admission"]["discharge"]
                self.assertIsNone(re.search(r"\d", json.dumps(
                    # A timestamp and a person's name are not figures; the
                    # fixture's names carry hex digits from the test token.
                    {k: v for k, v in block.items() if k not in ("medical_ready_at", "medical_ready_by")}
                )))
        # The ADMISSION block only: a Doctor Desk visit legitimately carries
        # clinical vitals (heart_rate, respiratory_rate), which are
        # measurements, not money.
        visit = self._ok(VISIT % appointment.id, self.doctor_user)
        self._assert_amount_free(visit["admission"])

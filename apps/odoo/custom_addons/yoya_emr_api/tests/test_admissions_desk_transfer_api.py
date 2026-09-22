"""Admissions Slice 3: transfer and cancel-request over real HTTP, with real
users, plus the amount-free financial state on the detail payload.

  1. THE FLOW. Admitted on ward A -> transferred to ward B: the old bed frees,
     the new one fills, the ward A nurse loses the patient and the ward B
     nurse gains the SAME admission, and the doctor sees where they are now.
  2. ROLES. Only the admissions clerk, manager and admin transfer; doctors
     cancel only their own drafts; everyone else is refused before a row is
     read.
  3. THE CONTRACT. Exact bodies, fixed codes, replay, stale revisions.
  4. MONEY. The financial state crosses as a key and booleans. No amount.
"""
import json
import re
import uuid

from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    DESK_CANCEL_REQUEST_GROUPS,
    DESK_TRANSFER_GROUPS,
    G_DOCTOR,
)

from ..services.reception_scope import (
    ADMISSIONS_CANCEL_REQUEST_GROUPS,
    ADMISSIONS_TRANSFER_GROUPS,
)
from .test_admissions_desk_api import (
    AMOUNT_FREE_FLAG_KEYS,
    BEDS,
    DETAIL,
    FORBIDDEN_KEY_WORDS,
    _walk_keys,
)
from .test_admissions_desk_mutations_api import VISIT, AdmissionsMutationCase

TRANSFER = DETAIL + "/transfer"
CANCEL = DETAIL + "/cancel-request"
FINANCIAL_KEYS = {"financial_state", "billing_blocked", "settlement_required", "refund_due", "review_reasons"}


def token():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "admissions_desk_transfer")
class AdmissionsTransferCase(AdmissionsMutationCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bed_a9 = cls._bed(cls.room_a, "A1-9")
        cls.bed_b9 = cls._bed(cls.room_b, "B1-9")
        cls.bed_b8 = cls._bed(cls.room_b, "B1-8")

    def _inpatient(self, bed=None):
        appointment, encounter, admission = self._requested()
        response, payload = self._http_admit(admission, bed or self.bed_a9)
        self.assertEqual(response.status_code, 200, payload)
        admission.invalidate_recordset()
        return appointment, encounter, admission

    def _http_transfer(self, admission, bed, user=None, op=None, revision=None, reason="Needs isolation", **extra):
        body = {
            "operation_token": op or token(),
            "expected_revision": admission.sudo().workflow_revision if revision is None else revision,
            "bed_id": bed.id if bed else None,
            "reason": reason,
        }
        body.update(extra)
        return self._post(TRANSFER % admission.id, body, user or self.receptionist)

    def _http_cancel(self, admission, user=None, op=None, revision=None, **extra):
        body = {
            "operation_token": op or token(),
            "expected_revision": admission.sudo().workflow_revision if revision is None else revision,
        }
        body.update(extra)
        return self._post(CANCEL % admission.id, body, user or self.receptionist)

    def _row(self, admission, user):
        rows = [r for r in self._worklist(user)["rows"] if r["id"] == admission.id]
        return rows[0] if rows else None

    def _assert_amount_free(self, payload):
        for key in _walk_keys(payload):
            if key in AMOUNT_FREE_FLAG_KEYS:
                continue
            self.assertFalse(set(key.lower().split("_")) & FORBIDDEN_KEY_WORDS, key)


@tagged("post_install", "-at_install", "admissions_desk_transfer")
class TestTransferFlow(AdmissionsTransferCase):
    def test_the_groups_agree_with_the_model(self):
        self.assertEqual(set(ADMISSIONS_TRANSFER_GROUPS), set(DESK_TRANSFER_GROUPS))
        # The API's cancel gate is the model's groups plus the doctor, whose
        # ownership the model decides per record.
        self.assertEqual(set(ADMISSIONS_CANCEL_REQUEST_GROUPS), set(DESK_CANCEL_REQUEST_GROUPS) | {G_DOCTOR})

    def test_transfer_moves_the_patient_between_wards_end_to_end(self):
        appointment, encounter, admission = self._inpatient(self.bed_a9)
        self.assertIsNotNone(self._row(admission, self.nurse_a))
        self.assertIsNone(self._row(admission, self.nurse_b))
        self.assertTrue(self._ok(DETAIL % admission.id, self.receptionist)["admission"]["can_transfer"])

        response, payload = self._http_transfer(admission, self.bed_b9)
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]
        self.assertEqual(data["operation"]["type"], "transfer")
        self.assertFalse(data["operation"]["replayed"])
        self.assertEqual(data["workflow_revision"], 3)
        moved = data["admission"]
        self.assertEqual(moved["state"], "transferred")
        self.assertEqual(moved["lane"], "transferred")
        self.assertEqual(moved["location"]["ward"]["id"], self.ward_b.id)
        self.assertEqual(moved["location"]["bed"]["id"], self.bed_b9.id)
        self.assertEqual(len(moved["transfers"]), 1)
        self.assertEqual(moved["transfers"][0]["from"]["bed"]["id"], self.bed_a9.id)
        self.assertEqual(moved["transfers"][0]["to"]["bed"]["id"], self.bed_b9.id)
        self.assertEqual(moved["transfers"][0]["reason"], "Needs isolation")
        self._assert_amount_free(payload)

        # Bed board: the old bed is free, the new one occupied by the SAME stay.
        beds = {row["id"]: row for row in self._ok(BEDS, self.receptionist)["beds"]}
        self.assertEqual(beds[self.bed_a9.id]["state"], "available")
        self.assertEqual(beds[self.bed_b9.id]["state"], "occupied")
        self.assertEqual(beds[self.bed_b9.id]["admission"]["id"], admission.id)

        # Ward nurses: A loses the patient, B gains the same admission.
        self.assertIsNone(self._row(admission, self.nurse_a))
        row_b = self._row(admission, self.nurse_b)
        self.assertEqual(row_b["id"], admission.id)
        self.assertFalse(row_b["can_transfer"])

        # The doctor sees a transferred inpatient, at the current bed, and
        # cannot move them.
        visit = self._ok(VISIT % appointment.id, self.doctor_user)["admission"]
        self.assertEqual(visit["status"], "transferred")
        self.assertEqual(visit["admission"]["location"]["bed"]["id"], self.bed_b9.id)
        self.assertFalse(visit["can_cancel_request"])

        # One admission, one episode.
        self.assertEqual(
            self.env["hospital.admission"].sudo().search_count(
                [("patient_id", "=", admission.patient_id.id), ("state", "in", ("admitted", "transferred"))]
            ),
            1,
        )
        self.assertEqual(admission.sudo().encounter_id, encounter)

    def test_only_the_clerk_manager_and_admin_transfer_and_the_gate_precedes_existence(self):
        _, _, admission = self._inpatient(self.bed_a9)
        refused = (self.doctor_user, self.nurse_a, self.front_desk, *self.denied.values())
        for user in refused:
            with self.subTest(user=user.login):
                self._assert_refused(*self._http_transfer(admission, self.bed_b9, user=user), 403, "admission_not_authorized")
                response, _ = self._post(
                    TRANSFER % 999999999,
                    {"operation_token": token(), "expected_revision": 0, "bed_id": self.bed_b9.id, "reason": "x"},
                    user,
                )
                self.assertEqual(response.status_code, 403)
        admission.invalidate_recordset()
        self.assertEqual(admission.sudo().bed_id, self.bed_a9)
        for user, bed in ((self.manager, self.bed_b9), (self.sysadmin, self.bed_b8)):
            with self.subTest(user=user.login):
                response, payload = self._http_transfer(admission, bed, user=user)
                self.assertEqual(response.status_code, 200, payload)

    def test_the_body_is_exactly_token_revision_bed_and_reason(self):
        _, _, admission = self._inpatient(self.bed_a9)
        bodies = (
            {},
            {"operation_token": token(), "expected_revision": 2, "bed_id": self.bed_b9.id},
            {"operation_token": token(), "expected_revision": 2, "bed_id": self.bed_b9.id, "reason": "x", "ward_id": 1},
            {"operation_token": token(), "expected_revision": "2", "bed_id": self.bed_b9.id, "reason": "x"},
            {"operation_token": token(), "expected_revision": 2, "bed_id": self.bed_b9.id, "reason": "   "},
        )
        for body in bodies:
            with self.subTest(body=body):
                self._assert_refused(*self._post(TRANSFER % admission.id, body, self.receptionist), 400, "admission_invalid_payload")
        self._assert_refused(*self._http_transfer(admission, None), 400, "admission_bed_required")
        admission.invalidate_recordset()
        self.assertEqual(admission.sudo().bed_id, self.bed_a9)

    def test_replay_conflict_and_stale_revision(self):
        _, _, admission = self._inpatient(self.bed_a9)
        op = token()
        self.assertEqual(self._http_transfer(admission, self.bed_b9, op=op, revision=2)[0].status_code, 200)
        response, payload = self._http_transfer(admission, self.bed_b9, op=op, revision=2)
        self.assertEqual(response.status_code, 200, payload)
        self.assertTrue(payload["data"]["operation"]["replayed"])
        self.assertEqual(payload["data"]["workflow_revision"], 3)
        self._assert_refused(
            *self._http_transfer(admission, self.bed_b8, op=op, revision=2), 409, "admission_operation_conflict"
        )
        self._assert_refused(*self._http_transfer(admission, self.bed_b8, revision=2), 409, "admission_revision_conflict")

    def test_occupied_and_out_of_service_beds_are_refused(self):
        _, _, admission = self._inpatient(self.bed_a9)
        self._assert_refused(*self._http_transfer(admission, self.bed_a1), 409, "admission_bed_conflict")
        self._assert_refused(*self._http_transfer(admission, self.bed_clean), 409, "admission_bed_unavailable")
        self._assert_refused(*self._http_transfer(admission, self.bed_off), 409, "admission_bed_unavailable")
        self._assert_refused(*self._http_transfer(admission, self.bed_a9), 409, "admission_bed_unavailable")
        self.assertEqual(admission.sudo().workflow_revision, 2)

    def test_a_draft_is_not_transferable(self):
        _, _, admission = self._requested()
        row = self._row(admission, self.receptionist)
        self.assertFalse(row["can_transfer"])
        self._assert_refused(*self._http_transfer(admission, self.bed_b9), 409, "admission_invalid_state")


@tagged("post_install", "-at_install", "admissions_desk_transfer")
class TestCancelRequest(AdmissionsTransferCase):
    def test_the_doctor_cancels_their_own_request_and_the_visit_is_reconciled(self):
        appointment, encounter, admission = self._requested()
        visit = self._ok(VISIT % appointment.id, self.doctor_user)["admission"]
        self.assertTrue(visit["can_cancel_request"])
        self.assertEqual(visit["admission"]["workflow_revision"], 1)
        self.assertTrue(self._row(admission, self.doctor_user)["can_cancel_request"])

        # The doctor finishes the consultation; completion is deferred.
        appointment.with_user(self.doctor_user).action_done()
        encounter.invalidate_recordset()
        self.assertEqual(encounter.state, "active")

        response, payload = self._http_cancel(admission, user=self.doctor_user)
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]
        self.assertEqual(data["operation"]["type"], "cancel_request")
        self.assertEqual(data["admission"]["state"], "cancelled")
        self.assertEqual(data["doctor_admission"]["status"], "cancelled")
        self.assertFalse(data["doctor_admission"]["can_cancel_request"])
        self._assert_amount_free(payload)

        encounter.invalidate_recordset()
        self.assertEqual(encounter.state, "completed", "the deferred completion was released")

    def test_the_clerk_and_oversight_may_cancel_any_request(self):
        for user in (self.receptionist, self.manager, self.sysadmin):
            with self.subTest(user=user.login):
                _, _, admission = self._requested()
                self.assertTrue(self._row(admission, user)["can_cancel_request"])
                response, payload = self._http_cancel(admission, user=user)
                self.assertEqual(response.status_code, 200, payload)
                self.assertEqual(payload["data"]["admission"]["state"], "cancelled")

    def test_who_may_not_cancel(self):
        _, _, admission = self._requested()
        for user in (self.nurse_a, self.front_desk, *self.denied.values()):
            with self.subTest(user=user.login):
                self._assert_refused(*self._http_cancel(admission, user=user), 403, "admission_not_authorized")
        # Another doctor cannot even see the request: hidden and missing are
        # the same answer.
        self._assert_refused(*self._http_cancel(admission, user=self.other_doctor_user), 404, "admission_not_found")
        admission.invalidate_recordset()
        self.assertEqual(admission.sudo().state, "draft")

    def test_after_admission_there_is_nothing_to_cancel(self):
        _, _, admission = self._inpatient(self.bed_a9)
        self.assertFalse(self._row(admission, self.receptionist)["can_cancel_request"])
        self._assert_refused(*self._http_cancel(admission), 409, "admission_invalid_state")
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().bed_id, self.bed_a9)
        self.assertEqual(self.bed_a9.state, "occupied")

    def test_the_body_replay_and_revision(self):
        _, _, admission = self._requested()
        self._assert_refused(*self._http_cancel(admission, extra_key=1), 400, "admission_invalid_payload")
        self._assert_refused(*self._http_cancel(admission, revision=0), 409, "admission_revision_conflict")
        op = token()
        self.assertEqual(self._http_cancel(admission, op=op, revision=1)[0].status_code, 200)
        response, payload = self._http_cancel(admission, op=op, revision=1)
        self.assertEqual(response.status_code, 200, payload)
        self.assertTrue(payload["data"]["operation"]["replayed"])
        self.assertEqual(payload["data"]["workflow_revision"], 2)


@tagged("post_install", "-at_install", "admissions_desk_transfer")
class TestFinancialState(AdmissionsTransferCase):
    def test_the_detail_carries_the_financial_state_and_no_amount(self):
        _, _, admission = self._inpatient(self.bed_a9)
        for user in (self.receptionist, self.nurse_a, self.doctor_user, self.manager):
            with self.subTest(user=user.login):
                data = self._ok(DETAIL % admission.id, user)
                financial = data["admission"]["financial"]
                self.assertEqual(set(financial), FINANCIAL_KEYS)
                self.assertIn(
                    financial["financial_state"],
                    ("covered", "due", "refundable", "pending", "not_applicable", "needs_review"),
                )
                for flag in ("billing_blocked", "settlement_required", "refund_due"):
                    self.assertIsInstance(financial[flag], bool, flag)
                for reason in financial["review_reasons"]:
                    self.assertEqual(set(reason), {"code", "message"})
                self._assert_amount_free(data)
                self.assertIsNone(re.search(r"\d", json.dumps(financial)), "no number of any kind")

    def test_a_draft_is_not_applicable(self):
        _, _, admission = self._requested()
        financial = self._ok(DETAIL % admission.id, self.receptionist)["admission"]["financial"]
        self.assertEqual(financial["financial_state"], "not_applicable")
        self.assertFalse(financial["billing_blocked"])

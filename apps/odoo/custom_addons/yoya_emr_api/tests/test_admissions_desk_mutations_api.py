"""Admissions Slice 2: the admission request (Doctor Desk) and admit (Admissions
Desk) routes, over real HTTP with real users.

  1. THE FLOW. Doctor requests -> the draft appears in Awaiting bed -> the clerk
     admits into a bed -> the visit is inpatient, the bed occupied, the doctor
     and the ward nurse see it from the SAME records.
  2. ROLES. Only the visit's own doctor (or oversight) requests; only the
     admissions clerk / manager / admin admits. Everyone else gets 403 before
     any row is read.
  3. THE CONTRACT. Fixed codes and fixed sentences; idempotent replay; stale
     revisions refused; malformed bodies refused; nothing leaks.
"""
import json
import uuid

from odoo import fields
from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    DESK_ADMIT_GROUPS,
    DESK_ERROR_MESSAGES,
)

from ..services.reception_scope import ADMISSIONS_ADMIT_GROUPS
from .test_admissions_desk_api import BEDS, DETAIL, WORKLIST, AdmissionsDeskCase

VISIT = "/yoya-emr/api/v1/doctor/visits/%s"
REQUEST = VISIT + "/admission-request"
ADMIT = DETAIL + "/admit"


def token():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "admissions_desk_mutations")
class AdmissionsMutationCase(AdmissionsDeskCase):
    def _visit(self, doctor=None):
        doctor = doctor or self.doctor
        patient = self._patient()
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": "confirmed",
        })
        encounter = self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id,
            "appointment_id": appointment.id,
            "primary_doctor_id": doctor.id,
            "company_id": self.company.id,
        })
        encounter.write({"state": "active"})
        return appointment, encounter

    def _post(self, url, body, user):
        self.env.flush_all()
        self.authenticate(user.login, self.PASSWORD)
        response = self.url_open(url, data=json.dumps(body), headers={"Content-Type": "application/json"})
        return response, json.loads(response.text)

    def _request(self, appointment, user=None, reason="Needs IV antibiotics", op=None):
        return self._post(
            REQUEST % appointment.id,
            {"operation_token": op or token(), "reason": reason},
            user or self.doctor_user,
        )

    def _clear_for_admission(self, admission):
        """Advance slice: a self-pay request needs the estimate and the full
        advance -- or an authorized emergency bypass -- before a bed. Suites
        that are NOT about admission money clear the request through the
        existing emergency route, authorized by a Hospital Manager with a
        reason, so their figures stay exactly as they were. The gate itself is
        exercised through the real estimate -> advance flow in
        test_admission_clearance_api."""
        admission = admission.sudo()
        if admission._admission_financial_clearance()["cleared"]:
            return
        admission.encounter_id.with_user(self.manager).write({
            "emergency_bypass": True,
            "emergency_bypass_reason": "Test fixture: admission money is not under test here.",
        })

    def _http_admit(self, admission, bed, user=None, op=None, revision=None, bed_id="__bed__", clear=True):
        if clear and admission.sudo().state == "draft" and admission.sudo().encounter_id:
            self._clear_for_admission(admission)
        body = {
            "operation_token": op or token(),
            "expected_revision": admission.sudo().workflow_revision if revision is None else revision,
            "bed_id": bed.id if bed_id == "__bed__" else bed_id,
        }
        return self._post(ADMIT % admission.id, body, user or self.receptionist)

    def _requested(self):
        appointment, encounter = self._visit()
        response, payload = self._request(appointment)
        self.assertEqual(response.status_code, 200, payload)
        admission = self.env["hospital.admission"].sudo().browse(
            payload["data"]["admission"]["admission"]["id"]
        )
        return appointment, encounter, admission

    def _assert_refused(self, response, payload, status, code):
        self.assertEqual(response.status_code, status, payload)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], code)
        if code in DESK_ERROR_MESSAGES:
            self.assertEqual(payload["error"]["message"], DESK_ERROR_MESSAGES[code])
        self.assertNotIn("Traceback", json.dumps(payload))


@tagged("post_install", "-at_install", "admissions_desk_mutations")
class TestTheFlow(AdmissionsMutationCase):
    def test_doctor_request_then_desk_admit_end_to_end(self):
        appointment, encounter = self._visit()

        # The doctor sees nothing yet, and may request.
        before = self._ok(VISIT % appointment.id, self.doctor_user)["admission"]
        self.assertEqual(before["status"], "none")
        self.assertTrue(before["can_request"])

        response, payload = self._request(appointment)
        self.assertEqual(response.status_code, 200, payload)
        self.assertFalse(payload["data"]["operation"]["replayed"])
        block = payload["data"]["admission"]
        self.assertEqual(block["status"], "requested")
        self.assertFalse(block["can_request"])
        admission = self.env["hospital.admission"].sudo().browse(block["admission"]["id"])
        self.assertEqual(admission.state, "draft")
        self.assertEqual(admission.encounter_id, encounter)

        # The desk sees it in Awaiting bed, admittable, at revision 1.
        rows = {r["id"]: r for r in self._worklist(self.receptionist, q=admission.name)["rows"]}
        row = rows[admission.id]
        self.assertEqual(row["lane"], "awaiting_bed")
        # Advance slice: a self-pay request is not admittable until cleared.
        self.assertFalse(row["can_admit"])
        self.assertEqual(row["admission_clearance"]["state"], "awaiting_estimate")
        self._clear_for_admission(admission)
        [row] = [r for r in self._worklist(self.receptionist)["rows"] if r["id"] == admission.id]
        self.assertTrue(row["can_admit"])
        self.assertEqual(row["workflow_revision"], 1)

        # The clerk admits.
        response, payload = self._http_admit(admission, self.bed_a2)
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]
        self.assertEqual(data["admission"]["lane"], "admitted")
        self.assertEqual(data["workflow_revision"], 2)
        self.assertFalse(data["admission"]["can_admit"])
        self.assertEqual(data["admission"]["location"]["bed"]["id"], self.bed_a2.id)
        self.env.invalidate_all()
        self.assertEqual(self.bed_a2.state, "occupied")
        self.assertEqual(self.bed_a2.current_admission_id, admission)
        self.assertEqual(encounter.encounter_type, "inpatient")

        # DOCTOR HANDOFF: inpatient, with the location.
        after = self._ok(VISIT % appointment.id, self.doctor_user)["admission"]
        self.assertEqual(after["status"], "admitted")
        self.assertEqual(after["admission"]["reference"], admission.name)
        self.assertEqual(after["admission"]["location"]["bed"]["id"], self.bed_a2.id)
        self.assertFalse(after["can_request"])

        # WARD NURSE HANDOFF: the same records, on their ward, no mutation.
        nurse_rows = {r["id"]: r for r in self._worklist(self.nurse_a, q=admission.name)["rows"]}
        self.assertEqual(nurse_rows[admission.id]["lane"], "admitted")
        self.assertFalse(nurse_rows[admission.id]["can_admit"])
        board = self._beds(self.nurse_a, ward_id=self.ward_a.id)
        cell = self._bed_row(board, self.bed_a2)
        self.assertTrue(cell["can_view_admission"])
        self.assertEqual(cell["admission"]["id"], admission.id)
        self.assertIsNotNone(cell["admission"]["length_of_stay"])
        # ...and the nurse on another ward does not see the patient.
        other = self._worklist(self.nurse_b, q=admission.name)["rows"]
        self.assertFalse(other)

    def test_completing_the_consultation_keeps_the_episode_admittable(self):
        appointment, encounter, admission = self._requested()
        encounter.sudo().action_complete()      # what completing the visit runs
        self.assertEqual(encounter.state, "active")
        response, payload = self._http_admit(admission, self.bed_a2)
        self.assertEqual(response.status_code, 200, payload)


@tagged("post_install", "-at_install", "admissions_desk_mutations")
class TestRequestContract(AdmissionsMutationCase):
    def test_replay_and_conflicts(self):
        appointment, _ = self._visit()
        op = token()
        first_response, first = self._request(appointment, op=op)
        self.assertEqual(first_response.status_code, 200)
        again_response, again = self._request(appointment, op=op)
        self.assertEqual(again_response.status_code, 200)
        self.assertTrue(again["data"]["operation"]["replayed"])
        self.assertEqual(
            again["data"]["admission"]["admission"]["id"],
            first["data"]["admission"]["admission"]["id"],
        )
        self._assert_refused(*self._request(appointment, op=op, reason="Other"), 409, "admission_operation_conflict")
        self._assert_refused(*self._request(appointment), 409, "admission_active_conflict")

    def test_only_the_visits_doctor_requests(self):
        appointment, _ = self._visit()
        # Another doctor's visit is outside the Doctor Desk's scope: its own
        # scoping answers (404, indistinguishable from a missing visit), before
        # the admission authority -- which would refuse too -- is ever reached.
        response, payload = self._request(appointment, user=self.other_doctor_user)
        self.assertIn(response.status_code, (403, 404), payload)
        for user in (self.nurse_a, self.receptionist, *self.denied.values()):
            with self.subTest(user=user.login):
                response, payload = self._request(appointment, user=user)
                self.assertEqual(response.status_code, 403, payload)
        self.assertFalse(
            self.env["hospital.admission"].sudo().search([("appointment_id", "=", appointment.id)])
        )

    def test_the_body_is_exactly_token_and_reason(self):
        appointment, _ = self._visit()
        for body in ({}, {"operation_token": token()}, {"reason": "x"},
                     {"operation_token": token(), "reason": "x", "bed_id": self.bed_a2.id},
                     {"operation_token": token(), "reason": "   "},
                     {"operation_token": "nope", "reason": "x"}):
            with self.subTest(body=body):
                self._assert_refused(
                    *self._post(REQUEST % appointment.id, body, self.doctor_user),
                    400, "admission_invalid_payload",
                )

    def test_a_closed_visit_cannot_request(self):
        appointment, encounter = self._visit()
        encounter.sudo().write({"state": "completed"})
        self._assert_refused(*self._request(appointment), 422, "admission_encounter_required")
        detail = self._ok(VISIT % appointment.id, self.doctor_user)["admission"]
        self.assertFalse(detail["can_request"])
        self.assertEqual(detail["request_blocked_reason"], "visit_not_open")


@tagged("post_install", "-at_install", "admissions_desk_mutations")
class TestAdmitContract(AdmissionsMutationCase):
    def test_the_admitting_groups_agree_with_the_model(self):
        self.assertEqual(set(ADMISSIONS_ADMIT_GROUPS), set(DESK_ADMIT_GROUPS))

    def test_only_admitting_roles_admit_and_the_gate_precedes_existence(self):
        _, _, admission = self._requested()
        for user in (self.doctor_user, self.nurse_a, self.front_desk, *self.denied.values()):
            with self.subTest(user=user.login):
                self._assert_refused(*self._http_admit(admission, self.bed_a2, user=user), 403, "admission_not_authorized")
                # Same answer for an id that does not exist: no existence leak.
                response, payload = self._post(
                    ADMIT % 999999999,
                    {"operation_token": token(), "expected_revision": 0, "bed_id": self.bed_a2.id},
                    user,
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(admission.state, "draft")
        for user in (self.manager, self.sysadmin):
            self.assertTrue(self._ok(DETAIL % admission.id, user)["admission"]["can_admit"])

    def test_replay_stale_revision_and_state(self):
        _, _, admission = self._requested()
        op = token()
        self.assertEqual(self._http_admit(admission, self.bed_a2, op=op, revision=1)[0].status_code, 200)
        response, payload = self._http_admit(admission, self.bed_a2, op=op, revision=1)
        self.assertEqual(response.status_code, 200, payload)
        self.assertTrue(payload["data"]["operation"]["replayed"])
        self.assertEqual(payload["data"]["workflow_revision"], 2)
        self._assert_refused(*self._http_admit(admission, self.bed_a2, revision=1), 409, "admission_revision_conflict")
        self._assert_refused(*self._http_admit(admission, self.bed_a2, revision=2), 409, "admission_invalid_state")

    def test_bed_conflicts(self):
        _, _, first = self._requested()
        _, _, second = self._requested()
        self.assertEqual(self._http_admit(first, self.bed_a2)[0].status_code, 200)
        self._assert_refused(*self._http_admit(second, self.bed_a2), 409, "admission_bed_conflict")
        self._assert_refused(*self._http_admit(second, self.bed_clean), 409, "admission_bed_unavailable")
        self._assert_refused(*self._http_admit(second, self.bed_off), 409, "admission_bed_unavailable")
        self._assert_refused(*self._http_admit(second, None, bed_id=None), 400, "admission_bed_required")
        self.assertEqual(second.state, "draft")
        self.assertEqual(second.workflow_revision, 1)

    def test_the_body_is_exactly_token_revision_and_bed(self):
        _, _, admission = self._requested()
        for body in ({}, {"operation_token": token(), "expected_revision": 1},
                     {"operation_token": token(), "expected_revision": 1, "bed_id": self.bed_a2.id, "ward_id": 1},
                     {"operation_token": token(), "expected_revision": "1", "bed_id": self.bed_a2.id},
                     {"operation_token": token(), "expected_revision": 1, "bed_id": "x"}):
            with self.subTest(body=body):
                self._assert_refused(*self._post(ADMIT % admission.id, body, self.receptionist), 400, "admission_invalid_payload")
        self.assertEqual(admission.state, "draft")

    def test_hidden_or_missing_admissions_are_404_for_an_admitting_role(self):
        response, payload = self._post(
            ADMIT % 999999999,
            {"operation_token": token(), "expected_revision": 0, "bed_id": self.bed_a2.id},
            self.receptionist,
        )
        self._assert_refused(response, payload, 404, "admission_not_found")

    def test_the_admit_response_carries_nothing_money_shaped(self):
        _, _, admission = self._requested()
        response, payload = self._http_admit(admission, self.bed_a2)
        text = json.dumps(payload).lower()
        for word in ("amount", "price", "tariff", "invoice", "receipt", "balance", "daily_rate"):
            self.assertNotIn(word, text)

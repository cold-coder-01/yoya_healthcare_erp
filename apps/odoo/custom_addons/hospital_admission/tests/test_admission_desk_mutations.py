"""Admissions Slice 2: the desk mutation authority, at the model.

  * _desk_request_admission -- the visit's doctor asks for an inpatient stay;
    a DRAFT bound to the visit, no bed, no occupancy.
  * _desk_admit              -- the admissions clerk assigns a bed and confirms,
    as ONE atomic act, through Slice 0's action_confirm_admission().

Every call runs inside a savepoint, the way the HTTP layer runs it, so a
refused call is checked for leaving NOTHING behind: no state, no occupancy, no
revision, no operation row.
"""
import uuid
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    AdmissionDeskError,
    AdmissionWorkflowError,
)

from .common import AdmissionCase


def token():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "admission_desk_mutations")
class AdmissionDeskMutationCase(AdmissionCase):
    def _visit(self, doctor=None, patient=None):
        """A registered visit: patient, appointment with its doctor, open encounter."""
        doctor = doctor or self.doctor
        patient = patient or self._patient()
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
        self._clear_visit(encounter)
        encounter.write({"state": "active"})
        return appointment, encounter

    def _request(self, appointment, user=None, reason="Needs IV antibiotics", op=None):
        user = user or self.doctor_user
        Admission = self.env["hospital.admission"].with_user(user)
        with self.env.cr.savepoint():
            return Admission._desk_request_admission(appointment.with_user(user), reason, op or token())

    def _admit(self, admission, bed, user=None, op=None, revision=None):
        user = user or self.receptionist
        record = admission.with_user(user)
        with self.env.cr.savepoint():
            return record._desk_admit(
                bed.id if bed else None,
                op or token(),
                admission.workflow_revision if revision is None else revision,
            )

    def _code(self, call, *args, **kwargs):
        with self.assertRaises(AdmissionDeskError) as caught:
            call(*args, **kwargs)
        return caught.exception.code

    def _ops(self, admission):
        return self.env["hospital.admission.operation"].sudo().search(
            [("admission_id", "=", admission.id)]
        )


@tagged("post_install", "-at_install", "admission_desk_mutations")
class TestDoctorRequest(AdmissionDeskMutationCase):
    def test_the_visits_doctor_requests_a_draft_bound_to_the_visit(self):
        appointment, encounter = self._visit()
        admission, replayed = self._request(appointment)
        admission = admission.sudo()
        self.assertFalse(replayed)
        self.assertEqual(admission.state, "draft")
        self.assertEqual(admission.encounter_id, encounter)
        self.assertEqual(admission.patient_id, appointment.patient_id)
        self.assertEqual(admission.physician_id, self.doctor)
        self.assertEqual(admission.appointment_id, appointment)
        self.assertEqual(admission.admission_reason, "Needs IV antibiotics")
        self.assertFalse(admission.bed_id, "the doctor chooses no bed")
        self.assertEqual(admission.workflow_revision, 1)
        self.assertEqual(self._ops(admission).mapped("operation_type"), ["request"])
        # Nothing clinical changed yet: the visit is still outpatient and active.
        self.assertEqual(encounter.encounter_type, "outpatient")
        self.assertEqual(encounter.state, "active")

    def test_the_requesting_doctor_can_read_their_request(self):
        appointment, _ = self._visit()
        admission, _ = self._request(appointment)
        visible = self.env["hospital.admission"].with_user(self.doctor_user).search(
            [("id", "=", admission.id)]
        )
        self.assertEqual(visible, admission)

    def test_a_replayed_token_returns_the_same_draft_without_a_second_revision(self):
        appointment, _ = self._visit()
        op = token()
        first, _ = self._request(appointment, op=op)
        again, replayed = self._request(appointment, op=op)
        self.assertTrue(replayed)
        self.assertEqual(again, first)
        self.assertEqual(first.sudo().workflow_revision, 1)
        self.assertEqual(len(self._ops(first)), 1)

    def test_the_same_token_for_a_different_request_is_a_conflict(self):
        appointment, _ = self._visit()
        op = token()
        self._request(appointment, op=op)
        self.assertEqual(
            self._code(self._request, appointment, reason="Different reason", op=op),
            "admission_operation_conflict",
        )

    def test_a_second_request_for_the_same_patient_is_refused(self):
        appointment, _ = self._visit()
        self._request(appointment)
        self.assertEqual(self._code(self._request, appointment), "admission_active_conflict")
        self.assertEqual(
            self.env["hospital.admission"].sudo().search_count(
                [("patient_id", "=", appointment.patient_id.id)]
            ),
            1,
        )

    def test_only_the_visits_own_doctor_or_oversight_may_request(self):
        appointment, _ = self._visit()
        for user in (self.other_doctor_user, self.nurse, self.receptionist, self.pharmacist, self.lab_tech):
            with self.subTest(user=user.login):
                self.assertEqual(
                    self._code(self._request, appointment, user=user),
                    "admission_not_authorized",
                )
        admission, _ = self._request(appointment, user=self.manager)
        self.assertEqual(admission.sudo().state, "draft")

    def test_a_closed_visit_cannot_be_admitted_from(self):
        appointment, encounter = self._visit()
        encounter.write({"state": "completed"})
        self.assertEqual(self._code(self._request, appointment), "admission_encounter_required")

    def test_payload_is_validated(self):
        appointment, _ = self._visit()
        for reason in ("", "   ", None, 42, "x" * 5000):
            with self.subTest(reason=reason):
                self.assertEqual(
                    self._code(self._request, appointment, reason=reason),
                    "admission_invalid_payload",
                )
        self.assertEqual(
            self._code(self._request, appointment, op="not-a-uuid"),
            "admission_invalid_payload",
        )


@tagged("post_install", "-at_install", "admission_desk_mutations")
class TestDeskAdmit(AdmissionDeskMutationCase):
    def _draft_request(self, **kwargs):
        appointment, encounter = self._visit(**kwargs)
        admission, _ = self._request(appointment)
        return admission.sudo(), encounter

    def test_admit_assigns_the_bed_and_confirms_in_one_act(self):
        admission, encounter = self._draft_request()
        before = admission.admission_date
        result, replayed = self._admit(admission, self.bed_a)
        self.assertFalse(replayed)
        self.assertEqual(result, admission)
        self.env.invalidate_all()
        self.assertEqual(admission.state, "admitted")
        self.assertEqual(admission.bed_id, self.bed_a)
        self.assertEqual(admission.ward_id, self.ward)
        self.assertEqual(self.bed_a.state, "occupied")
        self.assertEqual(self.bed_a.current_admission_id, admission)
        self.assertEqual(encounter.encounter_type, "inpatient")
        self.assertEqual(admission.workflow_revision, 2)
        self.assertGreaterEqual(admission.admission_date, before)
        self.assertEqual(sorted(self._ops(admission).mapped("operation_type")), ["admit", "request"])

    def test_a_replayed_admit_does_not_act_or_bump_again(self):
        admission, _ = self._draft_request()
        op = token()
        self._admit(admission, self.bed_a, op=op, revision=1)
        _, replayed = self._admit(admission, self.bed_a, op=op, revision=1)
        self.assertTrue(replayed)
        self.assertEqual(admission.workflow_revision, 2)
        self.assertEqual(len(self._ops(admission).filtered(lambda o: o.operation_type == "admit")), 1)

    def test_a_stale_revision_is_refused_and_nothing_moves(self):
        admission, _ = self._draft_request()
        self.assertEqual(
            self._code(self._admit, admission, self.bed_a, revision=0),
            "admission_revision_conflict",
        )
        self.env.invalidate_all()
        self.assertEqual(admission.state, "draft")
        self.assertEqual(self.bed_a.state, "available")
        self.assertEqual(admission.workflow_revision, 1)

    def test_two_clerks_admitting_the_same_draft(self):
        """The second clerk loaded revision 1 too; the draft has moved on."""
        admission, _ = self._draft_request()
        self._admit(admission, self.bed_a, revision=1)
        self.assertEqual(
            self._code(self._admit, admission, self.bed_b, revision=1, user=self.manager),
            "admission_revision_conflict",
        )
        self.assertEqual(
            self._code(self._admit, admission, self.bed_b, revision=2, user=self.manager),
            "admission_invalid_state",
        )
        self.env.invalidate_all()
        self.assertEqual(self.bed_b.state, "available")

    def test_two_drafts_targeting_the_same_bed(self):
        first, _ = self._draft_request()
        second, _ = self._draft_request()
        self._admit(first, self.bed_a)
        self.assertEqual(self._code(self._admit, second, self.bed_a), "admission_bed_conflict")
        self.env.invalidate_all()
        self.assertEqual(self.bed_a.current_admission_id, first)
        self.assertEqual(second.state, "draft")
        self.assertEqual(second.workflow_revision, 1)

    def test_unavailable_and_inactive_beds_are_refused(self):
        admission, _ = self._draft_request()
        self.bed_b._set_occupancy("maintenance")
        self.assertEqual(self._code(self._admit, admission, self.bed_b), "admission_bed_unavailable")
        self.bed_b._set_occupancy("available")
        self.bed_b.sudo().write({"active": False})
        self.assertEqual(self._code(self._admit, admission, self.bed_b), "admission_bed_unavailable")
        self.assertEqual(self._code(self._admit, admission, None), "admission_bed_required")

    def test_only_admitting_roles_may_admit(self):
        admission, _ = self._draft_request()
        for user in (self.doctor_user, self.nurse, self.pharmacist, self.lab_tech, self.accountant):
            with self.subTest(user=user.login):
                self.assertEqual(
                    self._code(self._admit, admission, self.bed_a, user=user),
                    "admission_not_authorized",
                )
        self._admit(admission, self.bed_a, user=self.admin)
        self.assertEqual(admission.state, "admitted")

    def test_a_patient_already_admitted_elsewhere_is_refused(self):
        admission, _ = self._draft_request()
        # A second draft for the same patient, from the back office (the desk
        # itself refuses a second request).
        duplicate = self.env["hospital.admission"].sudo().create({
            "patient_id": admission.patient_id.id,
            "encounter_id": admission.encounter_id.id,
            "company_id": self.company.id,
        })
        self._admit(admission, self.bed_a)
        self.assertEqual(self._code(self._admit, duplicate, self.bed_b), "admission_active_conflict")

    def test_a_failure_after_occupancy_rolls_everything_back(self):
        """No partial side effects: the operation row is the LAST write; make
        it fail and every earlier write -- state, bed, revision -- is undone."""
        admission, encounter = self._draft_request()
        target = type(self.env["hospital.admission"])
        with patch.object(target, "_desk_record_operation", side_effect=UserError("boom")):
            with self.assertRaises(UserError):
                self._admit(admission, self.bed_a)
        self.env.invalidate_all()
        self.assertEqual(admission.state, "draft")
        self.assertFalse(admission.bed_id)
        self.assertEqual(self.bed_a.state, "available")
        self.assertFalse(self.bed_a.current_admission_id)
        self.assertEqual(admission.workflow_revision, 1)
        self.assertEqual(encounter.encounter_type, "outpatient")
        self.assertEqual(self._ops(admission).mapped("operation_type"), ["request"])

    def test_the_revision_and_the_ledger_cannot_be_forged(self):
        admission, _ = self._draft_request()
        with self.assertRaises(AdmissionWorkflowError):
            admission.write({"workflow_revision": 99})
        with self.assertRaises(AdmissionWorkflowError):
            self.env["hospital.admission"].sudo().create(
                {"patient_id": self._patient().id, "workflow_revision": 5}
            )
        op = self._ops(admission)
        with self.assertRaises(UserError):
            op.write({"result_revision": 7})
        with self.assertRaises(UserError):
            op.unlink()
        with self.assertRaises(UserError):
            self.env["hospital.admission.operation"].sudo().create({
                "admission_id": admission.id, "operation_type": "admit",
                "operation_token": token(), "request_digest": "x",
                "result_revision": 1, "performed_by_id": self.env.uid,
            })

    def test_the_ledger_carries_no_patient_data(self):
        admission, _ = self._draft_request()
        op = self._ops(admission)
        self.assertEqual(len(op.request_digest), 64)
        self.assertNotIn("antibiotics", op.request_digest)
        self.assertEqual(
            {name for name in op._fields if not op._fields[name].automatic}
            - {"id", "display_name"},
            {"admission_id", "operation_type", "operation_token", "request_digest",
             "result_revision", "performed_by_id", "performed_at"},
        )


@tagged("post_install", "-at_install", "admission_desk_mutations")
class TestEpisodeAndHandoffs(AdmissionDeskMutationCase):
    def test_completing_the_consultation_does_not_close_an_admitted_episode(self):
        """The real flow: request, then the doctor finishes the consultation."""
        appointment, encounter = self._visit()
        admission, _ = self._request(appointment)
        encounter.sudo().action_complete()        # what appointment.action_done() runs
        self.assertEqual(encounter.state, "active", "a pending request keeps the episode open")
        self._admit(admission.sudo(), self.bed_a)
        encounter.sudo().action_complete()
        self.assertEqual(encounter.state, "active", "an admitted patient's episode stays open")
        self.assertEqual(admission.sudo().state, "admitted")

    def test_an_ordinary_visit_still_completes(self):
        _appointment, encounter = self._visit()
        encounter.sudo().action_complete()
        self.assertEqual(encounter.state, "completed")

    def test_the_ward_nurse_sees_the_admitted_patient_on_their_ward_only(self):
        appointment, _ = self._visit()
        admission, _ = self._request(appointment)
        self._admit(admission.sudo(), self.bed_a)     # bed_a is on the nurse's ward
        mine = self.env["hospital.admission"].with_user(self.nurse).search([("id", "=", admission.id)])
        self.assertEqual(mine, admission)
        self.assertEqual(mine.state, "admitted")
        self.assertEqual(mine.bed_id, self.bed_a)
        theirs = self.env["hospital.admission"].with_user(self.other_nurse).search([("id", "=", admission.id)])
        self.assertFalse(theirs)

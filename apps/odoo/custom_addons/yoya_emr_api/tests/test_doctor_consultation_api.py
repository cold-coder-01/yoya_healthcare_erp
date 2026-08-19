"""The consultation note: who owns it, when it exists, and who may overwrite it.

WHAT THESE TESTS ARE FOR
------------------------
Four properties carry this slice, and each has a class below.

  1. THE NOTE IS OPENED BY THE TRANSITION, AND EXACTLY ONCE PER ENCOUNTER.
     Reaching in_consultation opens it, through ANY channel; reading is pure and
     creates nothing. The old design opened it lazily from the GET, which made a
     clinical record appear as a side effect of a browser fetch and left visits
     started from the Odoo form with no note at all. The invariant now under
     test is:

         appointment.state == 'in_consultation'
             =>  a hospital.consultation exists for that encounter

     and a violation is REPORTED, never silently repaired.

  2. OWNERSHIP IS DERIVED, NEVER SUPPLIED. Patient, visit, encounter and
     physician all come from the record the caller already resolved through
     their own scope. The API rejects every one of them by name.

  3. A STALE WRITE IS REFUSED, NOT MERGED. Free-text clinical narrative has no
     safe automatic merge, so the second of two concurrent saves must be told
     to reload rather than silently discarding the first doctor's paragraph.

  4. THE CONFIDENTIALITY BOUNDARY HOLDS. A consultation payload carries no
     amount, receipt, agreement, membership number or payer name -- the same
     rule test_doctor_desk_api pins for the visit payload.

The presenting-complaint seeding gets its own class because the copy-once rule
fails in two opposite directions and both matter clinically: a mirror would let
a late triage edit rewrite the physician's note, and an overwrite-on-read would
throw away the physician's own wording every time the desk refreshed.
"""
import json
import uuid
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.yoya_clinical_bridge.models.consultation import (
    NARRATIVE_FIELDS,
    ConsultationConflict,
)

from .test_doctor_desk_api import (
    FORBIDDEN_KEYS,
    VISIT_SERIALIZER_TARGET,
    DoctorDeskCase,
)

G_NURSE = "hospital_management.group_hospital_nurse"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"

CONSULTATION = "/yoya-emr/api/v1/doctor/visits/%s/consultation"
SAVE = "/yoya-emr/api/v1/doctor/visits/%s/consultation/save"
START = "/yoya-emr/api/v1/doctor/visits/%s/start-consultation"

# Where the CONTROLLER looks the serializer up, not where it is defined --
# patching the definition would not affect the name already bound in the
# controller module. Same convention as VISIT_SERIALIZER_TARGET.
CONSULTATION_SERIALIZER_TARGET = (
    "odoo.addons.yoya_emr_api.controllers.doctor.serialize_consultation_envelope"
)

EXPECTED_CONSULTATION_KEYS = {
    "id", "name", "state", "started_at", "completed_at", "version", "editable",
    "presenting_complaint", "history_of_presenting_illness",
    "review_of_systems", "examination_findings", "assessment", "plan",
}


class ConsultationCase(DoctorDeskCase):
    """DoctorDeskCase plus the two roles this slice must keep out."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.receptionist_password = "dc-recep-pw-1"
        cls.receptionist = cls._make_user(
            "dc_recep", cls.receptionist_password, [G_RECEPTIONIST]
        )
        cls.accountant_password = "dc-acct-pw-1"
        cls.accountant = cls._make_user(
            "dc_acct", cls.accountant_password, [G_ACCOUNTANT]
        )

    # ------------------------------------------------------------------
    def _in_consultation_visit(self, doctor=None, complaint="DD complaint"):
        """A visit that has genuinely been started, through the real gate.

        Started AS THE ASSIGNED DOCTOR, not with sudo(). sudo() sets su=True but
        leaves env.user alone, and _assert_may_start_consultation decides on
        has_group / assignment -- so a sudo'd call is refused exactly as a
        stranger's would be. Going through the real gate also means every visit
        these tests use has genuinely passed triage and financial clearance.

        The consultation is never created here. It is created by the endpoint
        under test, which is the whole point: nothing in this fixture asserts
        the precondition the tests are checking.
        """
        doctor = doctor or self.doctor
        appointment, encounter = self._register(doctor=doctor)
        evaluation = self._triage(appointment, complete=False)
        evaluation.sudo().write({"chief_complaint": complaint})
        evaluation.sudo().action_done()
        self._pay(encounter)
        appointment.invalidate_recordset()
        appointment.with_user(doctor.user_id).action_start_consultation()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        # THE INVARIANT, asserted in the fixture every test builds on: reaching
        # in_consultation opened the consultation. If this ever fails, every
        # test below is testing something other than what it claims to.
        self.assertEqual(len(self._consultation_of(encounter)), 1)
        return appointment, encounter

    def _post_body(self, url, body, user=None, password=None):
        self._auth(user, password)
        response = self.url_open(
            url,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        return response, json.loads(response.text)

    def _consultation_of(self, encounter):
        return (
            self.env["hospital.consultation"]
            .sudo()
            .search([("encounter_id", "=", encounter.id)])
        )

    def _consultation_for(self, appointment):
        """The row opened by Start Consultation. Never creates one.

        Tests used to call get_or_create_for_appointment() here, which was
        harmless while the GET created the record too -- and would now hide the
        very invariant under test by manufacturing whatever it failed to find.
        """
        consultation = (
            self.env["hospital.consultation"]
            .sudo()
            .find_for_appointment(appointment.sudo())
        )
        self.assertTrue(consultation, "Start Consultation did not open a note")
        return consultation

    def _delete_consultation(self, consultation):
        """Remove the row underneath the workflow, as an integrity fault would.

        Raw SQL because unlink() is restricted to system administrators, which
        is the point: this simulates corruption, not a supported operation.
        """
        consultation.flush_recordset()
        self.env.cr.execute(
            "DELETE FROM hospital_consultation WHERE id = %s", (consultation.id,)
        )
        consultation.invalidate_recordset()


@tagged("post_install", "-at_install", "doctor_consultation")
class TestConsultationLifecycle(ConsultationCase):
    """When the note exists, and that there is never more than one."""

    def test_pre_start_get_returns_unavailable_and_creates_nothing(self):
        """THE regression this endpoint most easily introduces.

        A GET that opened a consultation for any visit the doctor selected
        would file a clinical record against a patient still at the cashier,
        and would do it silently, on a read.
        """
        appointment, encounter = self._ready_visit()
        self.assertEqual(appointment.state, "confirmed")

        response, payload = self._get(CONSULTATION % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertFalse(payload["data"]["available"])
        self.assertIsNone(payload["data"]["consultation"])
        self.assertIn("has not been started", payload["data"]["reason"])
        # Nothing was written.
        self.assertFalse(self._consultation_of(encounter))

    def test_get_reads_the_consultation_opened_by_start(self):
        appointment, encounter = self._in_consultation_visit()
        opened = self._consultation_of(encounter)

        response, payload = self._get(CONSULTATION % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["data"]["available"])
        consultation = payload["data"]["consultation"]
        self.assertIsNotNone(consultation)
        self.assertEqual(set(consultation), EXPECTED_CONSULTATION_KEYS)
        self.assertEqual(consultation["state"], "draft")
        self.assertTrue(consultation["editable"])
        self.assertTrue(consultation["version"])
        self.assertTrue(consultation["name"].startswith("CONS"))

        # The SAME row Start Consultation opened, not a second one.
        stored = self._consultation_of(encounter)
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored.id, opened.id)
        self.assertEqual(stored.id, consultation["id"])

    def test_repeated_get_returns_the_same_consultation(self):
        """A pure read repeated is a pure read. No row is ever added."""
        appointment, encounter = self._in_consultation_visit()

        _first, first_payload = self._get(CONSULTATION % appointment.id)
        _second, second_payload = self._get(CONSULTATION % appointment.id)

        first = first_payload["data"]["consultation"]
        second = second_payload["data"]["consultation"]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["name"], second["name"])
        self.assertEqual(len(self._consultation_of(encounter)), 1)

    def test_model_get_or_create_is_idempotent(self):
        """The opener is safe to call again, which is what makes a retried
        Start Consultation harmless."""
        appointment, encounter = self._in_consultation_visit()
        model = self.env["hospital.consultation"].sudo()
        already = self._consultation_of(encounter)

        first = model.get_or_create_for_appointment(appointment.sudo())
        second = model.get_or_create_for_appointment(appointment.sudo())

        self.assertEqual(first, second)
        self.assertEqual(first, already)
        self.assertEqual(len(self._consultation_of(encounter)), 1)

    def test_model_refuses_to_open_a_consultation_before_the_visit_starts(self):
        """Enforced in the MODEL, so the API is not the only thing obeying it."""
        appointment, encounter = self._ready_visit()

        with self.assertRaisesRegex(UserError, "cannot be opened"):
            self.env["hospital.consultation"].sudo().get_or_create_for_appointment(
                appointment.sudo()
            )

        self.assertFalse(self._consultation_of(encounter))

    def test_second_consultation_for_one_encounter_is_refused_by_the_database(self):
        """The unique index, not the search, is the guarantee.

        Two concurrent requests both read "no consultation" before either
        writes, so the application check can only ever be the friendly path.
        This asserts the invariant that holds when something skips it.
        """
        appointment, encounter = self._in_consultation_visit()
        self.env["hospital.consultation"].sudo().get_or_create_for_appointment(
            appointment.sudo()
        )

        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            with self.cr.savepoint():
                self.env["hospital.consultation"].sudo().create(
                    {"encounter_id": encounter.id}
                )

    def test_ownership_is_derived_from_the_visit_not_supplied(self):
        appointment, encounter = self._in_consultation_visit()

        consultation = self._consultation_for(appointment)

        self.assertEqual(consultation.encounter_id, encounter)
        self.assertEqual(consultation.patient_id, appointment.patient_id)
        self.assertEqual(consultation.appointment_id, appointment)
        self.assertEqual(consultation.doctor_id, appointment.doctor_id)
        self.assertEqual(consultation.company_id, encounter.company_id)
        self.assertTrue(consultation.started_at)
        self.assertFalse(consultation.completed_at)

    def test_the_api_rejects_client_supplied_ownership(self):
        """Rejected BY NAME, not dropped: a client sending doctor_id has a
        misunderstanding worth telling them about."""
        appointment, _encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)
        version = opened["data"]["consultation"]["version"]

        for field in ("doctor_id", "patient_id", "encounter_id", "appointment_id",
                      "state", "name", "id"):
            response, payload = self._post_body(
                SAVE % appointment.id,
                {"version": version, "assessment": "x", field: 1},
            )
            self.assertEqual(response.status_code, 400, field)
            self.assertEqual(payload["error"]["code"], "protected_field", field)

    def test_unknown_fields_are_rejected(self):
        appointment, _encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)

        response, payload = self._post_body(
            SAVE % appointment.id,
            {"version": opened["data"]["consultation"]["version"], "diagnosis": "x"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "unknown_field")

    def test_encounter_cannot_be_repointed_after_creation(self):
        appointment, _encounter = self._in_consultation_visit()
        _other_appointment, other_encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        with self.assertRaisesRegex(UserError, "cannot be changed once set"):
            consultation.write({"encounter_id": other_encounter.id})

    def test_save_on_a_pre_start_visit_is_refused(self):
        appointment, encounter = self._ready_visit()

        response, payload = self._post_body(
            SAVE % appointment.id, {"version": "anything", "assessment": "x"}
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_not_available")
        self.assertFalse(self._consultation_of(encounter))

    def test_missing_visit_is_a_404(self):
        response, payload = self._get(CONSULTATION % 99999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "visit_not_found")

    # ------------------------------------------------------------------
    # Creation belongs to the transition
    # ------------------------------------------------------------------
    def test_start_consultation_through_the_api_creates_exactly_one(self):
        appointment, encounter = self._ready_visit()
        self.assertFalse(self._consultation_of(encounter))

        response, _payload = self._post(START % appointment.id)

        self.assertEqual(response.status_code, 200)
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertEqual(len(self._consultation_of(encounter)), 1)

    def test_start_consultation_through_the_model_also_creates_one(self):
        """The invariant holds for the Odoo form button and RPC too.

        This is the case a controller-only implementation would have missed: a
        manager pressing Start Consultation in the backend would have produced
        a visit in consultation with no note, and the doctor opening it would
        have been shown an integrity error for someone else's shortcut.
        """
        appointment, encounter = self._ready_visit()
        self.assertFalse(self._consultation_of(encounter))

        appointment.with_user(self.doctor_user).action_start_consultation()

        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertEqual(len(self._consultation_of(encounter)), 1)

    def test_repeated_start_never_duplicates_the_consultation(self):
        appointment, encounter = self._ready_visit()

        self._post(START % appointment.id)
        self._post(START % appointment.id)
        appointment.with_user(self.doctor_user).action_start_consultation()

        self.assertEqual(len(self._consultation_of(encounter)), 1)

    def test_a_no_op_start_on_a_finished_visit_creates_nothing(self):
        """action_start_consultation filters on 'confirmed', so a call on a
        done visit is a no-op. It must stay one, not raise and not create."""
        appointment, encounter = self._ready_visit()
        appointment.sudo().action_done()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "done")

        appointment.with_user(self.doctor_user).action_start_consultation()

        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "done")
        self.assertFalse(self._consultation_of(encounter))

    def test_response_failure_after_start_rolls_back_appointment_and_note(self):
        """BOTH mutations, or neither.

        The consultation is opened inside action_start_consultation(), which the
        endpoint already wraps in a savepoint, so a failure while building the
        response must undo the transition AND the record it justified. A
        surviving note would be worse than a surviving transition: the retry
        would succeed, find the orphan note by encounter, and silently adopt it.
        """
        appointment, encounter = self._ready_visit()

        with patch(
            VISIT_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            response, payload = self._post(START % appointment.id)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "consultation_response_failed")

        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        # The consultation is asserted FIRST, deliberately: it is the mutation
        # this change added, and checking it before the appointment is what
        # makes a regression report "the note survived" rather than the older,
        # already-covered "the transition survived".
        self.assertFalse(
            self._consultation_of(encounter),
            "the consultation survived a rolled-back start",
        )
        self.assertEqual(appointment.state, "confirmed")
        self.assertNotEqual(encounter.state, "active")

    def test_the_retry_after_a_rolled_back_start_opens_exactly_one_note(self):
        appointment, encounter = self._ready_visit()

        with patch(
            VISIT_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            self._post(START % appointment.id)

        response, _payload = self._post(START % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._consultation_of(encounter)), 1)

    # ------------------------------------------------------------------
    # The read path is pure, and integrity faults are surfaced
    # ------------------------------------------------------------------
    def test_get_creates_nothing_when_the_note_is_missing(self):
        """PURITY, pinned where it actually matters.

        Deleting the row and re-reading is the only way to distinguish "the GET
        does not create" from "the GET had nothing left to create". If the read
        path ever regains a get_or_create, this is the test that fails.
        """
        appointment, encounter = self._in_consultation_visit()
        self._delete_consultation(self._consultation_of(encounter))
        self.assertFalse(self._consultation_of(encounter))

        response, payload = self._get(CONSULTATION % appointment.id)

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "consultation_missing")
        # Reported, NOT repaired.
        self.assertFalse(self._consultation_of(encounter))

    def test_the_missing_note_error_names_the_visit_and_leaks_nothing(self):
        appointment, encounter = self._in_consultation_visit()
        self._delete_consultation(self._consultation_of(encounter))

        _response, payload = self._get(CONSULTATION % appointment.id)

        message = payload["error"]["message"]
        self.assertIn(appointment.appointment_code, message)
        self.assertIn("Nothing has been changed", message)
        serialized = json.dumps(payload)
        self.assertNotIn("Traceback", serialized)

    def test_a_visit_with_no_encounter_is_reported_as_unavailable_not_broken(self):
        """A legacy visit that predates encounter tracking is not a fault.

        hospital.consultation.encounter_id is required, so such a visit can
        never carry a note. Reporting that as a 500 integrity error would send
        support hunting a bug that is really a property of the data, so it is
        answered as a 409 that says why.
        """
        appointment = self.env["hospital.appointment"].sudo().create(
            {
                "patient_id": self.env["hospital.patient"].sudo().create(
                    {"name": "No Encounter %s" % uuid.uuid4().hex[:6]}
                ).id,
                "doctor_id": self.doctor.id,
                "department_id": self.department.id,
                "appointment_date": self.env["hospital.appointment"]
                ._fields["appointment_date"]
                .convert_to_write(fields.Datetime.now(), self.env["hospital.appointment"]),
                "state": "in_consultation",
            }
        )
        self.assertFalse(appointment.encounter_id)

        response, payload = self._get(CONSULTATION % appointment.id)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_unavailable")
        self.assertIn("no encounter", payload["error"]["message"])

    def test_save_creates_nothing_when_the_note_is_missing(self):
        appointment, encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)
        version = opened["data"]["consultation"]["version"]
        self._delete_consultation(self._consultation_of(encounter))

        response, payload = self._post_body(
            SAVE % appointment.id, {"version": version, "assessment": "x"}
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "consultation_missing")
        self.assertFalse(self._consultation_of(encounter))


@tagged("post_install", "-at_install", "doctor_consultation")
class TestConsultationSeeding(ConsultationCase):
    """The presenting complaint is COPIED once, and never mirrored."""

    def test_presenting_complaint_is_seeded_from_completed_triage(self):
        appointment, _encounter = self._in_consultation_visit(
            complaint="Chest pain for two days"
        )

        _response, payload = self._get(CONSULTATION % appointment.id)

        self.assertEqual(
            payload["data"]["consultation"]["presenting_complaint"],
            "Chest pain for two days",
        )

    def test_a_draft_triage_seeds_nothing(self):
        """Copying a half-written sentence would freeze it into the note.

        The evaluation is still the nurse's to edit until it is done, so only a
        COMPLETED triage is a fact worth copying.
        """
        appointment, encounter = self._register(doctor=self.doctor)
        evaluation = self._triage(appointment, complete=False)
        evaluation.sudo().write({"chief_complaint": "half typed"})
        self._pay(encounter)
        # Reach in_consultation without completing triage: only a manager may,
        # and that is exactly the path that produces this shape.
        appointment.invalidate_recordset()
        consultation = self.env["hospital.consultation"].sudo()
        self.assertFalse(
            consultation._seed_presenting_complaint(appointment.sudo())
        )

    def test_later_reads_never_overwrite_a_doctor_edited_complaint(self):
        """THE copy-once rule, in the direction that loses physician work.

        A get-or-create that re-seeded on every call would discard the
        physician's own wording every time the desk refreshed the panel.
        """
        appointment, _encounter = self._in_consultation_visit(complaint="Nurse wording")
        _r, opened = self._get(CONSULTATION % appointment.id)
        version = opened["data"]["consultation"]["version"]

        _r2, saved = self._post_body(
            SAVE % appointment.id,
            {"version": version, "presenting_complaint": "Physician wording"},
        )
        self.assertEqual(
            saved["data"]["consultation"]["presenting_complaint"], "Physician wording"
        )

        # Re-read: get_or_create runs again and must leave the note alone.
        _r3, reread = self._get(CONSULTATION % appointment.id)
        self.assertEqual(
            reread["data"]["consultation"]["presenting_complaint"], "Physician wording"
        )

    def test_a_later_triage_edit_does_not_mirror_into_the_note(self):
        """THE copy-once rule, in the direction that rewrites clinical history.

        If this were a related field, a nurse reopening and amending the triage
        would silently rewrite what the physician documented.
        """
        appointment, _encounter = self._in_consultation_visit(complaint="Original")
        _r, opened = self._get(CONSULTATION % appointment.id)
        self.assertEqual(
            opened["data"]["consultation"]["presenting_complaint"], "Original"
        )

        # Reopening is manager-gated, and sudo() does not satisfy has_group.
        evaluation = appointment.sudo()._latest_evaluation()
        evaluation.with_user(self.manager).action_reopen()
        evaluation.sudo().write({"chief_complaint": "Amended by nurse"})
        evaluation.sudo().action_done()

        _r2, reread = self._get(CONSULTATION % appointment.id)
        self.assertEqual(
            reread["data"]["consultation"]["presenting_complaint"], "Original"
        )
        # The nursing record stays independently authoritative for its own account.
        self.assertEqual(evaluation.sudo().chief_complaint, "Amended by nurse")


@tagged("post_install", "-at_install", "doctor_consultation")
class TestConsultationSave(ConsultationCase):
    """Version-checked writes, the freeze, and the transaction boundary."""

    def test_a_fresh_version_saves_every_narrative_field(self):
        appointment, _encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)
        version = opened["data"]["consultation"]["version"]

        body = {"version": version}
        body.update({name: "value for %s" % name for name in NARRATIVE_FIELDS})
        response, payload = self._post_body(SAVE % appointment.id, body)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        consultation = payload["data"]["consultation"]
        for name in NARRATIVE_FIELDS:
            self.assertEqual(consultation[name], "value for %s" % name)
        # A NEW token comes back, so the client can chain saves without re-reading.
        self.assertNotEqual(consultation["version"], version)

    def test_line_breaks_are_preserved_exactly(self):
        """Paragraph breaks are clinical content, not formatting noise."""
        appointment, _encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)
        text = "Line one\nLine two\n\nParagraph two"

        _r2, payload = self._post_body(
            SAVE % appointment.id,
            {"version": opened["data"]["consultation"]["version"], "plan": text},
        )

        self.assertEqual(payload["data"]["consultation"]["plan"], text)

    def test_a_stale_version_is_refused_and_nothing_is_written(self):
        """THE last-write-wins defect, pinned.

        Doctor A and Doctor B both open the note. A saves. B saves against the
        version they read before A's write, and B's paragraph must NOT replace
        A's -- B is told to reload instead.
        """
        appointment, _encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)
        stale_version = opened["data"]["consultation"]["version"]

        _r2, first = self._post_body(
            SAVE % appointment.id,
            {"version": stale_version, "assessment": "First author"},
        )
        self.assertEqual(
            first["data"]["consultation"]["assessment"], "First author"
        )

        response, payload = self._post_body(
            SAVE % appointment.id,
            {"version": stale_version, "assessment": "Second author"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "consultation_conflict")
        self.assertIn("Reload", payload["error"]["message"])

        # The first author's text is intact. No merge, no overwrite.
        _r3, reread = self._get(CONSULTATION % appointment.id)
        self.assertEqual(
            reread["data"]["consultation"]["assessment"], "First author"
        )

    def test_a_missing_version_is_refused(self):
        appointment, _encounter = self._in_consultation_visit()
        self._get(CONSULTATION % appointment.id)

        response, payload = self._post_body(
            SAVE % appointment.id, {"assessment": "no version"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "missing_version")

    def test_the_conflict_is_not_reported_as_an_invalid_transition(self):
        """Pins the except-ordering in doctor_endpoint.

        ConsultationConflict subclasses UserError so that non-API callers get a
        clean refusal; that is exactly why the broad UserError handler would
        otherwise swallow it and answer 422.
        """
        appointment, _encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)
        version = opened["data"]["consultation"]["version"]
        self._post_body(SAVE % appointment.id, {"version": version, "plan": "a"})

        response, payload = self._post_body(
            SAVE % appointment.id, {"version": version, "plan": "b"}
        )

        self.assertEqual(response.status_code, 409)
        self.assertNotEqual(payload["error"]["code"], "invalid_workflow_state")
        self.assertNotEqual(payload["error"]["code"], "access_denied")

    def test_a_completed_consultation_cannot_be_edited(self):
        """The freeze is live NOW, even though nothing here can complete.

        A record that reaches 'completed' by any route -- a later slice, an
        administrator, a migration -- must already be immutable, or the freeze
        would be a rule only the code arriving later obeys.
        """
        appointment, _encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)
        version = opened["data"]["consultation"]["version"]
        consultation = self.env["hospital.consultation"].sudo().browse(
            opened["data"]["consultation"]["id"]
        )
        # Only the workflow column is written, which the freeze deliberately
        # leaves open so a later completion transition can stamp itself.
        consultation.write({"state": "completed"})

        response, payload = self._post_body(
            SAVE % appointment.id, {"version": version, "assessment": "after"}
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(payload["success"])
        consultation.invalidate_recordset()
        self.assertFalse(consultation.assessment)

        # And at the model layer, for every other channel.
        with self.assertRaisesRegex(UserError, "locked"):
            consultation.write({"assessment": "direct"})

    def test_the_model_refuses_a_stale_version_independently_of_the_api(self):
        """The check lives in the model, so every channel obeys it.

        THE OTHER PARTY'S WRITE IS SIMULATED IN SQL, DELIBERATELY. Odoo stamps
        write_date from PostgreSQL now(), which is the TRANSACTION timestamp, so
        two saves inside this single test transaction would leave the token
        identical and the test would pass for the wrong reason -- it would prove
        nothing about the comparison. Advancing write_date directly reproduces
        what a concurrent REQUEST actually does, which is the case the token
        exists for and the one the HTTP test above covers end to end.
        """
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)
        consultation.save_narrative({"plan": "first"}, consultation.version_token())

        # flush_recordset() before the raw UPDATE, or the ORM's still-pending
        # write lands AFTER it and re-stamps write_date to the transaction
        # timestamp -- silently undoing the simulated concurrent write and
        # making this test pass for the wrong reason.
        consultation.flush_recordset()
        stale = consultation.version_token()

        self.env.cr.execute(
            "UPDATE hospital_consultation "
            "SET write_date = write_date + interval '1 second' WHERE id = %s",
            (consultation.id,),
        )
        consultation.invalidate_recordset()
        self.assertNotEqual(consultation.version_token(), stale)

        with self.assertRaises(ConsultationConflict):
            consultation.save_narrative({"plan": "second"}, stale)

        consultation.invalidate_recordset()
        self.assertEqual(consultation.plan, "first")

    def test_the_model_accepts_a_current_version(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        consultation.save_narrative(
            {"plan": "accepted"}, consultation.version_token()
        )

        consultation.invalidate_recordset()
        self.assertEqual(consultation.plan, "accepted")

    def test_save_narrative_refuses_non_narrative_values(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        with self.assertRaises(AccessError):
            consultation.save_narrative(
                {"doctor_id": self.other_doctor.id}, consultation.version_token()
            )

    # ------------------------------------------------------------------
    # Transaction boundary
    # ------------------------------------------------------------------
    def test_response_failure_after_save_rolls_the_write_back(self):
        """Regression: a saved note must never survive a failed response.

        Structurally identical to the start-consultation case, and it matters
        MORE here. doctor_endpoint catches and RETURNS, so without the savepoint
        a failure raised after save_narrative() would commit the write and hand
        the doctor an error -- and the retry would then be refused as a stale
        version, because the write they were told had failed had already bumped
        write_date. The doctor would be locked out of their own note.
        """
        appointment, _encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)
        version = opened["data"]["consultation"]["version"]
        consultation = self.env["hospital.consultation"].sudo().browse(
            opened["data"]["consultation"]["id"]
        )
        self.assertFalse(consultation.assessment)

        with patch(
            CONSULTATION_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            response, payload = self._post_body(
                SAVE % appointment.id,
                {"version": version, "assessment": "must not survive"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            payload["error"]["code"], "consultation_note_response_failed"
        )
        # NOT reported as an authorization failure, which is what pins the
        # except-ordering.
        self.assertNotEqual(payload["error"]["code"], "access_denied")
        serialized = json.dumps(payload)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("simulated post-write failure", serialized)

        # The write genuinely did not land.
        consultation.invalidate_recordset()
        self.assertFalse(consultation.assessment)

    def test_the_retry_after_a_rollback_succeeds(self):
        """A rollback that left the note unusable would be a different bug."""
        appointment, _encounter = self._in_consultation_visit()
        _r, opened = self._get(CONSULTATION % appointment.id)
        version = opened["data"]["consultation"]["version"]

        with patch(
            CONSULTATION_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            failed, _ = self._post_body(
                SAVE % appointment.id, {"version": version, "assessment": "attempt"}
            )
        self.assertEqual(failed.status_code, 500)

        # The version is unchanged, because the write was rolled back -- so the
        # SAME token the client already holds still works.
        response, payload = self._post_body(
            SAVE % appointment.id, {"version": version, "assessment": "retry"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["consultation"]["assessment"], "retry")



@tagged("post_install", "-at_install", "doctor_consultation")
class TestConsultationAccess(ConsultationCase):
    """Whose note it is, and who is kept out of it."""

    def test_a_pure_doctor_cannot_reach_another_doctors_consultation(self):
        appointment, _encounter = self._in_consultation_visit(doctor=self.other_doctor)

        response, payload = self._get(
            CONSULTATION % appointment.id,
            user=self.doctor_user,
            password=self.doctor_password,
        )

        # Indistinguishable from a visit that does not exist, so the desk cannot
        # be used to confirm a colleague's visit is real.
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "visit_not_found")

    def test_the_record_rule_hides_another_doctors_consultation_at_the_orm(self):
        """Independent of the API: the rule, not the controller, is the control."""
        appointment, encounter = self._in_consultation_visit(doctor=self.other_doctor)
        consultation = self._consultation_for(appointment)

        visible = (
            self.env["hospital.consultation"]
            .with_user(self.doctor_user)
            .search([("id", "=", consultation.id)])
        )

        self.assertFalse(visible)

    def test_the_assigned_doctor_reaches_their_own_consultation_at_the_orm(self):
        appointment, _encounter = self._in_consultation_visit(doctor=self.doctor)
        consultation = self._consultation_for(appointment)

        visible = (
            self.env["hospital.consultation"]
            .with_user(self.doctor_user)
            .search([("id", "=", consultation.id)])
        )

        self.assertEqual(visible, consultation)

    def test_a_nurse_holds_no_consultation_access_at_all(self):
        """Hospital Nurse has NO ACL row, which is the whole security argument.

        An earlier version granted nurses department-scoped read for continuity
        of care. It was withdrawn because group_hospital_front_desk_nurse
        IMPLIES Hospital Nurse, so the grant reached the entrance, and the only
        thing holding it back was that a front desk nurse happens to have no
        permitted departments.
        """
        appointment, _encounter = self._in_consultation_visit(doctor=self.doctor)
        consultation = self._consultation_for(appointment)
        # Even fully departmentally privileged, a nurse reads nothing.
        self.nurse.sudo().write(
            {"yoya_permitted_department_ids": [(6, 0, [self.department.id])]}
        )

        with self.assertRaises(AccessError):
            consultation.with_user(self.nurse).read(["assessment"])
        with self.assertRaises(AccessError):
            consultation.with_user(self.nurse).write({"assessment": "nurse text"})

    def test_a_front_desk_nurse_is_excluded_structurally_not_incidentally(self):
        """THE property that keeps the entrance out of physician notes.

        THE OLD EXCLUSION WAS AN ACCIDENT AND THIS TEST PROVES THE NEW ONE IS
        NOT. Previously a front desk nurse was kept out only because they had
        no yoya_permitted_department_ids -- a field also consulted by the nurse
        rules on hospital.patient.evaluation and hospital.appointment, so
        granting a department for any unrelated workflow would have silently
        handed the entrance every physician note in it.

        Here the department IS granted, and access is still refused, because
        the control is now the absence of an ACL row rather than the emptiness
        of a configuration field. No record rule can widen that: OR-ing rules
        cannot grant a row on a model the group may not touch at all.
        """
        appointment, _encounter = self._in_consultation_visit(doctor=self.doctor)
        consultation = self._consultation_for(appointment)

        self.front_desk.sudo().write(
            {"yoya_permitted_department_ids": [(6, 0, [self.department.id])]}
        )
        self.assertTrue(self.front_desk.sudo().yoya_permitted_department_ids)
        # Still a nurse by implication, which is exactly the risk being closed.
        self.assertTrue(self.front_desk.has_group(G_NURSE))

        with self.assertRaises(AccessError):
            consultation.with_user(self.front_desk).read(["assessment"])

        response, payload = self._get(
            CONSULTATION % appointment.id,
            user=self.front_desk,
            password=self.fd_password,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "access_denied")

    def test_non_clinical_roles_are_denied_the_endpoint(self):
        appointment, _encounter = self._in_consultation_visit()

        for user, password in (
            (self.nurse, self.nurse_password),
            (self.front_desk, self.fd_password),
            (self.cashier, self.cashier_password),
            (self.receptionist, self.receptionist_password),
            (self.accountant, self.accountant_password),
        ):
            response, payload = self._get(
                CONSULTATION % appointment.id, user=user, password=password
            )
            self.assertEqual(response.status_code, 403, user.login)
            self.assertEqual(payload["error"]["code"], "access_denied", user.login)

            response, payload = self._post_body(
                SAVE % appointment.id,
                {"version": "x", "assessment": "y"},
                user=user,
                password=password,
            )
            self.assertEqual(response.status_code, 403, user.login)

    def test_non_clinical_roles_hold_no_orm_access_at_all(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        for user in (
            self.cashier, self.receptionist, self.accountant,
            self.nurse, self.front_desk,
        ):
            with self.assertRaises(AccessError, msg=user.login):
                consultation.with_user(user).read(["assessment"])

    def test_nobody_below_system_administrator_may_delete_a_consultation(self):
        appointment, _encounter = self._in_consultation_visit()
        consultation = self._consultation_for(appointment)

        # AccessError subclasses UserError, so one type covers both the ACL
        # refusal and the model's own. Odoo's assertRaises override does not
        # accept a tuple.
        with self.assertRaises(UserError):
            consultation.with_user(self.doctor_user).unlink()
        with self.assertRaises(UserError):
            consultation.with_user(self.manager).unlink()


@tagged("post_install", "-at_install", "doctor_consultation")
class TestConsultationConfidentiality(ConsultationCase):
    """No money, no receipts, no agreements, no named payer. Ever."""

    def test_the_consultation_payload_carries_no_commercial_field(self):
        appointment, _encounter = self._in_consultation_visit()

        _response, payload = self._get(CONSULTATION % appointment.id)

        serialized = json.dumps(payload).lower()
        for forbidden in FORBIDDEN_KEYS:
            self.assertNotIn(
                forbidden, serialized,
                "'%s' leaked into the consultation payload" % forbidden,
            )

    def test_an_unpaid_visit_leaks_nothing_either(self):
        """The interesting case: a visit with a real outstanding balance.

        A payload that only stays clean while nothing is owed is not clean.
        """
        appointment, encounter = self._register(doctor=self.doctor)
        evaluation = self._triage(appointment, complete=True)
        self.assertTrue(evaluation)
        self.assertFalse(encounter.reception_clearance_ok)
        # A manager may start without the cashier having been paid, so the
        # encounter genuinely still carries an unpaid consultation charge while
        # the note is read. hospital_billing's own gate may still refuse, which
        # is its right; the assertion below only matters if it did not.
        try:
            appointment.with_user(self.manager).action_start_consultation()
        except UserError:
            self.skipTest("financial gate refused the manager start path")
        appointment.invalidate_recordset()
        if appointment.state != "in_consultation":
            self.skipTest("financial gate refused the manager start path")

        _response, payload = self._get(CONSULTATION % appointment.id)

        serialized = json.dumps(payload).lower()
        for forbidden in FORBIDDEN_KEYS:
            self.assertNotIn(forbidden, serialized, forbidden)

    def test_the_payload_key_set_is_exactly_the_agreed_contract(self):
        """A new key cannot appear without this test being updated on purpose."""
        appointment, _encounter = self._in_consultation_visit()

        _response, payload = self._get(CONSULTATION % appointment.id)

        self.assertEqual(set(payload["data"]), {"available", "reason", "consultation"})
        self.assertEqual(
            set(payload["data"]["consultation"]), EXPECTED_CONSULTATION_KEYS
        )

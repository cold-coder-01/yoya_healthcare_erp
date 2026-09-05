"""Slice 9A: the longitudinal clinical history API.

WHAT THESE TESTS ARE FOR
------------------------
Five properties carry this slice.

  1. THE CURRENT VISIT IS THE ONLY CREDENTIAL. Every route is addressed by the
     caller's own appointment; the patient is derived from it and can never be
     named by the client. A doctor therefore cannot walk the hospital's census,
     which matters because hospital.patient carries a read ACL for Hospital
     Doctor and NO record rule at all.

  2. CROSS-PROVIDER READING IS THE POINT. A prior episode conducted by another
     doctor is visible, and only while the current appointment is active.

  3. EVERY REFUSAL LOOKS THE SAME. An unknown current visit, someone else's
     current visit, a historical visit belonging to another patient, an
     arbitrary historical id and a clinically empty episode all answer 404, so
     the response never confirms that a record exists.

  4. WIDENING THE RULES DID NOT WIDEN THE LIVE DESK. The current-visit
     endpoints stay visit-scoped even for a patient the caller now has
     longitudinal access to. This is the regression the whole slice risks.

  5. RELEASED-ONLY AND ABNORMAL SEMANTICS ARE NOT RE-DERIVED. History reuses
     result_serializers, so it cannot disagree with the Results tab.

FIXTURES DRIVE STATE, NOT THE DEPARTMENT WORKFLOW, for the reason the Slice 7B
and 8A/8B suites give: hospital_billing's action_validate/action_release
deliver charges and re-check financial clearance, which is a whole apparatus
these tests have no business exercising to ask what a GET returns.
"""
import base64
import json
import uuid

from odoo import fields
from odoo.tests import tagged

from .test_doctor_results_api import ResultsCase

HISTORY = "/yoya-emr/api/v1/doctor/visits/%s/history"
HISTORY_DETAIL = "/yoya-emr/api/v1/doctor/visits/%s/history/%s"

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

# Anything that would mean financial, operational or identity vocabulary
# reached a clinical history payload.
FORBIDDEN_SUBSTRINGS = (
    "payer", "insurance", "charge", "payment", "receipt", "cashier",
    "accounting", "fiscal", "stock", "batch", "cogs", "audit",
    "reception_outstanding_amount", "billing_blocked", "login", "email",
    "access_token", "/web/content", "localhost:8171", "traceback",
)


class HistoryCase(ResultsCase):
    """ResultsCase plus prior episodes for the same patient."""

    def _history(self, appointment, user=None, password=None, **params):
        self._auth(user, password)
        query = "&".join(
            "%s=%s" % (k, v) for k, v in params.items() if v is not None
        )
        url = HISTORY % appointment.id
        response = self.url_open("%s%s" % (url, "?" + query if query else ""))
        return response, json.loads(response.text)

    def _history_detail(self, appointment, historical_id, user=None, password=None):
        self._auth(user, password)
        response = self.url_open(HISTORY_DETAIL % (appointment.id, historical_id))
        return response, json.loads(response.text)

    # ------------------------------------------------------------------
    def _prior_visit(self, patient, doctor=None, state="completed",
                     with_consultation=True, complaint="Prior complaint",
                     opened_at=None):
        """A SETTLED prior episode for `patient`, conducted by `doctor`.

        Built directly rather than driven through reception: these tests ask
        what History returns for an episode that already exists, and the
        registration workflow would additionally exercise triage gating and
        financial clearance for no benefit to the question.
        """
        doctor = doctor or self.other_doctor
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": doctor.id,
            "department_id": self.other_department.id,
            "appointment_date": opened_at or fields.Datetime.now(),
            "state": "done",
        })
        encounter = self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id,
            "appointment_id": appointment.id,
            "primary_doctor_id": doctor.id,
            "department_id": self.other_department.id,
            "encounter_type": "outpatient",
            "state": state,
            "opened_at": opened_at or fields.Datetime.now(),
        })
        consultation = None
        if with_consultation:
            consultation = self.env["hospital.consultation"].sudo().create({
                "encounter_id": encounter.id,
                "doctor_id": doctor.id,
                "presenting_complaint": complaint,
                "assessment": "Prior assessment text",
                "plan": "Prior plan text",
            })
        return appointment, encounter, consultation

    def _prior_diagnosis(self, patient, consultation, diagnosis_type="primary"):
        return self.env["hospital.patient.diagnosis"].sudo().create({
            "patient_id": patient.id,
            "disease_id": self._disease().id,
            "consultation_id": consultation.id,
            # REQUIRED alongside consultation_id: the bridge's
            # _check_consultation_references refuses a consultation diagnosis
            # that does not name the consultation's own episode of care.
            "encounter_id": consultation.encounter_id.id,
            "appointment_id": consultation.appointment_id.id,
            "diagnosis_type": diagnosis_type,
            "certainty": "final",
            "severity": "moderate",
        })

    def _disease(self):
        tag = uuid.uuid4().hex[:6]
        return self.env["hospital.disease"].sudo().create(
            {"name": "Hist Disease %s" % tag, "code": "HX%s" % tag.upper()}
        )

    def _history_patient(self):
        """A patient with NO open episode yet.

        Prior episodes are built for this patient BEFORE the current visit,
        which is both the realistic order and the only possible one:
        yoya_reception_bridge refuses to create a second live episode while one
        is open, so a settled prior encounter cannot be inserted behind an
        active current one.
        """
        return self.env["hospital.patient"].sudo().create(
            {"name": "Hist Patient %s" % uuid.uuid4().hex[:8]}
        )

    def _current_visit(self, patient, doctor=None, open_consultation=False):
        """The caller's OWN active visit, which proves the care relationship.

        Created directly, and created LAST. The history endpoints need only a
        visit in the caller's scope and its patient; they do not require triage,
        financial clearance or an open consultation, so driving reception here
        would exercise machinery the question does not involve.
        """
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": (doctor or self.doctor).id,
            "department_id": self.department.id,
            "appointment_date": fields.Datetime.now(),
            "state": "in_consultation",
        })
        encounter = self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id,
            "appointment_id": appointment.id,
            "primary_doctor_id": (doctor or self.doctor).id,
            "department_id": self.department.id,
            "encounter_type": "outpatient",
            "state": "active",
        })
        if open_consultation:
            # Only the tests that exercise a WRITE endpoint need this: those
            # handlers resolve the current visit's open consultation before
            # they look at the record id under test.
            self.env["hospital.consultation"].sudo().create({
                "encounter_id": encounter.id,
                "doctor_id": (doctor or self.doctor).id,
            })
        return appointment

    def _visits(self, payload):
        return payload["data"]["visits"]

    def _assert_clean(self, payload):
        """Recursive vocabulary sweep over the WHOLE payload."""
        blob = json.dumps(payload).lower()
        for needle in FORBIDDEN_SUBSTRINGS:
            self.assertNotIn(
                needle, blob, "%r leaked into a history payload" % needle
            )


# ======================================================================
# 1. THE SUMMARY
# ======================================================================
@tagged("post_install", "-at_install", "doctor_history")
class TestHistorySummary(HistoryCase):

    def test_a_prior_visit_by_ANOTHER_doctor_is_returned(self):
        """THE property the slice exists for: continuity of care."""
        patient = self._history_patient()
        prior_appt, _enc, consultation = self._prior_visit(
            patient, doctor=self.other_doctor
        )
        self._prior_diagnosis(patient, consultation)

        current = self._current_visit(patient)
        response, payload = self._history(current)

        self.assertEqual(response.status_code, 200, payload)
        visits = self._visits(payload)
        self.assertEqual(len(visits), 1)
        self.assertEqual(visits[0]["appointment_id"], prior_appt.id)
        self.assertEqual(visits[0]["doctor"], self.other_doctor.name)

    def test_the_CURRENT_visit_is_excluded(self):
        patient = self._history_patient()
        self._prior_visit(patient)

        current = self._current_visit(patient)
        _response, payload = self._history(current)

        ids = [row["appointment_id"] for row in self._visits(payload)]
        self.assertNotIn(current.id, ids)

    def test_visits_are_newest_first(self):
        patient = self._history_patient()
        older, _e1, c1 = self._prior_visit(
            patient, opened_at="2026-01-05 09:00:00"
        )
        newer, _e2, c2 = self._prior_visit(
            patient, opened_at="2026-06-05 09:00:00"
        )
        self._prior_diagnosis(patient, c1)
        self._prior_diagnosis(patient, c2)

        current = self._current_visit(patient)
        _response, payload = self._history(current)

        ids = [row["appointment_id"] for row in self._visits(payload)]
        self.assertEqual(ids, [newer.id, older.id])

    def test_a_clinically_empty_visit_is_excluded(self):
        """An administrative booking is not a clinical episode."""
        patient = self._history_patient()
        self._prior_visit(patient, with_consultation=False)

        current = self._current_visit(patient)
        _response, payload = self._history(current)

        self.assertEqual(self._visits(payload), [])

    def test_the_primary_diagnosis_appears_on_the_card(self):
        patient = self._history_patient()
        _a, _e2, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation, "secondary")
        primary = self._prior_diagnosis(patient, consultation, "primary")

        current = self._current_visit(patient)
        _response, payload = self._history(current)

        card = self._visits(payload)[0]
        self.assertEqual(card["primary_diagnosis"]["name"], primary.disease_id.name)
        self.assertEqual(card["counts"]["diagnoses"], 2)

    def test_the_card_carries_no_narrative(self):
        """Notes, values and instructions belong inside the viewer."""
        patient = self._history_patient()
        _a, _e2, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation)

        current = self._current_visit(patient)
        _response, payload = self._history(current)

        card = self._visits(payload)[0]
        for absent in ("note", "assessment", "plan", "laboratory", "radiology"):
            self.assertNotIn(absent, card)
        self.assertNotIn("Prior assessment text", json.dumps(payload))

    def test_the_patient_block_carries_no_raw_id(self):
        patient = self._history_patient()
        self._prior_visit(patient)
        current = self._current_visit(patient)

        _response, payload = self._history(current)

        self.assertNotIn("id", payload["data"]["patient"])
        self.assertEqual(
            payload["data"]["patient"]["identification_code"],
            current.patient_id.identification_code,
        )

    def test_no_billing_or_identity_vocabulary_anywhere(self):
        patient = self._history_patient()
        _a, _e2, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation)

        current = self._current_visit(patient)
        _response, payload = self._history(current)
        self._assert_clean(payload)

    def test_a_patient_with_no_history_gets_an_honest_empty_list(self):
        patient = self._history_patient()
        current = self._current_visit(patient)

        response, payload = self._history(current)

        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(self._visits(payload), [])
        self.assertEqual(payload["data"]["total"], 0)
        self.assertFalse(payload["data"]["has_more"])


# ======================================================================
# 2. PAGINATION
# ======================================================================
@tagged("post_install", "-at_install", "doctor_history")
class TestHistoryPagination(HistoryCase):

    def _paged(self, count):
        """A patient with `count` settled prior episodes, then a current visit.

        The current visit is created LAST for the reason HistoryCase documents:
        a settled episode cannot be inserted behind an open one.
        """
        patient = self._history_patient()
        made = []
        for index in range(count):
            appointment, _e, consultation = self._prior_visit(
                patient, opened_at="2026-0%s-05 09:00:00" % ((index % 9) + 1)
            )
            self._prior_diagnosis(patient, consultation)
            made.append(appointment)
        return self._current_visit(patient), made

    def test_limit_bounds_the_page(self):
        current, _made = self._paged(5)

        _response, payload = self._history(current, limit=2)

        self.assertEqual(len(self._visits(payload)), 2)
        self.assertEqual(payload["data"]["limit"], 2)
        self.assertEqual(payload["data"]["total"], 5)
        self.assertTrue(payload["data"]["has_more"])

    def test_offset_walks_the_list(self):
        current, _made = self._paged(5)

        _r1, first = self._history(current, limit=2, offset=0)
        _r2, second = self._history(current, limit=2, offset=2)

        first_ids = [row["appointment_id"] for row in self._visits(first)]
        second_ids = [row["appointment_id"] for row in self._visits(second)]
        self.assertEqual(len(set(first_ids) & set(second_ids)), 0)
        self.assertEqual(second["data"]["offset"], 2)

    def test_has_more_is_false_on_the_last_page(self):
        current, _made = self._paged(3)

        _response, payload = self._history(current, limit=2, offset=2)

        self.assertFalse(payload["data"]["has_more"])

    def test_the_maximum_limit_is_enforced(self):
        patient = self._history_patient()
        current = self._current_visit(patient)

        _response, payload = self._history(current, limit=5000)

        self.assertEqual(payload["data"]["limit"], 50)

    def test_a_negative_offset_is_refused(self):
        patient = self._history_patient()
        current = self._current_visit(patient)

        response, payload = self._history(current, offset=-1)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_parameter")


# ======================================================================
# 3. THE DETAIL
# ======================================================================
@tagged("post_install", "-at_install", "doctor_history")
class TestHistoryDetail(HistoryCase):

    def test_the_note_of_another_doctors_prior_visit_is_readable(self):
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation)

        current = self._current_visit(patient)
        response, payload = self._history_detail(current, prior.id)

        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(payload["data"]["note"]["assessment"], "Prior assessment text")
        self.assertEqual(payload["data"]["visit"]["doctor"], self.other_doctor.name)

    def test_the_note_carries_no_live_workflow_affordance(self):
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation)

        current = self._current_visit(patient)
        _response, payload = self._history_detail(current, prior.id)

        note = payload["data"]["note"]
        self.assertNotIn("editable", note)
        self.assertNotIn("version", note)
        # And nothing from the completion envelope, which carries a figure.
        self.assertNotIn("can_complete", payload["data"])
        self.assertNotIn("completion_warnings", payload["data"])

    def test_diagnoses_are_read_only_and_primary_first(self):
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation, "secondary")
        self._prior_diagnosis(patient, consultation, "primary")

        current = self._current_visit(patient)
        _response, payload = self._history_detail(current, prior.id)

        rows = payload["data"]["diagnoses"]
        self.assertEqual(rows[0]["diagnosis_type"], "primary")
        self.assertTrue(all(row["editable"] is False for row in rows))

    def test_every_section_is_present_even_when_empty(self):
        patient = self._history_patient()
        prior, _enc, _c = self._prior_visit(patient)

        current = self._current_visit(patient)
        _response, payload = self._history_detail(current, prior.id)

        for section in ("visit", "triage", "note", "diagnoses",
                        "medications", "laboratory", "radiology"):
            self.assertIn(section, payload["data"])

    def test_no_billing_or_identity_vocabulary_anywhere(self):
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation)

        current = self._current_visit(patient)
        _response, payload = self._history_detail(current, prior.id)
        self._assert_clean(payload)


# ======================================================================
# 4. AUTHORIZATION AND NON-DISCLOSURE
# ======================================================================
@tagged("post_install", "-at_install", "doctor_history")
class TestHistoryAuthorization(HistoryCase):

    def _assert_same_404(self, response, payload):
        self.assertEqual(response.status_code, 404)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "visit_not_found")

    def test_an_unknown_current_visit_is_404(self):
        self._auth()
        response = self.url_open(HISTORY % 99999999)
        self._assert_same_404(response, json.loads(response.text))

    def test_ANOTHER_doctors_current_visit_is_THE_SAME_404(self):
        """Indistinguishable from an id that never existed."""
        stranger = self._history_patient()
        theirs = self._current_visit(stranger, doctor=self.other_doctor)

        response, payload = self._history(theirs)
        self._assert_same_404(response, payload)

        self._auth()
        missing = self.url_open(HISTORY % 99999999)
        self.assertEqual(missing.status_code, response.status_code)
        self.assertEqual(
            json.loads(missing.text)["error"]["code"], payload["error"]["code"]
        )

    def test_a_historical_visit_of_ANOTHER_patient_is_404(self):
        """The check the record rules cannot make: the care-relationship rule
        admits every record of every patient this doctor treats, so only an
        explicit patient match stops one patient's episode being rendered
        under another patient's name."""
        # A SECOND patient of the SAME doctor, so the care-relationship rule
        # admits their records and only the explicit patient match refuses.
        stranger = self._history_patient()
        stranger_prior, _enc, consultation = self._prior_visit(stranger)
        self._prior_diagnosis(stranger, consultation)
        self._current_visit(stranger)

        patient = self._history_patient()
        current = self._current_visit(patient)

        response, payload = self._history_detail(current, stranger_prior.id)
        self._assert_same_404(response, payload)

    def test_an_arbitrary_historical_id_is_404(self):
        patient = self._history_patient()
        current = self._current_visit(patient)

        response, payload = self._history_detail(current, 99999999)
        self._assert_same_404(response, payload)

    def test_a_clinically_empty_historical_visit_is_404(self):
        """Excluded from the list, therefore not readable by typing its id."""
        patient = self._history_patient()
        empty, _enc, _c = self._prior_visit(patient, with_consultation=False)
        current = self._current_visit(patient)

        response, payload = self._history_detail(current, empty.id)
        self._assert_same_404(response, payload)

    def test_history_lapses_when_the_current_episode_closes(self):
        """The documented Phase-1 consequence, asserted end to end."""
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation)

        current = self._current_visit(patient)
        response, payload = self._history_detail(current, prior.id)
        self.assertEqual(response.status_code, 200, payload)

        current.sudo().write({"state": "done"})

        closed, closed_payload = self._history_detail(current, prior.id)
        # The visit itself leaves the doctor's scope, so the anchor is gone.
        self._assert_same_404(closed, closed_payload)

    def test_a_nurse_is_refused_the_doctor_desk(self):
        patient = self._history_patient()
        current = self._current_visit(patient)

        response, payload = self._history(
            current, user=self.nurse, password=self.nurse_password
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "access_denied")

    def test_a_cashier_is_refused_the_doctor_desk(self):
        patient = self._history_patient()
        current = self._current_visit(patient)

        response, payload = self._history(
            current, user=self.cashier, password=self.cashier_password
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "access_denied")

    def test_a_front_desk_nurse_is_refused_the_doctor_desk(self):
        patient = self._history_patient()
        current = self._current_visit(patient)

        response, payload = self._history(
            current, user=self.front_desk, password=self.fd_password
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "access_denied")

    def test_a_manager_keeps_operational_breadth(self):
        """Manager IMPLIES doctor, and Odoo ORs rule domains. The history
        surface must not clip a manager down to the doctor's care scope."""
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation)
        current = self._current_visit(patient)

        response, payload = self._history(
            current, user=self.manager, password=self.manager_password
        )

        self.assertEqual(response.status_code, 200, payload)
        ids = [row["appointment_id"] for row in self._visits(payload)]
        self.assertIn(prior.id, ids)

    def test_history_is_GET_only(self):
        patient = self._history_patient()
        current = self._current_visit(patient)
        self._auth()

        response = self.url_open(
            HISTORY % current.id,
            data="{}",
            headers={"Content-Type": "application/json"},
        )

        self.assertIn(response.status_code, (404, 405))


# ======================================================================
# 5. THE REGRESSION THE WIDENED RULES RISK
# ======================================================================
@tagged("post_install", "-at_install", "doctor_history")
class TestLiveEndpointsStayVisitScoped(HistoryCase):
    """Widening READ must not let a CURRENT-visit endpoint reach a historical
    record. The record-id loaders in the controller re-check consultation
    identity for exactly this reason; these tests pin that they still do."""

    def _prepared(self):
        """A live current visit, plus a historical diagnosis of the same patient."""
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        diagnosis = self._prior_diagnosis(patient, consultation)
        current = self._current_visit(patient, open_consultation=True)
        return current, prior, consultation, diagnosis

    def test_a_historical_diagnosis_cannot_be_edited_through_the_current_visit(self):
        current, _prior, _consultation, diagnosis = self._prepared()
        self._auth()

        response = self.url_open(
            "/yoya-emr/api/v1/doctor/visits/%s/diagnoses/%s/update"
            % (current.id, diagnosis.id),
            data=json.dumps({"diagnosis_type": "secondary"}),
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(response.status_code, 404)
        diagnosis.invalidate_recordset()
        self.assertEqual(diagnosis.diagnosis_type, "primary")

    def test_a_historical_diagnosis_cannot_be_removed_through_the_current_visit(self):
        current, _prior, _consultation, diagnosis = self._prepared()
        self._auth()

        response = self.url_open(
            "/yoya-emr/api/v1/doctor/visits/%s/diagnoses/%s/remove"
            % (current.id, diagnosis.id),
            data="{}",
            headers={"Content-Type": "application/json"},
        )

        self.assertEqual(response.status_code, 404)
        diagnosis.invalidate_recordset()
        self.assertTrue(diagnosis.exists())

    def test_the_current_diagnosis_list_does_not_include_history(self):
        current, _prior, _consultation, diagnosis = self._prepared()

        response, payload = self._get(
            "/yoya-emr/api/v1/doctor/visits/%s/diagnoses" % current.id
        )

        self.assertEqual(response.status_code, 200, payload)
        ids = [row["id"] for row in payload["data"]["diagnoses"]]
        self.assertNotIn(diagnosis.id, ids)

    def test_the_current_results_tab_does_not_include_history(self):
        current, prior, _consultation, _diagnosis = self._prepared()

        _response, payload = self._results(current)

        self.assertEqual(payload["data"]["laboratory"], [])
        self.assertEqual(payload["data"]["radiology"], [])

    def test_the_worklist_still_shows_only_my_own_visits(self):
        """The care relationship must not put another doctor's visit on my
        queue: clinical_scope is an API-layer domain, not an ir.rule."""
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation)
        self._current_visit(patient)

        response, payload = self._get("/yoya-emr/api/v1/doctor/worklist")

        self.assertEqual(response.status_code, 200, payload)
        ids = [row["appointment_id"] for row in payload["data"]["rows"]]
        self.assertNotIn(prior.id, ids)

    def test_the_visit_detail_of_a_historical_visit_is_still_refused(self):
        """Even though its records are now readable, the VISIT is not mine."""
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        self._prior_diagnosis(patient, consultation)
        self._current_visit(patient)

        response, payload = self._get(
            "/yoya-emr/api/v1/doctor/visits/%s" % prior.id
        )

        self.assertIn(response.status_code, (403, 404))
        self.assertTrue(payload.get("error"))


# ======================================================================
# 6. HISTORICAL IMAGING
# ======================================================================
@tagged("post_install", "-at_install", "doctor_history")
class TestHistoryImages(HistoryCase):
    """Bytes of a PRIOR episode's released image.

    A DEDICATED HISTORY ROUTE, not a widened /results/images. The reasoning is
    in the handler's docstring; these tests pin the consequence: the
    current-visit route still refuses a historical visit, and the history route
    refuses everything except an image of the named prior episode.
    """

    IMAGE = ("/yoya-emr/api/v1/doctor/visits/%s/history/%s/images/%s")
    RESULTS_IMAGE = "/yoya-emr/api/v1/doctor/visits/%s/results/images/%s"

    def _released_image_for(self, consultation, patient, release=True):
        """A released report with one image, attached to `consultation`.

        The image is attached while the result is still ENTERED, because Slice
        8A freezes the set from validated onward; that freeze is respected
        rather than worked around.
        """
        request = self.env["hospital.radiology.request"].sudo().create({
            "patient_id": patient.id,
            "physician_id": self.other_doctor.id,
            "appointment_id": consultation.appointment_id.id,
            "consultation_id": consultation.id,
            # Same bridge invariant the diagnosis and prescription fixtures
            # satisfy: a consultation-linked record must cite the
            # consultation's own episode of care.
            "encounter_id": consultation.encounter_id.id,
            "line_ids": [(0, 0, {"exam_id": self.chest_xray.id})],
        })
        result = self.env["hospital.radiology.result"].sudo().create({
            "request_id": request.id,
            "patient_id": patient.id,
            "physician_id": self.other_doctor.id,
            "state": "entered",
            "impression": "Prior impression",
        })
        image = self.env["hospital.radiology.image"].sudo().create({
            "result_id": result.id,
            "name": "Prior study",
            "filename": "prior.png",
            "file": PNG,
        })
        result.sudo().write({"state": "validated"})
        if release:
            # A released report is FROZEN by hospital_radiology, so a test that
            # wants an unreleased one must stop here rather than roll back.
            result.sudo().write({"state": "released"})
        return result, image

    def _fixture(self):
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        _result, image = self._released_image_for(consultation, patient)
        current = self._current_visit(patient)
        return patient, current, prior, image

    def _open(self, url):
        self._auth()
        return self.url_open(url)

    def test_a_released_historical_image_is_served(self):
        _patient, current, prior, image = self._fixture()

        response = self._open(self.IMAGE % (current.id, prior.id, image.id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_the_metadata_reaches_the_detail_payload(self):
        _patient, current, prior, image = self._fixture()

        _response, payload = self._history_detail(current, prior.id)

        result = payload["data"]["radiology"][0]["result"]
        self.assertEqual(result["image_count"], 1)
        self.assertEqual(result["images"][0]["id"], image.id)
        # No URL, no attachment id, no token crosses.
        blob = json.dumps(payload)
        for needle in ("http://", "https://", "/web/content", "access_token",
                       "ir.attachment", "datas"):
            self.assertNotIn(needle, blob)

    def test_an_UNRELEASED_historical_report_yields_no_image_and_no_bytes(self):
        patient = self._history_patient()
        prior, _enc, consultation = self._prior_visit(patient)
        _result, image = self._released_image_for(
            consultation, patient, release=False
        )
        current = self._current_visit(patient)

        _response, payload = self._history_detail(current, prior.id)
        self.assertIsNone(payload["data"]["radiology"][0]["result"])

        response = self._open(self.IMAGE % (current.id, prior.id, image.id))
        self.assertEqual(response.status_code, 404)

    def test_the_CURRENT_visit_image_route_still_refuses_a_historical_visit(self):
        """THE regression the dedicated route exists to avoid."""
        _patient, current, prior, image = self._fixture()

        response = self._open(self.RESULTS_IMAGE % (prior.id, image.id))

        self.assertIn(response.status_code, (403, 404))

    def test_an_image_of_ANOTHER_patient_is_404(self):
        stranger = self._history_patient()
        stranger_prior, _e, stranger_consultation = self._prior_visit(stranger)
        _r, stranger_image = self._released_image_for(
            stranger_consultation, stranger
        )
        self._current_visit(stranger)

        _patient, current, prior, _image = self._fixture()

        response = self._open(
            self.IMAGE % (current.id, prior.id, stranger_image.id)
        )
        self.assertEqual(response.status_code, 404)

    def test_an_arbitrary_image_id_is_404(self):
        _patient, current, prior, _image = self._fixture()

        response = self._open(self.IMAGE % (current.id, prior.id, 99999999))

        self.assertEqual(response.status_code, 404)

    def test_an_unowned_current_visit_is_404(self):
        _patient, _current, prior, image = self._fixture()
        stranger = self._history_patient()
        theirs = self._current_visit(stranger, doctor=self.other_doctor)

        response = self._open(self.IMAGE % (theirs.id, prior.id, image.id))

        self.assertEqual(response.status_code, 404)


# ======================================================================
# 7. SOURCE-LEVEL INVARIANTS
# ======================================================================
@tagged("post_install", "-at_install", "doctor_history")
class TestHistorySourceInvariants(HistoryCase):
    """Properties that are cheaper to assert about the SOURCE than to provoke.

    A sudo() reintroduced into the history path would not fail any behavioural
    test above: it would make them pass MORE easily, by bypassing the very
    record rules those tests exist to exercise. So it is asserted directly.
    """

    HISTORY_FUNCTIONS = (
        "_load_history_current_visit",
        "_history_patient_and_encounter",
        "_history_clinical_children",
        "_history_evaluation",
        "_history_has_substance",
        "_history_page_children",
        "_history_children_for",
        "_history_paginate",
        "_load_history_episode",
        "_serve_released_image",
        "history_summary",
        "history_detail",
        "history_image_content",
    )

    def _controller_source(self):
        import inspect

        from odoo.addons.yoya_emr_api.controllers import doctor

        return inspect.getsource(doctor)

    def test_no_sudo_anywhere_in_the_history_code_path(self):
        import re

        source = self._controller_source()
        for name in self.HISTORY_FUNCTIONS:
            match = re.search(r"\n(    )?def %s\(" % re.escape(name), source)
            self.assertTrue(match, "%s vanished from the controller" % name)
            start = match.start() + 1
            rest = source[start + 10:]
            nxt = re.search(r"\n(    )?def ", rest)
            body = source[start:start + 10 + (nxt.start() if nxt else len(rest))]
            self.assertNotIn(
                "sudo", body,
                "%s reintroduced sudo into the history path" % name,
            )

    def test_the_history_serializers_never_sudo(self):
        import inspect

        from odoo.addons.yoya_emr_api.services import history_serializers

        self.assertNotIn("sudo", inspect.getsource(history_serializers))

    def test_the_completion_envelope_is_not_reachable_from_history(self):
        """serialize_consultation_envelope carries an outstanding AMOUNT.

        Asserted over the module's IMPORTS rather than its text: the docstring
        names the excluded symbols deliberately, to explain why they are
        excluded, and a substring check would trip on the explanation instead
        of on the code.
        """
        import ast
        import inspect

        from odoo.addons.yoya_emr_api.services import history_serializers

        tree = ast.parse(inspect.getsource(history_serializers))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

        self.assertIn("serialize_consultation", imported)
        self.assertNotIn("serialize_consultation_envelope", imported)
        self.assertNotIn("doctor_serializers", imported)
        self.assertNotIn(".doctor_serializers", imported)
        for name in imported:
            self.assertNotIn(
                "cashier", name, "a cashier serializer reached History"
            )
            self.assertNotIn(
                "insurance", name, "an insurance serializer reached History"
            )

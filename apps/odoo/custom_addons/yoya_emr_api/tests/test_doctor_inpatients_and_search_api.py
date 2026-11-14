"""Doctor Desk: the Inpatients lane and "search all my visits".

Business decisions 8 and 9 (docs/business-decisions.md):

  1. THE LANE. Current stays (admitted / transferred) whose visit is booked to
     the caller; managers and system administrators see every one. An
     admitting physician who is not the visit's booked doctor does not see the
     stay here, and a stay with no visit is never listed. The date is ignored.
  2. THE ROW. The ordinary queue row for the visit plus the stay: bed, length
     of stay, the open consultation (with its holder) and the discharge status
     from the existing discharge checks. Nothing money-shaped.
  3. THE SEARCH. Any date, the worklist's states (draft and cancelled
     excluded), patient name / chart number / appointment code / consultation
     reference, the caller's own visits only, newest first, limited and flagged
     when truncated. At least two letters.
  4. THE WORKLIST with a date is unchanged.
"""
import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .test_admissions_desk_api import (
    AMOUNT_FREE_FLAG_KEYS,
    FORBIDDEN_KEY_WORDS,
    _walk_keys,
)
from .test_admissions_desk_discharge_api import AdmissionsDischargeCase

DOCTOR_API = "/yoya-emr/api/v1/doctor"
INPATIENTS = DOCTOR_API + "/inpatients"
SEARCH = DOCTOR_API + "/visit-search"
WORKLIST = DOCTOR_API + "/worklist"


@tagged("post_install", "-at_install", "doctor_inpatients_search")
class TestDoctorInpatientsAndSearch(AdmissionsDischargeCase):
    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    def _new_bed(self):
        return self._bed(self.room_a, "IP-%s" % uuid.uuid4().hex[:5])

    def _lane(self, user=None, **params):
        return self._ok(INPATIENTS, user or self.doctor_user, **params)

    def _lane_ids(self, user=None, **params):
        return [row["inpatient"]["admission_id"] for row in self._lane(user, **params)["rows"]]

    def _row(self, admission, user=None):
        rows = {r["inpatient"]["admission_id"]: r for r in self._lane(user)["rows"]}
        self.assertIn(admission.id, rows)
        return rows[admission.id]

    def _search(self, q, user=None, **params):
        return self._ok(SEARCH, user or self.doctor_user, q=q, **params)

    def _search_ids(self, q, user=None, **params):
        return [row["appointment_id"] for row in self._search(q, user, **params)["rows"]]

    def _named_visit(self, name, doctor=None, days_ago=0, state="confirmed", when=None):
        """A visit for a patient with a known name, on a chosen day."""
        patient = self.env["hospital.patient"].sudo().create({"name": name})
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": (doctor or self.doctor).id,
            "appointment_date": when or (fields.Datetime.now() - timedelta(days=days_ago)),
            "state": "confirmed",
        })
        if state != "confirmed":
            appointment.write({"state": state})
        return appointment

    def _open_consultation(self, appointment):
        appointment = appointment.sudo()
        appointment._ensure_consultation_billing()
        appointment.write({"state": "in_consultation"})
        consultation = self.env["hospital.consultation"].sudo().get_or_create_for_appointment(
            appointment
        )
        self.assertEqual(consultation.state, "draft")
        return consultation

    def _assert_no_money(self, value):
        for key in _walk_keys(value):
            if key in AMOUNT_FREE_FLAG_KEYS:
                continue
            words = set(str(key).lower().split("_"))
            self.assertFalse(words & FORBIDDEN_KEY_WORDS, "money-shaped key %r" % key)

    # ------------------------------------------------------------------
    # 1. The lane: who is listed
    # ------------------------------------------------------------------
    def test_lane_lists_my_current_inpatients_whatever_the_date(self):
        appointment, _encounter, admission = self._inpatient(self._new_bed())
        # The visit began long ago: the lane does not care.
        appointment.sudo().write({"appointment_date": fields.Datetime.now() - timedelta(days=52)})

        data = self._lane(date="2020-01-01")
        self.assertIn(admission.id, [r["inpatient"]["admission_id"] for r in data["rows"]])
        self.assertEqual(data["count"], len(data["rows"]))
        self.assertFalse(data["meta"]["truncated"])
        row = self._row(admission)
        self.assertEqual(row["visit"]["appointment_id"], appointment.id)

    def test_lane_excludes_other_doctors_and_closed_or_unlinked_stays(self):
        _a, _e, mine = self._inpatient(self._new_bed())
        ids = self._lane_ids()
        self.assertIn(mine.id, ids)
        # The census stays carry no visit (adm_mine is even this doctor's own
        # admission by physician and primary doctor): never listed.
        for stay in (self.adm_mine, self.adm_transferred, self.adm_other_ward,
                     self.adm_draft_bed, self.adm_discharged, self.adm_cancelled):
            self.assertNotIn(stay.id, ids)
        # The other doctor does not see my patient.
        self.assertNotIn(mine.id, self._lane_ids(self.other_doctor_user))

    def test_a_draft_request_is_not_an_inpatient(self):
        _appointment, _encounter, requested = self._requested()
        self.assertEqual(requested.sudo().state, "draft")
        self.assertNotIn(requested.id, self._lane_ids())

    def test_admitting_physician_who_is_not_the_booked_doctor_does_not_see_it(self):
        """Decision 9, the known limitation: the centre panel could not open
        that visit, so the lane does not list it."""
        appointment, _encounter, admission = self._requested()
        # Attribution is editable while the request is a draft.
        admission.sudo().write({"physician_id": self.other_doctor.id})
        response, payload = self._http_admit(admission, self._new_bed())
        self.assertEqual(response.status_code, 200, payload)

        # The other doctor may read the admission (they are its physician) ...
        self.assertTrue(
            self.env["hospital.admission"].with_user(self.other_doctor_user).search(
                [("id", "=", admission.id)]
            )
        )
        # ... but the lane lists only stays whose visit is booked to them.
        self.assertNotIn(admission.id, self._lane_ids(self.other_doctor_user))

        # The booked doctor sees it, without the physician's discharge request.
        row = self._row(admission)
        self.assertEqual(row["visit"]["appointment_id"], appointment.id)
        self.assertFalse(row["inpatient"]["discharge"]["can_request_discharge"])

    def test_managers_and_system_administrators_see_every_inpatient(self):
        _a, _e, mine = self._inpatient(self._new_bed())
        appointment, _encounter = self._visit(self.other_doctor)
        response, payload = self._request(appointment, user=self.other_doctor_user)
        self.assertEqual(response.status_code, 200, payload)
        theirs = self.env["hospital.admission"].sudo().browse(
            payload["data"]["admission"]["admission"]["id"]
        )
        response, payload = self._http_admit(theirs, self._new_bed())
        self.assertEqual(response.status_code, 200, payload)

        for user in (self.manager, self.sysadmin):
            ids = self._lane_ids(user)
            self.assertIn(mine.id, ids)
            self.assertIn(theirs.id, ids)
        self.assertNotIn(theirs.id, self._lane_ids())

    def test_lane_limit_and_truncation(self):
        self._inpatient(self._new_bed())
        self._inpatient(self._new_bed())
        data = self._lane(limit=1)
        self.assertEqual(len(data["rows"]), 1)
        self.assertTrue(data["meta"]["truncated"])
        self.assertEqual(self._lane(limit=100000)["filters"]["limit"], 500)
        response, payload = self._get(INPATIENTS, self.doctor_user, limit=0)
        self.assertEqual(response.status_code, 400, payload)

    def test_only_doctor_desk_roles_open_either_endpoint(self):
        for user in (self.receptionist, self.nurse_a, self.front_desk, *self.denied.values()):
            for url, params in ((INPATIENTS, {}), (SEARCH, {"q": "ab"})):
                response, payload = self._get(url, user, **params)
                self.assertEqual(response.status_code, 403, (user.login, url, payload))
                self.assertEqual(payload["error"]["code"], "access_denied")

    # ------------------------------------------------------------------
    # 2. The row
    # ------------------------------------------------------------------
    def test_row_names_bed_stay_open_consultation_and_discharge_status(self):
        bed = self._new_bed()
        appointment, _encounter, admission = self._inpatient(bed)
        consultation = self._open_consultation(appointment)

        row = self._row(admission)
        inpatient = row["inpatient"]
        self.assertEqual(inpatient["reference"], admission.sudo().name)
        self.assertEqual(inpatient["state"], "admitted")
        self.assertEqual(inpatient["patient"]["id"], admission.sudo().patient_id.id)
        self.assertEqual(inpatient["location"]["bed"]["id"], bed.id)
        self.assertEqual(inpatient["location"]["ward"]["id"], bed.ward_id.id)
        self.assertTrue(inpatient["length_of_stay"]["ongoing"])

        held = inpatient["consultation"]
        self.assertEqual(held["reference"], consultation.name)
        self.assertEqual(held["doctor_name"], consultation.doctor_id.name)
        self.assertFalse(held["archived"])
        self.assertTrue(held["started_at"])

        discharge = inpatient["discharge"]
        self.assertFalse(discharge["medically_ready"])
        codes = {c["code"]: c for c in discharge["blocking"]}
        self.assertIn("not_medically_ready", codes)
        self.assertEqual(codes["consultation_open"]["consultation"]["reference"], consultation.name)
        self.assertFalse(discharge["can_request_discharge"], "Complete the consultation first.")
        self._assert_no_money(inpatient)

        # Selecting the row opens that visit in the centre panel, as any visit.
        detail = self._ok(DOCTOR_API + "/visits/%s" % row["visit"]["appointment_id"], self.doctor_user)
        self.assertEqual(detail["admission"]["admission"]["id"], admission.id)

    def test_row_offers_the_discharge_request_then_reports_ready(self):
        appointment, _encounter, admission = self._inpatient(self._new_bed())
        row = self._row(admission)["inpatient"]
        self.assertIsNone(row["consultation"])
        self.assertTrue(row["discharge"]["can_request_discharge"])

        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        row = self._row(admission)["inpatient"]
        self.assertTrue(row["discharge"]["medically_ready"])
        self.assertTrue(row["discharge"]["medical_ready_at"])
        self.assertFalse(row["discharge"]["can_request_discharge"])
        self.assertNotIn("not_medically_ready", [c["code"] for c in row["discharge"]["blocking"]])

    def test_an_archived_open_consultation_is_still_shown(self):
        appointment, _encounter, admission = self._inpatient(self._new_bed())
        consultation = self._open_consultation(appointment)
        consultation.sudo().write({"active": False})
        self.assertTrue(self._row(admission)["inpatient"]["consultation"]["archived"])

    # ------------------------------------------------------------------
    # 3. Search all my visits
    # ------------------------------------------------------------------
    def test_search_finds_my_visits_on_any_date_newest_first(self):
        name = "Srch %s" % uuid.uuid4().hex[:6]
        old = self._named_visit(name, days_ago=60, state="done")
        recent = self._named_visit(name, days_ago=3)
        data = self._search(name)
        self.assertEqual([r["appointment_id"] for r in data["rows"]], [recent.id, old.id])
        self.assertFalse(data["meta"]["truncated"])
        self.assertEqual(data["filters"]["q"], name)

    def test_search_matches_chart_number_appointment_code_and_consultation(self):
        visit = self._named_visit("Ids %s" % uuid.uuid4().hex[:6], days_ago=10)
        mrn = visit.sudo().patient_id.identification_code
        if mrn:
            self.assertIn(visit.id, self._search_ids(mrn))
        code = visit.sudo().appointment_code
        self.assertTrue(code)
        self.assertIn(visit.id, self._search_ids(code))

        # A consultation needs a real visit episode (_visit creates one).
        with_episode, _encounter = self._visit()
        consultation = self._open_consultation(with_episode)
        self.assertIn(with_episode.id, self._search_ids(consultation.name))

    def test_search_is_limited_to_my_visits(self):
        name = "Mine %s" % uuid.uuid4().hex[:6]
        mine = self._named_visit(name, days_ago=5)
        theirs = self._named_visit(name, doctor=self.other_doctor, days_ago=5)
        self.assertEqual(self._search_ids(name), [mine.id])
        self.assertEqual(self._search_ids(name, self.other_doctor_user), [theirs.id])
        self.assertEqual(set(self._search_ids(name, self.manager)), {mine.id, theirs.id})

    def test_search_excludes_draft_and_cancelled_visits(self):
        name = "State %s" % uuid.uuid4().hex[:6]
        kept = self._named_visit(name, days_ago=2)
        self._named_visit(name, days_ago=2, state="draft")
        self._named_visit(name, days_ago=2, state="cancelled")
        self.assertEqual(self._search_ids(name), [kept.id])

    def test_search_needs_two_letters_and_caps_its_limit(self):
        for q in ("", " ", "a", " b "):
            response, payload = self._get(SEARCH, self.doctor_user, q=q)
            self.assertEqual(response.status_code, 400, payload)
            self.assertEqual(payload["error"]["code"], "invalid_parameter")

        name = "Cap %s" % uuid.uuid4().hex[:6]
        for days in (1, 2, 3):
            self._named_visit(name, days_ago=days)
        data = self._search(name, limit=2)
        self.assertEqual(len(data["rows"]), 2)
        self.assertTrue(data["meta"]["truncated"])
        self.assertEqual(self._search(name, limit=100000)["filters"]["limit"], 100)

    # ------------------------------------------------------------------
    # 4. The dated worklist is unchanged; the indexes exist
    # ------------------------------------------------------------------
    def test_dated_worklist_still_bounds_search_to_the_day(self):
        name = "Day %s" % uuid.uuid4().hex[:6]
        # Midday UTC sits inside the same hospital day in any nearby timezone.
        old = self._named_visit(name, when=fields.Datetime.to_datetime("2026-01-05 12:00:00"))
        on_day = self._named_visit(name, when=fields.Datetime.to_datetime("2026-02-15 12:00:00"))
        data = self._ok(WORKLIST, self.doctor_user, date="2026-02-15", q=name)
        ids = [r["appointment_id"] for r in data["rows"]]
        self.assertEqual(data["filters"]["date"], "2026-02-15")
        self.assertIn(on_day.id, ids)
        self.assertNotIn(old.id, ids)
        # Without a date the search mode, not the worklist, finds both.
        self.assertEqual(set(self._search_ids(name)), {old.id, on_day.id})

    def test_the_doctor_desk_indexes_exist(self):
        self.env.cr.execute(
            "SELECT indexname FROM pg_indexes WHERE indexname IN %s",
            (("hospital_appointment_doctor_date_idx", "hospital_admission_physician_appointment_idx"),),
        )
        self.assertEqual(len(self.env.cr.fetchall()), 2)

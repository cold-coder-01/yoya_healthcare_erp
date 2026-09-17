"""Radiology Desk Slice 1: the READ-ONLY imaging workstation API.

WHAT THESE TESTS ARE FOR
------------------------
  1. THE GATE. Exactly Radiology Technician, Radiologist, Manager and System
     Administrator open the desk. Every other hospital role -- including the
     Doctor, who orders studies, and the Lab Technician, who used to be the
     imaging bench -- gets 403 from every route, the session route included.

  2. THE LANES. Each lane is derived from request state, the one operational
     report's state and hospital_billing's `billing_blocked` boolean, and a
     request with more than one operational report is an anomaly rather than a
     silently chosen report.

  3. THE COUNTS describe the whole filter scope (date, search, modality), never
     the page and never the selected lane.

  4. CONFIDENTIALITY. Only `billing_blocked` crosses from billing. No amount, no
     invoice, no receipt, no charge, no clearance message -- for Manager and
     Admin exactly as for a technician.

  5. NOTHING ELSE MOVED. The Doctor Desk still sees a radiology report and its
     images only once released, and the desk's routes accept no write.

HOW THE FIXTURES ARE BUILT
--------------------------
Orders go through the real Doctor Desk endpoint, so billing is real: charges are
raised by hospital_billing at confirmation, and clearance comes from a real
cashier payment. Workflow states beyond `requested` and report records are set
with sudo writes -- the same convention the Doctor results suites use -- because
this slice tests how the desk READS those states, not how they are reached.

ISOLATION. Every count and search is scoped by the patient name the fixture
created (a random suffix), so these tests read only their own rows and hold on a
database that already carries real radiology data.
"""
import base64
import json
import uuid
from datetime import date
from urllib.parse import urlencode

from odoo.tests import tagged

from ..controllers.radiology import SUMMARY_WORK_SCAN_MAX
from ..services.rad_desk_serializers import (
    RAD_DESK_ACTIVE_LANES,
    RAD_DESK_LANES,
    RESULT_CONFLICT_MESSAGE,
)
from ..services.reception_scope import RAD_DESK_GROUPS, may_rad_desk
from .test_doctor_radiology_api import RadiologyCase

SESSION = "/yoya-emr/api/v1/radiology/session"
WORKLIST = "/yoya-emr/api/v1/radiology/worklist"
DETAIL = "/yoya-emr/api/v1/radiology/requests/%s"
DOCTOR_RESULTS = "/yoya-emr/api/v1/doctor/visits/%s/results"
DOCTOR_IMAGE = "/yoya-emr/api/v1/doctor/visits/%s/results/images/%s"

G_RADIOLOGY_TECHNICIAN = "hospital_radiology.group_hospital_radiology_technician"
G_RADIOLOGIST = "hospital_radiology.group_hospital_radiologist"
G_LAB_TECHNICIAN = "hospital_management.group_hospital_lab_technician"
G_PHARMACIST = "hospital_management.group_hospital_pharmacist"
G_INSURANCE_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_DPO = "hospital_management.group_hospital_data_protection_officer"
G_SYSADMIN = "hospital_management.group_hospital_system_administrator"

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

# Anything that would mean a financial value, a billing object or an upstream
# file handle reached the desk. Checked against every KEY in a payload, and the
# message/handle markers against the whole serialized text.
FORBIDDEN_KEY_FRAGMENTS = (
    "amount", "invoice", "receipt", "charge", "payer", "price", "tariff",
    "balance", "payment", "clearance_message", "allocation", "account",
)
FORBIDDEN_TEXT = (
    "billing_clearance_message", "/web/content", "/web/image", "access_token",
    "ir.attachment", "datas", "Patient-payable",
)


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


class RadDeskCase(RadiologyCase):
    """RadiologyCase plus every role the Radiology Desk gate must decide on."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tech_password = "rad-desk-tech-pw"
        cls.rad_tech = cls._make_user("rd_tech", cls.tech_password, [G_RADIOLOGY_TECHNICIAN])
        cls.radiologist_password = "rad-desk-radiologist-pw"
        cls.radiologist = cls._make_user(
            "rd_radiologist", cls.radiologist_password, [G_RADIOLOGIST]
        )
        cls.sysadmin_password = "rad-desk-admin-pw"
        cls.sysadmin = cls._make_user("rd_admin", cls.sysadmin_password, [G_SYSADMIN])
        cls.lab_password = "rad-desk-lab-pw"
        cls.lab_tech = cls._make_user("rd_lab", cls.lab_password, [G_LAB_TECHNICIAN])
        cls.pharmacist_password = "rad-desk-pharm-pw"
        cls.pharmacist = cls._make_user("rd_pharm", cls.pharmacist_password, [G_PHARMACIST])
        cls.officer_password = "rad-desk-officer-pw"
        cls.officer = cls._make_user("rd_officer", cls.officer_password, [G_INSURANCE_OFFICER])
        cls.dpo_password = "rad-desk-dpo-pw"
        cls.dpo = cls._make_user("rd_dpo", cls.dpo_password, [G_DPO])

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _desk_get(self, url, user=None, password=None, **params):
        """GET as a desk user. Parameters are URL-encoded (patient names have
        spaces), and the session is re-authenticated on every call because
        HttpCase shares one session across a class."""
        self._auth(user or self.rad_tech, password or self.tech_password)
        query = urlencode({key: value for key, value in params.items() if value is not None})
        response = self.url_open("%s%s" % (url, "?" + query if query else ""))
        return response, json.loads(response.text)

    def _worklist(self, user=None, password=None, **params):
        response, payload = self._desk_get(WORKLIST, user, password, **params)
        self.assertEqual(response.status_code, 200, payload)
        return payload["data"]

    def _detail(self, record, user=None, password=None):
        response, payload = self._desk_get(DETAIL % record.id, user, password)
        self.assertEqual(response.status_code, 200, payload)
        return payload["data"]["request"]

    @staticmethod
    def _rows(data):
        return {row["id"]: row for row in data["rows"]}

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    def _visit(self):
        appointment, encounter = self._in_consultation_visit(doctor=self.doctor)
        return appointment, encounter, appointment.patient_id

    def _new_order(self, appointment, exams=None, **extra):
        """Place one order through the Doctor Desk and return THAT request."""
        consultation = self._consultation_for(appointment)
        before = set(self._requests_of(consultation).ids)
        response, payload = self._order(appointment, exams=exams, **extra)
        self.assertEqual(response.status_code, 200, payload)
        created = self._requests_of(consultation).filtered(lambda r: r.id not in before)
        self.assertEqual(len(created), 1)
        return created

    def _set_state(self, record, state):
        record.sudo().write({"state": state})
        record.invalidate_recordset()
        return record

    def _report(self, record, state="draft", **values):
        """A report on `record`, driven to `state` by fixture writes."""
        result = self.env["hospital.radiology.result"].sudo().create(
            dict({"request_id": record.id}, **values)
        )
        if state == "cancelled":
            # Cancelled from draft, the way the model permits. Walking the
            # report to released first would hit the release freeze.
            result.sudo().write({"state": "cancelled"})
        else:
            for step in ("entered", "validated", "released"):
                if result.state == state:
                    break
                result.sudo().write({"state": step})
        self.assertEqual(result.state, state)
        return result

    def _image(self, result, name="Axial"):
        return self.env["hospital.radiology.image"].sudo().create({
            "result_id": result.id,
            "name": name,
            "filename": "axial.png",
            "file": PNG,
        })

    def _legacy_request(self, state="in_progress", lines=True, **values):
        """A department-raised row with no appointment, encounter, consultation
        or charges -- the legacy shape the discovery found in UAT."""
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "RD Legacy %s" % suffix}
        )
        doctor = self.env["hospital.doctor"].sudo().create(
            {"name": "RD Legacy Doctor %s" % suffix}
        )
        vals = {"patient_id": patient.id, "physician_id": doctor.id}
        if lines:
            vals["line_ids"] = [(0, 0, {"exam_id": self.chest_xray.id})]
        vals.update(values)
        record = self.env["hospital.radiology.request"].sudo().create(vals)
        if state != "draft":
            self._set_state(record, state)
        return record, patient


# ===========================================================================
# 1. Authorization
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyDeskAuthorization(RadDeskCase):

    def _allowed(self, user, password):
        record, _patient = self._legacy_request()
        for url in (SESSION, WORKLIST, DETAIL % record.id):
            response, payload = self._desk_get(url, user, password)
            self.assertEqual(response.status_code, 200, (user.login, url, payload))
            self.assertTrue(payload["success"])

    def _denied(self, user, password):
        record, _patient = self._legacy_request()
        for url in (SESSION, WORKLIST, DETAIL % record.id):
            response, payload = self._desk_get(url, user, password)
            self.assertEqual(response.status_code, 403, (user.login, url))
            self.assertEqual(payload["error"]["code"], "radiology_desk_not_authorized")
            # A refused caller learns nothing about the record.
            self.assertNotIn(record.name, response.text)

    def test_01_radiology_technician_allowed(self):
        self._allowed(self.rad_tech, self.tech_password)

    def test_02_radiologist_allowed(self):
        self._allowed(self.radiologist, self.radiologist_password)

    def test_03_manager_allowed(self):
        self._allowed(self.manager, self.manager_password)

    def test_04_system_administrator_allowed(self):
        self._allowed(self.sysadmin, self.sysadmin_password)

    def test_05_lab_technician_denied(self):
        self._denied(self.lab_tech, self.lab_password)

    def test_06_doctor_denied(self):
        self._denied(self.doctor_user, self.doctor_password)

    def test_07_nurse_denied(self):
        self._denied(self.nurse, self.nurse_password)

    def test_08_receptionist_denied(self):
        self._denied(self.receptionist, self.receptionist_password)

    def test_09_front_desk_nurse_denied(self):
        self._denied(self.front_desk, self.fd_password)

    def test_10_cashier_denied(self):
        self._denied(self.cashier, self.cashier_password)

    def test_11_pharmacist_denied(self):
        self._denied(self.pharmacist, self.pharmacist_password)

    def test_12_accountant_denied(self):
        self._denied(self.accountant, self.accountant_password)

    def test_13_insurance_officer_denied(self):
        self._denied(self.officer, self.officer_password)

    def test_14_dpo_denied(self):
        self._denied(self.dpo, self.dpo_password)

    def test_15_unauthenticated_never_reaches_the_endpoint(self):
        """auth="user" REDIRECTS a public caller; the assertion is on that."""
        record, _patient = self._legacy_request()
        self.authenticate(None, None)
        for url in (SESSION, WORKLIST, DETAIL % record.id):
            response = self.url_open(url, allow_redirects=False)
            self.assertIn(response.status_code, (301, 302, 303, 401, 403), url)
            self.assertNotIn(record.name, response.text or "")

    def test_16_the_gate_tuple_is_exactly_the_radiology_roles(self):
        self.assertEqual(
            RAD_DESK_GROUPS,
            (
                "hospital_radiology.group_hospital_radiology_technician",
                "hospital_radiology.group_hospital_radiologist",
                "hospital_management.group_hospital_manager",
                "hospital_management.group_hospital_system_administrator",
            ),
        )
        self.assertFalse(may_rad_desk(self.env(user=self.lab_tech)))
        self.assertFalse(may_rad_desk(self.env(user=self.doctor_user)))

    def test_17_the_routes_accept_no_write(self):
        record, _patient = self._legacy_request()
        for url in (SESSION, WORKLIST, DETAIL % record.id):
            self._auth(self.rad_tech, self.tech_password)
            response = self.url_open(
                url, data="{}", headers={"Content-Type": "application/json"}
            )
            self.assertIn(response.status_code, (404, 405), url)

    def test_18_no_mutation_route_exists_under_radiology(self):
        record, _patient = self._legacy_request(state="requested")
        for suffix in ("schedule", "start", "report", "result", "cancel", "images"):
            self._auth(self.rad_tech, self.tech_password)
            response = self.url_open(
                "%s/%s" % (DETAIL % record.id, suffix),
                data="{}",
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(response.status_code, 404, suffix)
        record.invalidate_recordset()
        self.assertEqual(record.state, "requested")


# ===========================================================================
# 2. Session
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyDeskSession(RadDeskCase):

    def test_20_session_returns_safe_identity_and_role_flags(self):
        for user, password, expected in (
            (self.rad_tech, self.tech_password,
             {"radiology_technician": True, "radiologist": False,
              "manager": False, "system_admin": False}),
            (self.radiologist, self.radiologist_password,
             {"radiology_technician": False, "radiologist": True,
              "manager": False, "system_admin": False}),
            (self.manager, self.manager_password,
             {"radiology_technician": False, "radiologist": False,
              "manager": True, "system_admin": False}),
            (self.sysadmin, self.sysadmin_password,
             {"radiology_technician": False, "radiologist": False,
              "manager": True, "system_admin": True}),
        ):
            response, payload = self._desk_get(SESSION, user, password)
            self.assertEqual(response.status_code, 200)
            data = payload["data"]
            self.assertEqual(data["user"], {"id": user.id, "name": user.name})
            self.assertEqual(data["roles"], expected, user.login)
            self.assertEqual(data["capabilities"], {"radiology_desk": True})

    def test_21_session_exposes_no_unrelated_security_metadata(self):
        _response, payload = self._desk_get(SESSION)
        data = payload["data"]
        self.assertEqual(set(data), {"user", "company", "roles", "capabilities"})
        self.assertEqual(set(data["user"]), {"id", "name"})
        text = json.dumps(data)
        for marker in ("groups", "group_id", "login", "lab_technician", "doctor",
                       "cashier", "receptionist"):
            self.assertNotIn(marker, text)


# ===========================================================================
# 3. Lanes
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyDeskLanes(RadDeskCase):

    def _lane_of(self, record, patient, **params):
        data = self._worklist(q=patient.name, status=",".join(RAD_DESK_LANES), **params)
        row = self._rows(data).get(record.id)
        self.assertIsNotNone(row, "request %s missing from the queue" % record.name)
        return row

    def test_30_requested_and_blocked_is_awaiting_clearance(self):
        appointment, _encounter, patient = self._visit()
        record = self._new_order(appointment)
        self.assertTrue(record.billing_blocked)
        row = self._lane_of(record, patient)
        self.assertEqual((row["state"], row["lane"]), ("requested", "awaiting_clearance"))
        self.assertTrue(row["billing_blocked"])

    def test_31_scheduled_and_blocked_is_awaiting_clearance(self):
        appointment, _encounter, patient = self._visit()
        record = self._set_state(self._new_order(appointment), "scheduled")
        row = self._lane_of(record, patient)
        self.assertEqual((row["state"], row["lane"]), ("scheduled", "awaiting_clearance"))

    def test_32_requested_and_clear_is_to_schedule(self):
        appointment, encounter, patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        record.invalidate_recordset()
        row = self._lane_of(record, patient)
        self.assertEqual((row["state"], row["lane"]), ("requested", "to_schedule"))
        self.assertFalse(row["billing_blocked"])

    def test_33_scheduled_and_clear_is_ready_to_start(self):
        appointment, encounter, patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        self._set_state(record, "scheduled")
        row = self._lane_of(record, patient)
        self.assertEqual((row["state"], row["lane"]), ("scheduled", "ready_to_start"))

    def _in_progress(self):
        appointment, encounter, patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        return self._set_state(record, "in_progress"), patient, appointment

    def test_34_in_progress_without_a_report_is_awaiting_report(self):
        record, patient, _appointment = self._in_progress()
        row = self._lane_of(record, patient)
        self.assertEqual(row["lane"], "awaiting_report")
        self.assertIsNone(row["result"])
        self.assertFalse(row["result_conflict"])

    def test_35_in_progress_with_a_draft_report_is_awaiting_report(self):
        record, patient, _appointment = self._in_progress()
        report = self._report(record, "draft")
        row = self._lane_of(record, patient)
        self.assertEqual(row["lane"], "awaiting_report")
        self.assertEqual(row["result"]["id"], report.id)
        self.assertEqual(row["result"]["state"], "draft")

    def test_36_in_progress_with_an_entered_report_is_awaiting_validation(self):
        record, patient, _appointment = self._in_progress()
        self._report(record, "entered")
        self.assertEqual(self._lane_of(record, patient)["lane"], "awaiting_validation")

    def test_37_in_progress_with_a_validated_report_is_awaiting_release(self):
        record, patient, _appointment = self._in_progress()
        self._report(record, "validated")
        self.assertEqual(self._lane_of(record, patient)["lane"], "awaiting_release")

    def test_38_completed_is_completed(self):
        record, patient, _appointment = self._in_progress()
        self._report(record, "released")
        self._set_state(record, "completed")
        row = self._lane_of(record, patient)
        self.assertEqual((row["state"], row["lane"]), ("completed", "completed"))

    def test_39_in_progress_with_a_released_report_is_an_anomaly(self):
        record, patient, _appointment = self._in_progress()
        self._report(record, "released")
        row = self._lane_of(record, patient)
        self.assertEqual(row["lane"], "anomaly")
        self.assertEqual(row["anomaly_reason"], "released_not_completed")
        self.assertFalse(row["result_conflict"])

    def test_40_multiple_active_reports_is_an_anomaly_and_a_conflict(self):
        record, patient, _appointment = self._in_progress()
        self._report(record, "draft")
        self._report(record, "entered")
        row = self._lane_of(record, patient)
        self.assertEqual(row["lane"], "anomaly")
        self.assertEqual(row["anomaly_reason"], "result_conflict")
        self.assertTrue(row["result_conflict"])
        self.assertIsNone(row["result"], "no report may be presented as authoritative")
        self.assertEqual(row["result_count"], 2)

    def test_41_cancelled_and_archived_reports_do_not_make_a_conflict(self):
        record, patient, _appointment = self._in_progress()
        self._report(record, "cancelled")
        archived = self._report(record, "draft")
        archived.sudo().write({"active": False})
        current = self._report(record, "entered")
        row = self._lane_of(record, patient)
        self.assertFalse(row["result_conflict"])
        self.assertEqual(row["result"]["id"], current.id)
        self.assertEqual(row["lane"], "awaiting_validation")

    def test_42_a_conflict_on_a_completed_request_is_reviewed_not_hidden(self):
        record, patient, _appointment = self._in_progress()
        self._report(record, "released")
        self._report(record, "draft")
        self._set_state(record, "completed")
        # Asked for the ACTIVE lanes only: it must be found through the anomaly
        # lane even though its state is completed.
        data = self._worklist(q=patient.name)
        row = self._rows(data).get(record.id)
        self.assertIsNotNone(row)
        self.assertEqual(row["lane"], "anomaly")
        self.assertEqual(data["summary"]["anomaly"], 1)
        self.assertEqual(data["summary"]["completed"], 0)

    def test_43_draft_requests_never_appear(self):
        record, patient = self._legacy_request(state="draft")
        data = self._worklist(q=patient.name, status=",".join(RAD_DESK_LANES))
        self.assertNotIn(record.id, self._rows(data))
        response, payload = self._desk_get(WORKLIST, status="draft")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_status")

    def test_44_cancelled_is_excluded_by_default_and_available_by_name(self):
        appointment, _encounter, patient = self._visit()
        record = self._new_order(appointment)
        record.sudo().action_cancel()
        record.invalidate_recordset()
        self.assertEqual(record.state, "cancelled")
        self.assertNotIn(record.id, self._rows(self._worklist(q=patient.name)))
        row = self._rows(self._worklist(q=patient.name, status="cancelled")).get(record.id)
        self.assertEqual(row["lane"], "cancelled")

    def test_45_default_lanes_are_the_active_lanes(self):
        data = self._worklist()
        self.assertEqual(data["filters"]["status"], list(RAD_DESK_ACTIVE_LANES))
        self.assertEqual(data["meta"]["default_lanes"], list(RAD_DESK_ACTIVE_LANES))
        self.assertNotIn("completed", RAD_DESK_ACTIVE_LANES)
        self.assertNotIn("draft", RAD_DESK_LANES)

    def test_46_unknown_lane_is_refused(self):
        response, payload = self._desk_get(WORKLIST, status="to_schedule,made_up")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_status")


# ===========================================================================
# 4. Filters
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyDeskFilters(RadDeskCase):

    def test_50_search_by_request_code(self):
        appointment, _encounter, _patient = self._visit()
        record = self._new_order(appointment)
        other = self._new_order(appointment)
        rows = self._rows(self._worklist(q=record.name))
        self.assertIn(record.id, rows)
        self.assertNotIn(other.id, rows)

    def test_51_search_by_patient_name(self):
        appointment, _encounter, patient = self._visit()
        record = self._new_order(appointment)
        self.assertIn(record.id, self._rows(self._worklist(q=patient.name)))

    def test_52_search_by_mrn(self):
        appointment, _encounter, patient = self._visit()
        record = self._new_order(appointment)
        self.assertTrue(patient.identification_code)
        rows = self._rows(self._worklist(q=patient.identification_code))
        self.assertIn(record.id, rows)
        self.assertEqual(rows[record.id]["patient"]["mrn"], patient.identification_code)

    def test_53_search_by_exam_name_and_code(self):
        appointment, _encounter, _patient = self._visit()
        xray = self._new_order(appointment, exams=[self.chest_xray])
        ct = self._new_order(appointment, exams=[self.ct_brain])
        for term in (self.chest_xray.name, self.chest_xray.code):
            rows = self._rows(self._worklist(q=term))
            self.assertIn(xray.id, rows, term)
            self.assertNotIn(ct.id, rows, term)

    def test_54_search_by_ordering_doctor(self):
        appointment, _encounter, _patient = self._visit()
        record = self._new_order(appointment)
        self.assertIn(record.id, self._rows(self._worklist(q=self.doctor.name)))

    def test_55_modality_filter(self):
        appointment, _encounter, patient = self._visit()
        xray = self._new_order(appointment, exams=[self.chest_xray])
        ct = self._new_order(appointment, exams=[self.ct_brain])
        rows = self._rows(self._worklist(q=patient.name, modality="xray"))
        self.assertIn(xray.id, rows)
        self.assertNotIn(ct.id, rows)
        self.assertEqual(rows[xray.id]["modality"], "xray")
        self.assertEqual(rows[xray.id]["modality_label"], "X-Ray")

    def test_56_modality_values_come_from_the_model(self):
        data = self._worklist()
        values = [option["value"] for option in data["meta"]["modalities"]]
        expected = [
            value for value, _label in self.env["hospital.radiology.exam"]
            ._fields["modality"]._description_selection(self.env)
        ]
        self.assertEqual(values, expected)
        response, payload = self._desk_get(WORKLIST, modality="pet")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_modality")

    def test_57_exact_date_filter(self):
        appointment, _encounter, patient = self._visit()
        old = self._new_order(appointment)
        today = self._new_order(appointment)
        old.sudo().write({"request_date": date(2020, 1, 2)})
        rows = self._rows(self._worklist(q=patient.name, date="2020-01-02"))
        self.assertIn(old.id, rows)
        self.assertNotIn(today.id, rows)
        response, payload = self._desk_get(WORKLIST, date="02/01/2020")
        self.assertEqual(response.status_code, 400)

    def test_58_combined_filters(self):
        appointment, _encounter, patient = self._visit()
        target = self._new_order(appointment, exams=[self.chest_xray])
        wrong_day = self._new_order(appointment, exams=[self.chest_xray])
        wrong_modality = self._new_order(appointment, exams=[self.ct_brain])
        for record in (target, wrong_modality):
            record.sudo().write({"request_date": date(2021, 3, 4)})
        data = self._worklist(
            q=patient.name, modality="xray", date="2021-03-04",
            status="awaiting_clearance",
        )
        self.assertEqual(set(self._rows(data)), {target.id})
        self.assertNotIn(wrong_day.id, self._rows(data))
        self.assertEqual(data["summary"]["awaiting_clearance"], 1)


# ===========================================================================
# 5. Counts
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyDeskSummary(RadDeskCase):

    def _one_of_each(self):
        """Seven requests of ONE patient, one per lane (awaiting_release aside).

        Six are ordered and paid; the seventh is ordered after payment, so its
        own charge is unpaid and it alone reads awaiting clearance.
        """
        appointment, encounter, patient = self._visit()
        to_schedule = self._new_order(appointment)
        ready = self._new_order(appointment)
        report = self._new_order(appointment)
        validation = self._new_order(appointment)
        completed = self._new_order(appointment)
        conflict = self._new_order(appointment)
        self._settle(encounter)
        blocked = self._new_order(appointment)

        self._set_state(ready, "scheduled")
        self._set_state(report, "in_progress")
        self._set_state(validation, "in_progress")
        self._report(validation, "entered")
        self._set_state(completed, "in_progress")
        self._report(completed, "released")
        self._set_state(completed, "completed")
        self._set_state(conflict, "in_progress")
        self._report(conflict, "draft")
        self._report(conflict, "draft")
        return patient, {
            "awaiting_clearance": blocked, "to_schedule": to_schedule,
            "ready_to_start": ready, "awaiting_report": report,
            "awaiting_validation": validation, "completed": completed,
            "anomaly": conflict,
        }

    def test_60_the_summary_is_authoritative(self):
        patient, records = self._one_of_each()
        data = self._worklist(q=patient.name, status=",".join(RAD_DESK_LANES))
        summary = data["summary"]
        for lane in records:
            self.assertEqual(summary[lane], 1, lane)
        self.assertEqual(summary["awaiting_release"], 0)
        self.assertEqual(summary["cancelled"], 0)
        self.assertEqual(summary["active"], 6)
        self.assertTrue(data["meta"]["summary_exact"])
        rows = self._rows(data)
        for lane, record in records.items():
            self.assertEqual(rows[record.id]["lane"], lane)

    def test_61_the_summary_is_not_the_page(self):
        patient, _records = self._one_of_each()
        data = self._worklist(q=patient.name, status="completed", limit=1)
        self.assertEqual(len(data["rows"]), 1)
        self.assertEqual(data["meta"]["row_count"], 1)
        # Every lane still counted, though only the completed lane was asked
        # for and only one row fitted.
        self.assertEqual(data["summary"]["awaiting_clearance"], 1)
        self.assertEqual(data["summary"]["active"], 6)

    def test_62_the_summary_respects_date_search_and_modality(self):
        patient, records = self._one_of_each()
        records["to_schedule"].sudo().write({"request_date": date(2019, 5, 6)})
        dated = self._worklist(q=patient.name, date="2019-05-06")["summary"]
        self.assertEqual(dated["to_schedule"], 1)
        self.assertEqual(dated["active"], 1)
        self.assertEqual(dated["completed"], 0)
        # Every fixture ordered CT, so an x-ray scope counts nothing.
        xray = self._worklist(q=patient.name, modality="xray")["summary"]
        self.assertEqual(xray["active"], 0)
        self.assertEqual(xray["completed"], 0)
        data = self._worklist(q=patient.name)
        self.assertEqual(data["meta"]["summary_filters"], ["date", "q", "modality"])

    def test_63_the_summary_ignores_the_selected_lane(self):
        patient, _records = self._one_of_each()
        one = self._worklist(q=patient.name, status="to_schedule")["summary"]
        other = self._worklist(q=patient.name, status="anomaly")["summary"]
        self.assertEqual(one, other)

    def test_64_the_scan_cap_is_bounded(self):
        self.assertGreater(SUMMARY_WORK_SCAN_MAX, 0)
        self.assertLessEqual(SUMMARY_WORK_SCAN_MAX, 5000)


# ===========================================================================
# 6. Detail
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyDeskDetail(RadDeskCase):

    def test_70_detail_is_the_correct_request(self):
        appointment, _encounter, patient = self._visit()
        record = self._new_order(appointment, priority="urgent")
        detail = self._detail(record)
        self.assertEqual(detail["id"], record.id)
        self.assertEqual(detail["request_code"], record.name)
        self.assertEqual(detail["patient"]["id"], patient.id)
        self.assertEqual(detail["patient"]["mrn"], patient.identification_code)
        self.assertEqual(detail["ordering_physician"]["id"], self.doctor.id)
        self.assertEqual(detail["priority"], "urgent")
        self.assertEqual(detail["lane"], "awaiting_clearance")
        self.assertTrue(detail["ordered_from_consultation"])

    def test_71_detail_lists_the_ordered_exams(self):
        appointment, _encounter, _patient = self._visit()
        record = self._new_order(appointment, exams=[self.ct_brain, self.chest_xray])
        exams = self._detail(record)["exams"]
        self.assertEqual([exam["exam_id"] for exam in exams],
                         [self.ct_brain.id, self.chest_xray.id])
        first = exams[0]
        self.assertEqual(first["request_line_id"], record.line_ids[0].id)
        self.assertEqual(first["code"], self.ct_brain.code)
        self.assertEqual(first["modality"], "ct")
        self.assertEqual(first["modality_label"], "CT Scan")
        self.assertEqual(first["body_part"], "Head")
        self.assertTrue(first["contrast_required"])
        self.assertEqual(first["state"], "active")

    def test_72_detail_carries_indication_and_instructions(self):
        appointment, _encounter, _patient = self._visit()
        record = self._new_order(
            appointment,
            clinical_indication="Sudden severe headache, rule out bleed.",
            instructions="Non-contrast first.",
        )
        detail = self._detail(record)
        self.assertEqual(detail["clinical_indication"], "Sudden severe headache, rule out bleed.")
        self.assertEqual(detail["instructions"], "Non-contrast first.")

    def test_73_unreleased_report_text_is_shown_to_the_desk(self):
        appointment, encounter, _patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        self._set_state(record, "in_progress")
        report = self._report(
            record, "entered",
            findings="No acute intracranial haemorrhage.",
            impression="Normal CT brain.",
            recommendations="Clinical correlation.",
        )
        report.line_ids[:1].sudo().write({"result_summary": "Unremarkable"})
        result = self._detail(record)["result"]
        self.assertEqual(result["id"], report.id)
        self.assertEqual(result["state"], "entered")
        self.assertEqual(result["findings"], "No acute intracranial haemorrhage.")
        self.assertEqual(result["impression"], "Normal CT brain.")
        self.assertEqual(result["recommendations"], "Clinical correlation.")
        self.assertTrue(result["has_report"])
        self.assertEqual(result["lines"][0]["result_summary"], "Unremarkable")
        self.assertIn("contrast_used", result["lines"][0])

    def test_74_image_metadata_only(self):
        appointment, encounter, _patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        self._set_state(record, "in_progress")
        report = self._report(record, "draft")
        image = self._image(report, name="Axial slice")
        response, payload = self._desk_get(DETAIL % record.id)
        images = payload["data"]["request"]["result"]["images"]
        self.assertEqual(len(images), 1)
        self.assertEqual(
            set(images[0]),
            {"id", "name", "caption", "image_type", "image_type_label", "filename",
             "mimetype", "file_size", "uploaded_by", "uploaded_at", "sequence"},
        )
        self.assertEqual(images[0]["id"], image.id)
        self.assertEqual(images[0]["mimetype"], "image/png")
        self.assertEqual(images[0]["file_size"], image.file_size)
        self.assertEqual(payload["data"]["request"]["result"]["image_count"], 1)

    def test_75_no_image_bytes_or_urls(self):
        appointment, encounter, _patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        self._set_state(record, "in_progress")
        self._image(self._report(record, "draft"))
        response, _payload = self._desk_get(DETAIL % record.id)
        for marker in FORBIDDEN_TEXT:
            self.assertNotIn(marker, response.text, marker)
        self.assertNotIn(PNG.decode(), response.text)
        self.assertNotIn('"file"', response.text)

    def test_76_a_conflict_is_stated_safely(self):
        appointment, encounter, _patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        self._set_state(record, "in_progress")
        self._report(record, "draft", findings="first text")
        self._report(record, "entered", findings="second text")
        response, payload = self._desk_get(DETAIL % record.id)
        detail = payload["data"]["request"]
        self.assertTrue(detail["result_conflict"])
        self.assertIsNone(detail["result"])
        self.assertEqual(detail["lane"], "anomaly")
        self.assertEqual(detail["review_message"], RESULT_CONFLICT_MESSAGE)
        # Neither report's text is presented, and no internal detail leaks.
        for text in ("first text", "second text", "RADRES", "hospital.radiology"):
            self.assertNotIn(text, response.text)

    def test_77_malformed_legacy_requests_are_read_safe(self):
        # No lines, no appointment, no encounter, no charges, physician with no
        # user, in progress with no report.
        bare, _patient = self._legacy_request(lines=False)
        detail = self._detail(bare)
        self.assertEqual(detail["lane"], "awaiting_report")
        self.assertEqual(detail["exams"], [])
        self.assertIsNone(detail["first_exam"])
        self.assertEqual(detail["exam_count"], 0)
        self.assertFalse(detail["ordered_from_consultation"])
        self.assertFalse(detail["billing_blocked"])
        self.assertIsNotNone(detail["ordering_physician"])

        # A released, EMPTY report on a request that never completed.
        stuck, patient = self._legacy_request()
        self._report(stuck, "released")
        detail = self._detail(stuck)
        self.assertEqual(detail["lane"], "anomaly")
        self.assertEqual(detail["anomaly_reason"], "released_not_completed")
        self.assertFalse(detail["result"]["has_report"])
        self.assertIsNotNone(detail["review_message"])
        row = self._rows(self._worklist(q=patient.name))[stuck.id]
        self.assertEqual(row["lane"], "anomaly")

        # A draft request opened by id still renders, without being queue work.
        draft, _patient = self._legacy_request(state="draft", lines=False)
        self.assertEqual(self._detail(draft)["lane"], "draft")

    def test_78_detail_is_404_for_missing_and_archived_requests(self):
        record, _patient = self._legacy_request()
        record.sudo().write({"active": False})
        for request_id in (record.id, 999999999):
            response, payload = self._desk_get(DETAIL % request_id)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(payload["error"]["code"], "radiology_request_not_found")

    def test_79_reads_never_mutate(self):
        appointment, encounter, _patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        self._set_state(record, "in_progress")
        report = self._report(record, "entered")
        before = (record.state, record.write_date, report.state, report.write_date,
                  len(record.result_ids))
        self._detail(record)
        self._worklist(q=record.name, status=",".join(RAD_DESK_LANES))
        record.invalidate_recordset()
        report.invalidate_recordset()
        self.assertEqual(
            before,
            (record.state, record.write_date, report.state, report.write_date,
             len(record.result_ids)),
        )


# ===========================================================================
# 7. Confidentiality
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyDeskConfidentiality(RadDeskCase):

    def _assert_money_free(self, payload, response_text, label):
        for key in _walk_keys(payload):
            lowered = key.lower()
            for fragment in FORBIDDEN_KEY_FRAGMENTS:
                self.assertNotIn(fragment, lowered, "%s: key %r" % (label, key))
        for marker in FORBIDDEN_TEXT:
            self.assertNotIn(marker, response_text, "%s: %s" % (label, marker))

    def _blocked_and_reported(self):
        appointment, encounter, patient = self._visit()
        blocked = self._new_order(appointment)
        reported = self._new_order(appointment)
        self._set_state(reported, "in_progress")
        self._image(self._report(reported, "entered", findings="text"))
        return patient, blocked, reported

    def test_80_no_amount_invoice_receipt_or_charge_for_the_technician(self):
        patient, blocked, reported = self._blocked_and_reported()
        self.assertTrue(blocked.sudo().charge_line_ids)
        for url, params in (
            (WORKLIST, {"q": patient.name, "status": ",".join(RAD_DESK_LANES)}),
            (DETAIL % blocked.id, {}),
            (DETAIL % reported.id, {}),
            (SESSION, {}),
        ):
            response, payload = self._desk_get(url, **params)
            self.assertEqual(response.status_code, 200)
            self._assert_money_free(payload, response.text, url)

    def test_81_billing_blocked_is_the_only_billing_value_and_is_a_boolean(self):
        _patient, blocked, _reported = self._blocked_and_reported()
        detail = self._detail(blocked)
        billing_keys = [key for key in _walk_keys(detail) if "billing" in key]
        self.assertEqual(billing_keys, ["billing_blocked"])
        self.assertIs(detail["billing_blocked"], True)

    def test_82_the_clearance_message_is_never_served(self):
        _patient, blocked, _reported = self._blocked_and_reported()
        message = blocked.sudo().billing_clearance_message
        response, _payload = self._desk_get(DETAIL % blocked.id)
        self.assertNotIn("billing_clearance_message", response.text)
        if message:
            self.assertNotIn(message, response.text)

    def test_83_manager_and_admin_receive_the_same_money_free_payload(self):
        patient, blocked, reported = self._blocked_and_reported()
        tech_detail = self._detail(blocked)
        for user, password in (
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            for url, params in (
                (WORKLIST, {"q": patient.name}),
                (DETAIL % blocked.id, {}),
                (DETAIL % reported.id, {}),
            ):
                response, payload = self._desk_get(url, user, password, **params)
                self.assertEqual(response.status_code, 200)
                self._assert_money_free(payload, response.text, user.login)
            self.assertEqual(self._detail(blocked, user, password), tech_detail)


# ===========================================================================
# 8. The Doctor Desk is unchanged
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyDeskDoctorRegression(RadDeskCase):

    def _doctor_view(self, appointment, record):
        response, payload = self._get(
            DOCTOR_RESULTS % appointment.id, self.doctor_user, self.doctor_password
        )
        self.assertEqual(response.status_code, 200)
        return next(
            entry for entry in payload["data"]["radiology"]
            if entry["request_id"] == record.id
        )

    def _reported_study(self, state):
        appointment, encounter, _patient = self._visit()
        record = self._new_order(appointment)
        self._settle(encounter)
        self._set_state(record, "in_progress")
        report = self._report(record, "draft", findings="Doctor must wait for this.")
        image = self._image(report)
        for step in ("entered", "validated", "released"):
            if report.state == state:
                break
            report.sudo().write({"state": step})
        return appointment, record, report, image

    def test_90_doctor_sees_only_released_reports(self):
        for state in ("draft", "entered", "validated"):
            appointment, record, _report, _image = self._reported_study(state)
            # The desk sees it...
            self.assertEqual(self._detail(record)["result"]["state"], state)
            # ...the doctor still does not.
            entry = self._doctor_view(appointment, record)
            self.assertEqual(entry["status"], "pending", state)
            self.assertIsNone(entry["result"], state)

        appointment, record, _report, _image = self._reported_study("released")
        entry = self._doctor_view(appointment, record)
        self.assertEqual(entry["status"], "available")
        self.assertEqual(entry["result"]["findings"], "Doctor must wait for this.")

    def test_91_doctor_image_bytes_only_once_released(self):
        appointment, _record, _report, image = self._reported_study("validated")
        self._auth(self.doctor_user, self.doctor_password)
        response = self.url_open(DOCTOR_IMAGE % (appointment.id, image.id))
        self.assertEqual(response.status_code, 404)

        appointment, _record, _report, image = self._reported_study("released")
        self._auth(self.doctor_user, self.doctor_password)
        response = self.url_open(DOCTOR_IMAGE % (appointment.id, image.id))
        self.assertEqual(response.status_code, 200)
        self.assertIn("image/png", response.headers.get("Content-Type", ""))

    def test_92_the_doctor_desk_still_refuses_the_radiology_roles(self):
        for user, password in (
            (self.rad_tech, self.tech_password),
            (self.radiologist, self.radiologist_password),
        ):
            response, _payload = self._get(
                "/yoya-emr/api/v1/doctor/worklist", user, password
            )
            self.assertEqual(response.status_code, 403, user.login)

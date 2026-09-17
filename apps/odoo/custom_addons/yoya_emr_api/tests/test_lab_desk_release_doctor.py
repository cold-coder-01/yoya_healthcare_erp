"""Laboratory Desk release (Slice 3C), seen from the Doctor Desk.

WHY A SEPARATE MODULE. The Lab Desk suite builds requests with no consultation,
and the Doctor Desk's Results tab is keyed on the consultation -- a request
without one can never appear there, so a visibility test built on those
fixtures would pass for the wrong reason. These tests order through the REAL
Doctor Desk endpoint on a visit that has genuinely started, then take the
request through the REAL Laboratory Desk API -- collect, start processing,
open, enter, validate, release -- exactly as a technician does, and read what
the ordering doctor sees at each step.

THE RULE UNDER TEST is result_serializers.released_results: released AND
active. Validated is not visible; released is, immediately, in the current
consultation's Results. History deliberately excludes the current encounter,
so the same visit's History must not show it.
"""
import json

from odoo.tests import tagged

from .test_doctor_history_api import HistoryCase

G_LAB_TECH = "hospital_management.group_hospital_lab_technician"

LAB_API = "/yoya-emr/api/v1/lab"


@tagged("post_install", "-at_install", "lab_desk")
class TestLabDeskReleaseDoctorVisibility(HistoryCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.lab_password = "lab-release-doctor-pw"
        cls.lab_tech = cls._make_user("lab_rel_doc", cls.lab_password, [G_LAB_TECH])

    # ------------------------------------------------------------------
    def _lab(self, path, body=None):
        self.authenticate(self.lab_tech.login, self.lab_password)
        response = self.url_open(
            LAB_API + path,
            data=json.dumps(body or {}),
            headers={"Content-Type": "application/json"},
        )
        return response, json.loads(response.text)

    def _validated_through_the_lab_desk(self):
        """Order as the doctor; run the whole bench as the technician."""
        appointment, _encounter = self._in_consultation_visit()
        request = self._lab_request(appointment)
        self.assertEqual(request.state, "requested")

        for step in ("collect", "start-processing"):
            response, payload = self._lab("/requests/%s/%s" % (request.id, step))
            self.assertEqual(response.status_code, 200, (step, payload))

        response, payload = self._lab("/requests/%s/result" % request.id)
        self.assertEqual(response.status_code, 200, payload)
        result = payload["data"]["result"]
        body = {
            "interpretation": "Mild leukocytosis.",
            "remarks": "Routine CBC result.",
            "lines": [{
                "id": result["lines"][0]["id"],
                "result_value": "WBC 11.8, Hgb 14.2, Plt 265",
                "unit": "mixed",
                "reference_range": "WBC 4.0-10.0, Hgb 13-17, Plt 150-400",
                "abnormal_flag": "high",
                "notes": "Entered on the Laboratory Desk.",
            }],
        }
        response, payload = self._lab("/results/%s/enter" % result["id"], body)
        self.assertEqual(response.status_code, 200, payload)
        response, payload = self._lab("/results/%s/validate" % result["id"])
        self.assertEqual(response.status_code, 200, payload)
        return appointment, request, result

    def _doctor_row(self, appointment, request):
        response, payload = self._results(appointment)
        self.assertEqual(response.status_code, 200, payload)
        rows = [row for row in self._lab_rows(payload) if row["request_id"] == request.id]
        self.assertEqual(len(rows), 1)
        return rows[0]

    # ------------------------------------------------------------------
    def test_700_before_release_the_doctor_sees_pending_and_no_values(self):
        appointment, request, _result = self._validated_through_the_lab_desk()
        row = self._doctor_row(appointment, request)
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["result"])
        self.assertNotIn("WBC 11.8", json.dumps(row))

    def test_701_after_release_the_doctor_sees_the_result_immediately(self):
        appointment, request, result = self._validated_through_the_lab_desk()
        response, payload = self._lab("/results/%s/release" % result["id"])
        self.assertEqual(response.status_code, 200, payload)
        self.assertTrue(payload["data"]["completion"]["completed"])

        row = self._doctor_row(appointment, request)
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["status_label"], "Result available")
        self.assertEqual(row["workflow_status"], "completed")
        released = row["result"]
        self.assertIsNotNone(released)
        self.assertEqual(released["id"], result["id"])
        self.assertEqual(released["result_code"], result["name"])
        self.assertTrue(released["result_date"])
        self.assertEqual(released["interpretation"], "Mild leukocytosis.")
        self.assertEqual(released["remarks"], "Routine CBC result.")

    def test_702_every_entered_value_reaches_the_doctor(self):
        appointment, request, result = self._validated_through_the_lab_desk()
        self._lab("/results/%s/release" % result["id"])
        line = self._doctor_row(appointment, request)["result"]["lines"][0]
        self.assertEqual(line["name"], self.cbc.name)
        self.assertEqual(line["code"], self.cbc.code)
        self.assertEqual(line["sample_type"], "blood")
        self.assertEqual(line["value"], "WBC 11.8, Hgb 14.2, Plt 265")
        self.assertEqual(line["unit"], "mixed")
        self.assertEqual(line["reference_range"], "WBC 4.0-10.0, Hgb 13-17, Plt 150-400")
        self.assertEqual(line["abnormal_flag"], "high")
        self.assertEqual(line["abnormal_flag_label"], "High")
        self.assertEqual(line["notes"], "Entered on the Laboratory Desk.")
        self.assertTrue(line["ordered"])

    def test_703_the_same_visits_history_does_not_show_it(self):
        """History is prior episodes only; the current encounter is excluded."""
        appointment, _request, result = self._validated_through_the_lab_desk()
        self._lab("/results/%s/release" % result["id"])
        response, payload = self._history(appointment)
        self.assertEqual(response.status_code, 200, payload)
        blob = json.dumps(payload)
        self.assertNotIn(result["name"], blob)
        self.assertNotIn("WBC 11.8", blob)

    def test_704_a_refused_release_leaves_the_doctor_seeing_pending(self):
        appointment, request, result = self._validated_through_the_lab_desk()
        self.env["hospital.laboratory.result"].sudo().create({"request_id": request.id})
        response, payload = self._lab("/results/%s/release" % result["id"])
        self.assertEqual(response.status_code, 409, payload)
        row = self._doctor_row(appointment, request)
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["result"])

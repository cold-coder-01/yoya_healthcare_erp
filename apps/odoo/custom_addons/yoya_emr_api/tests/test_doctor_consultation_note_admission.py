"""Consultation note saving alongside the inpatient workflow (UAT CONS00036).

  * HPI and ROS save and reload from the server, exactly
  * an admission request -- with an estimate given -- does not block writing
    the consultation note
"""
import uuid

from odoo.tests import tagged

from .test_doctor_consultation_api import CONSULTATION, SAVE, ConsultationCase


@tagged("post_install", "-at_install", "doctor_consultation")
class TestNoteSavesAlongsideAdmission(ConsultationCase):
    def _save(self, appointment, **fields):
        _r, opened = self._get(CONSULTATION % appointment.id)
        body = {"version": opened["data"]["consultation"]["version"]}
        body.update(fields)
        return self._post_body(SAVE % appointment.id, body)

    def test_hpi_and_ros_save_and_reload(self):
        appointment, _encounter = self._in_consultation_visit()
        response, payload = self._save(
            appointment,
            history_of_presenting_illness="Headache for 2 days, worse in the morning.",
            review_of_systems="No fever. No vomiting.",
        )
        self.assertEqual(response.status_code, 200, payload)
        _r, reloaded = self._get(CONSULTATION % appointment.id)
        consultation = reloaded["data"]["consultation"]
        self.assertEqual(consultation["history_of_presenting_illness"], "Headache for 2 days, worse in the morning.")
        self.assertEqual(consultation["review_of_systems"], "No fever. No vomiting.")
        self.assertTrue(consultation["editable"])

    def test_an_admission_request_does_not_block_the_note(self):
        appointment, _encounter = self._in_consultation_visit()
        Admission = self.env["hospital.admission"].with_user(self.doctor.user_id)
        admission, _ = Admission._desk_request_admission(
            appointment.with_user(self.doctor.user_id), "Needs IV antibiotics", str(uuid.uuid4())
        )
        admission.with_user(self.doctor.user_id)._desk_set_estimate(
            20000.0, "Planned stay", str(uuid.uuid4()), admission.sudo().workflow_revision
        )
        self.assertEqual(admission.sudo().state, "draft")

        response, payload = self._save(appointment, history_of_presenting_illness="Updated after request.")
        self.assertEqual(response.status_code, 200, payload)
        _r, reloaded = self._get(CONSULTATION % appointment.id)
        self.assertEqual(
            reloaded["data"]["consultation"]["history_of_presenting_illness"], "Updated after request."
        )

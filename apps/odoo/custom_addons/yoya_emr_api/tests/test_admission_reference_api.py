"""Admission references over real HTTP.

  * the Doctor Desk request and a backend create draw from ONE sequence:
    request, backend, request come out strictly increasing and distinct
  * the browser cannot name an admission: a request body carrying `name` is
    refused before anything is created
  * legacy duplicate references still open on the Admissions Desk, each by its
    own id
"""
import re

from odoo import fields
from odoo.tests import tagged

from .test_admissions_desk_api import DETAIL
from .test_admissions_desk_mutations_api import REQUEST, AdmissionsMutationCase, token

REFERENCE = re.compile(r"^ADM(\d{5,})$")


def _number(reference):
    match = REFERENCE.match(reference or "")
    assert match, reference
    return int(match.group(1))


@tagged("post_install", "-at_install", "admission_reference")
class TestAdmissionReferenceApi(AdmissionsMutationCase):
    def test_the_desk_and_the_backend_share_one_sequence(self):
        _, _, first = self._requested()

        patient = self._patient()
        encounter = self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id, "company_id": self.company.id,
            "primary_doctor_id": self.doctor.id, "opened_at": fields.Datetime.now(),
        })
        encounter.write({"state": "active"})
        backend = self.env["hospital.admission"].sudo().create({
            "patient_id": patient.id, "physician_id": self.doctor.id,
            "company_id": self.company.id,
        })

        _, _, second = self._requested()
        names = [first.name, backend.name, second.name]
        self.assertEqual(len(set(names)), 3, names)
        numbers = [_number(name) for name in names]
        self.assertEqual(numbers, sorted(numbers))

        # What the desk shows is the reference the model assigned.
        detail = self._ok(DETAIL % second.id, self.receptionist)["admission"]
        self.assertEqual(detail["reference"], second.name)

    def test_the_browser_cannot_name_an_admission(self):
        appointment, _ = self._visit()
        response, payload = self._post(
            REQUEST % appointment.id,
            {"operation_token": token(), "reason": "Needs IV antibiotics", "name": "ADM99999"},
            self.doctor_user,
        )
        self._assert_refused(response, payload, 400, "admission_invalid_payload")
        self.assertFalse(
            self.env["hospital.admission"].sudo().with_context(active_test=False).search(
                [("name", "=", "ADM99999")]
            )
        )

    def test_legacy_duplicates_open_by_their_own_id(self):
        _, _, one = self._requested()
        _, _, two = self._requested()
        self._raw("UPDATE hospital_admission SET name = 'ADM00001' WHERE id IN %s", (tuple([one.id, two.id]),))
        for admission in (one, two):
            detail = self._ok(DETAIL % admission.id, self.receptionist)["admission"]
            self.assertEqual(detail["id"], admission.id)
            self.assertEqual(detail["reference"], "ADM00001")

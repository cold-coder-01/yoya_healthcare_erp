"""Decision 3 (docs/business-decisions.md): an open consultation blocks discharge.

  1. EVERY GATE. The discharge-checks list, the doctor's medical-discharge
     request and the final discharge (desk route and back-office
     action_discharge) all refuse while the visit's consultation is a draft --
     archived or not -- with admission_consultation_open / HTTP 409 and the
     holding doctor in error.consultation.
  2. NEVER AUTO-COMPLETES. A refusal leaves the consultation a draft.
  3. THE DOCTOR COMPLETES, THEN DISCHARGE SUCCEEDS. Completion delivers the
     consultation charge exactly once (found by its stable source key) and
     creates no second charge.
  4. NOT BLOCKED. No appointment, consultation never started, emergency stay
     without a consultation. Emergency is not a blanket exemption.
"""
import uuid

from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    DESK_ERROR_MESSAGES,
    AdmissionWorkflowError,
)

from .test_admissions_desk_api import DETAIL
from .test_admissions_desk_discharge_api import AdmissionsDischargeCase
from .test_admissions_desk_mutations_api import VISIT

CODE = "admission_consultation_open"


@tagged("post_install", "-at_install", "admissions_consultation_gate")
class TestConsultationDischargeGate(AdmissionsDischargeCase):
    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    def _consultation_charges(self, appointment):
        return (
            self.env["hospital.charge.line"]
            .sudo()
            .with_context(active_test=False)
            .search([("source_key", "=", appointment.sudo()._consultation_source_key())])
        )

    def _open_consultation(self, appointment):
        """The visit's consultation, opened the real way: the consultation
        charge exists (as a confirmed visit has), the appointment is in
        consultation, and get_or_create_for_appointment() creates the note."""
        appointment = appointment.sudo()
        appointment._ensure_consultation_billing()
        appointment.write({"state": "in_consultation"})
        consultation = self.env["hospital.consultation"].sudo().get_or_create_for_appointment(
            appointment
        )
        self.assertEqual(consultation.state, "draft")
        return consultation

    def _disease(self):
        tag = uuid.uuid4().hex[:6]
        category = self.env["hospital.disease.category"].sudo().create(
            {"name": "Gate %s" % tag, "code": "GT%s" % tag.upper()}
        )
        return self.env["hospital.disease"].sudo().create(
            {"name": "Gate Disease %s" % tag, "code": "G%s" % tag.upper(), "category_id": category.id}
        )

    def _complete(self, consultation):
        """The doctor's completion through the model (assessment + primary
        diagnosis, then action_complete), which delegates to
        appointment.action_done() -- the chain that delivers the charge."""
        consultation.sudo().write({"assessment": "Assessed; admitted for care."})
        self.env["hospital.patient.diagnosis"].sudo().add_to_consultation(
            consultation.sudo(), self._disease(), {"diagnosis_type": "primary"}
        )
        consultation.invalidate_recordset()
        consultation.with_user(self.manager).action_complete(consultation.version_token())
        consultation.invalidate_recordset()
        self.assertEqual(consultation.state, "completed")

    def _assert_holder(self, payload, consultation, archived=False):
        held = payload["error"]["consultation"]
        self.assertEqual(held["reference"], consultation.name)
        self.assertEqual(held["doctor_name"], consultation.sudo().doctor_id.name)
        self.assertEqual(held["archived"], archived)
        self.assertIn("started_at", held)
        # The doctor's name travels in the structured field, never the sentence.
        self.assertEqual(payload["error"]["message"], DESK_ERROR_MESSAGES[CODE])

    def _assert_untouched(self, admission, consultation, bed):
        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "admitted")
        self.assertEqual(bed.sudo().current_admission_id, admission.sudo())
        self.assertEqual(admission.sudo().encounter_id.state, "active")
        self.assertEqual(consultation.sudo().state, "draft", "Never completed automatically.")
        self.assertFalse(consultation.sudo().completed_at)

    # ------------------------------------------------------------------
    # 1. Every gate
    # ------------------------------------------------------------------
    def test_discharge_checks_name_the_holding_doctor(self):
        appointment, _encounter, admission = self._inpatient(self.bed_a9)
        consultation = self._open_consultation(appointment)

        blocking, _warnings = admission.sudo()._discharge_checks()
        self.assertIn("consultation_open", blocking)

        detail = self._ok(DETAIL % admission.id, self.receptionist)["admission"]
        checks = {c["code"]: c for c in detail["discharge"]["blocking"]}
        self.assertIn("consultation_open", checks)
        self.assertEqual(checks["consultation_open"]["consultation"]["doctor_name"], consultation.doctor_id.name)
        self.assertFalse(checks["consultation_open"]["consultation"]["archived"])
        self.assertFalse(detail["discharge"]["can_finalize_discharge"])

        visit = self._ok(VISIT % appointment.id, self.doctor_user)["admission"]
        self.assertFalse(visit["can_request_discharge"])
        self.assertEqual(visit["discharge_blocking"][0]["code"], "consultation_open")

    def test_medical_discharge_request_is_refused_and_writes_nothing(self):
        appointment, _encounter, admission = self._inpatient(self.bed_a9)
        consultation = self._open_consultation(appointment)
        revision = admission.sudo().workflow_revision

        response, payload = self._http_ready(appointment, admission)
        self._assert_refused(response, payload, 409, CODE)
        self._assert_holder(payload, consultation)

        self.env.invalidate_all()
        self.assertFalse(admission.sudo().medical_discharge_ready)
        self.assertEqual(admission.sudo().workflow_revision, revision)
        self._assert_untouched(admission, consultation, self.bed_a9)

    def test_final_discharge_is_refused_on_the_desk(self):
        appointment, _encounter, admission = self._inpatient(self.bed_a9)
        # Medically ready first (no consultation yet), then a consultation is
        # opened: the final discharge re-checks under its locks.
        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        consultation = self._open_consultation(appointment)
        self._settle(admission)

        response, payload = self._http_finalize(admission)
        self._assert_refused(response, payload, 409, CODE)
        self._assert_holder(payload, consultation)
        self._assert_untouched(admission, consultation, self.bed_a9)

    def test_back_office_discharge_is_refused(self):
        appointment, _encounter, admission = self._inpatient(self.bed_a9)
        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        consultation = self._open_consultation(appointment)

        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.with_user(self.receptionist).action_discharge()
        self.assertEqual(caught.exception.code, CODE)
        self.assertEqual(caught.exception.extra["consultation"]["reference"], consultation.name)
        self._assert_untouched(admission, consultation, self.bed_a9)

    def test_an_archived_draft_still_blocks(self):
        appointment, _encounter, admission = self._inpatient(self.bed_a9)
        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        consultation = self._open_consultation(appointment)
        consultation.sudo().write({"active": False})
        self._settle(admission)

        detail = self._ok(DETAIL % admission.id, self.receptionist)["admission"]
        checks = {c["code"]: c for c in detail["discharge"]["blocking"]}
        self.assertTrue(checks["consultation_open"]["consultation"]["archived"])

        response, payload = self._http_finalize(admission)
        self._assert_refused(response, payload, 409, CODE)
        self._assert_holder(payload, consultation, archived=True)
        self._assert_untouched(admission, consultation, self.bed_a9)

    def test_system_structural_discharge_is_not_gated(self):
        """Superuser system code keeps Slice 0's structural discharge, which
        decides no business policy. No user, cron or automated action reaches it."""
        appointment, _encounter, admission = self._inpatient(self.bed_a9)
        self._open_consultation(appointment)
        admission.sudo().action_discharge()
        self.assertEqual(admission.sudo().state, "discharged")

    # ------------------------------------------------------------------
    # 3. The doctor completes; the charge is delivered once; discharge works
    # ------------------------------------------------------------------
    def test_completion_delivers_the_charge_once_then_discharge_succeeds(self):
        appointment, encounter, admission = self._inpatient(self.bed_a9)
        consultation = self._open_consultation(appointment)

        before = self._consultation_charges(appointment)
        self.assertEqual(len(before), 1, "One consultation charge, by its stable source key.")
        self.assertEqual(before.qty_delivered, 0.0)

        self._complete(consultation)

        after = self._consultation_charges(appointment)
        self.assertEqual(after, before, "Completion creates no second charge.")
        self.assertEqual(after.qty_delivered, 1.0)
        self.assertEqual(after.delivery_state, "delivered")
        self.assertNotIn(after.charge_state, ("cancelled", "reversed"))
        # The episode stays open for the stay (completion is deferred).
        self.assertEqual(encounter.sudo().state, "active")
        self.assertNotIn("consultation_open", admission.sudo()._discharge_checks()[0])

        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)
        self._settle(admission)
        response, payload = self._http_finalize(admission)
        self.assertEqual(response.status_code, 200, payload)

        self.env.invalidate_all()
        self.assertEqual(admission.sudo().state, "discharged")
        self.assertEqual(encounter.sudo().state, "completed")
        final = self._consultation_charges(appointment)
        self.assertEqual(final, before, "Discharge creates no second consultation charge.")
        self.assertEqual(final.qty_delivered, 1.0, "Delivered exactly once.")

    # ------------------------------------------------------------------
    # 4. Not blocked
    # ------------------------------------------------------------------
    def test_never_started_consultation_does_not_block(self):
        appointment, _encounter, admission = self._inpatient(self.bed_a9)
        self.assertFalse(admission.sudo()._discharge_open_consultation())
        self.assertNotIn("consultation_open", admission.sudo()._discharge_checks()[0])
        response, payload = self._http_ready(appointment, admission)
        self.assertEqual(response.status_code, 200, payload)

    def test_admission_without_appointment_does_not_block(self):
        _appointment, _encounter, admission = self._requested()
        # Attribution is frozen only once the patient is in a bed.
        admission.sudo().write({"appointment_id": False})
        self.assertFalse(admission.sudo()._discharge_open_consultation())
        self.assertNotIn("consultation_open", admission.sudo()._discharge_checks()[0])

    def test_emergency_without_consultation_does_not_block_but_with_one_does(self):
        appointment, encounter, admission = self._inpatient(self.bed_a9)
        # The bypass guard checks the real user's group (a Hospital Manager may
        # authorize) and requires a reason; it stamps the authorizer itself.
        encounter.with_user(self.manager).write({
            "emergency_bypass": True,
            "emergency_bypass_reason": "Test fixture: trauma admitted before payment.",
        })
        self.assertTrue(encounter.sudo().emergency_bypass)
        self.assertNotIn("consultation_open", admission.sudo()._discharge_checks()[0])

        consultation = self._open_consultation(appointment)
        response, payload = self._http_ready(appointment, admission)
        self._assert_refused(response, payload, 409, CODE)
        self._assert_holder(payload, consultation)

"""Radiology Desk Slice 5: validation and release.

WHAT THESE TESTS ARE FOR
------------------------
  1. WHO. Radiologist, Manager and System Administrator validate and release;
     the Radiology Technician is refused, and so is every other role.
  2. ONLY THROUGH THE MODEL. action_validate() and action_release(), with
     hospital_billing's overrides: validation delivers nothing and completes
     nothing; release delivers the charge once and completes the request.
  3. STRICT POLICY UNDER LOCK. Wrong report state, wrong request state, more
     than one active report, an incomplete report -- each refused before the
     model is asked, and a malformed legacy request is refused safely.
  4. ALL OR NOTHING. A refusal inside billing, a failure in completion or in
     the response rolls back the state, the charge, the completion and the
     audit together.
  5. NO MONEY in any payload, for any role -- including when billing's own
     refusal names an amount.
  6. THE DOCTOR SEES THE REPORT AND ITS IMAGES AT RELEASE, AND NOT BEFORE.
  7. THE IMAGE SET FREEZES AT VALIDATION, on every channel.
"""
import base64
import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.hospital_radiology.models.radiology_result import (
    _result_workflow_capability,
)

from .test_radiology_desk_images import (
    DOCTOR_IMAGE,
    IMAGE_MODEL,
    JPEG,
    PNG,
    RadImageCase,
)
from .test_radiology_desk_report import RESULT
from .test_radiology_desk_api import RAD_DESK_LANES

VALIDATE = "/yoya-emr/api/v1/radiology/results/%s/validate"
RELEASE = "/yoya-emr/api/v1/radiology/results/%s/release"
CONTROLLER = "odoo.addons.yoya_emr_api.controllers.radiology"
ENGINE = "odoo.addons.hospital_billing.models.billing_engine.HospitalBillingEngine"
REQUEST_BILLING = (
    "odoo.addons.hospital_billing.models.radiology_billing.HospitalRadiologyRequestBilling"
)


class RadSignoffCase(RadImageCase):
    """RadImageCase plus validation and release over HTTP."""

    def _act_on(self, url, report, user=None, password=None):
        self._auth(user or self.radiologist, password or self.radiologist_password)
        response = self.url_open(
            url % report.id, data="{}", headers={"Content-Type": "application/json"}
        )
        report.invalidate_recordset()
        report.request_id.invalidate_recordset()
        return response, json.loads(response.text)

    def _validate(self, report, user=None, password=None):
        return self._act_on(VALIDATE, report, user, password)

    def _release(self, report, user=None, password=None):
        return self._act_on(RELEASE, report, user, password)

    def _entered_with_image(self):
        """A real paid order, started, reported, entered, with one image."""
        record, _patient, appointment, _e = self._in_progress()
        report, _d = self._opened(record)
        image, _p = self._uploaded(report, JPEG, "brain-axial.jpg")
        response, payload = self._enter(
            report,
            {"findings": "No acute intracranial haemorrhage.",
             "impression": "Normal CT brain.",
             "recommendations": "Clinical correlation."},
        )
        self.assertEqual(response.status_code, 200, payload)
        return record, report, appointment, image

    def _validated_with_image(self):
        record, report, appointment, image = self._entered_with_image()
        response, payload = self._validate(report)
        self.assertEqual(response.status_code, 200, payload)
        return record, report, appointment, image

    def _state_audit(self, model, record):
        return self.env["hospital.audit.log"].sudo().search_count([
            ("model_name", "=", model),
            ("record_id", "=", record.id),
            ("action_type", "=", "state_change"),
        ])

    def _snapshot(self, record, report):
        """Everything a refused or rolled-back transition must leave alone."""
        record.invalidate_recordset()
        report.invalidate_recordset()
        return {
            "result": report.state,
            "request": record.state,
            "completed_at": record.completed_at,
            "charges": self._charge_snapshot(record),
            "result_audit": self._state_audit(RESULT, report),
            "request_audit": self._state_audit("hospital.radiology.request", record),
        }


# ===========================================================================
# 1-10 Authorization
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologySignoffAuthorization(RadSignoffCase):

    def test_01_to_03_report_authors_validate_and_release(self):
        for user, password in (
            (self.radiologist, self.radiologist_password),
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            _record, report, _a, _i = self._entered_with_image()
            response, payload = self._validate(report, user, password)
            self.assertEqual(response.status_code, 200, (user.login, payload))
            self.assertEqual(report.state, "validated", user.login)
            self.assertTrue(payload["data"]["capabilities"]["validate_report"])
            response, payload = self._release(report, user, password)
            self.assertEqual(response.status_code, 200, (user.login, payload))
            self.assertEqual(report.state, "released", user.login)

    def test_04_05_the_technician_neither_validates_nor_releases(self):
        _record, report, _a, _i = self._entered_with_image()
        response, payload = self._validate(report, self.rad_tech, self.tech_password)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (403, "radiology_report_author_required"))
        self.assertEqual(report.state, "entered")
        # The model refuses the technician's validation directly, too.
        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                report.with_user(self.rad_tech).action_validate()
        self.assertEqual(report.state, "entered")
        self._validate(report)
        response, payload = self._release(report, self.rad_tech, self.tech_password)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (403, "radiology_report_author_required"))
        self.assertEqual(report.state, "validated")
        # The model refuses the technician too, whatever the channel.
        with self.assertRaises(UserError):
            with self.env.cr.savepoint():
                report.with_user(self.rad_tech).action_release()

    def test_06_to_09_other_roles_are_refused_before_anything_is_touched(self):
        _record, report, _a, _i = self._entered_with_image()
        for user, password in (
            (self.lab_tech, self.lab_password),
            (self.doctor_user, self.doctor_password),
            (self.nurse, self.nurse_password),
            (self.receptionist, self.receptionist_password),
            (self.cashier, self.cashier_password),
            (self.pharmacist, self.pharmacist_password),
            (self.officer, self.officer_password),
            (self.dpo, self.dpo_password),
        ):
            for url in (VALIDATE, RELEASE):
                response, payload = self._act_on(url, report, user, password)
                self.assertEqual(response.status_code, 403, (user.login, url))
                self.assertEqual(payload["error"]["code"], "radiology_desk_not_authorized")
        self.assertEqual(report.state, "entered")

    def test_10_unauthenticated_never_reaches_the_endpoint(self):
        _record, report, _a, _i = self._entered_with_image()
        self.authenticate(None, None)
        for url in (VALIDATE, RELEASE):
            response = self.url_open(
                url % report.id, data="{}",
                headers={"Content-Type": "application/json"}, allow_redirects=False,
            )
            self.assertIn(response.status_code, (301, 302, 303, 401, 403), url)
        report.invalidate_recordset()
        self.assertEqual(report.state, "entered")


# ===========================================================================
# 11-22 Validation
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyValidation(RadSignoffCase):

    def test_11_to_14_validation_signs_the_report_and_freezes_the_images(self):
        record, report, _a, image = self._entered_with_image()
        charges = self._charge_snapshot(record)
        audit = self._state_audit(RESULT, report)
        response, payload = self._validate(report)
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]
        self.assertEqual((report.state, record.state), ("validated", "in_progress"))
        self.assertEqual((data["result"]["state"], data["request"]["state"]),
                         ("validated", "in_progress"))
        self.assertEqual(data["request"]["lane"], "awaiting_release")
        self.assertFalse(data["request_completed"])
        self.assertFalse(data["result"]["images_mutable"], "images freeze at validation")
        # Validation delivers nothing and completes nothing.
        self.assertEqual(self._charge_snapshot(record), charges)
        self.assertFalse(record.completed_at)
        self.assertEqual(self._state_audit(RESULT, report), audit + 1)
        # Images: frozen (13). Text: still frozen (14).
        response, payload = self._upload(report, PNG, "late.png")
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_image_not_editable"))
        response, payload = self._remove(report, image.id)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_image_not_editable"))
        response, payload = self._save(report, {"findings": "Rewritten."})
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_not_editable"))
        self.assertEqual(report.findings, "No acute intracranial haemorrhage.")

    def test_15_to_18_only_an_entered_report_is_validated(self):
        _record, report, _a, _i = self._validated_with_image()
        response, payload = self._validate(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_not_validatable"))
        self._release(report)
        response, payload = self._validate(report)
        self.assertEqual(response.status_code, 409, "released")
        for state in ("draft", "cancelled"):
            _record, other, _a = self._report_in(state)
            response, payload = self._validate(other)
            self.assertEqual((response.status_code, payload["error"]["code"]),
                             (409, "radiology_result_not_validatable"), state)
            other.invalidate_recordset()
            self.assertEqual(other.state, state)

    def test_19_a_request_not_in_progress_is_refused(self):
        record, _patient = self._legacy_request(state="completed")
        with _result_workflow_capability():
            report = self.env[RESULT].sudo().create({
                "request_id": record.id, "impression": "Legacy.",
            })
            report.sudo().write({"state": "entered"})
        response, payload = self._validate(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_not_validatable"))
        self.assertEqual(report.state, "entered")

    def test_20_more_than_one_active_report_is_refused_never_guessed(self):
        record, _patient, _a, _e = self._in_progress()
        with _result_workflow_capability():
            first = self.env[RESULT].sudo().create({"request_id": record.id, "impression": "One."})
            second = self.env[RESULT].sudo().create({"request_id": record.id, "impression": "Two."})
            (first | second).sudo().write({"state": "entered"})
        for report in (first, second):
            response, payload = self._validate(report)
            self.assertEqual((response.status_code, payload["error"]["code"]),
                             (409, "radiology_result_ambiguous"))
            self.assertEqual(report.state, "entered")

    def test_an_incomplete_report_is_refused_in_the_models_words(self):
        record, _patient, _a, _e = self._in_progress()
        with _result_workflow_capability():
            report = self.env[RESULT].sudo().create({"request_id": record.id})
            report.sudo().write({"state": "entered"})
        response, payload = self._validate(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (422, "radiology_report_incomplete"))
        self.assertIn("findings or the impression", payload["error"]["message"])
        self.assertEqual(report.state, "entered")

    def test_21_22_a_payment_block_is_refused_with_no_amount(self):
        record, report, _a, _i = self._entered_with_image()
        before = self._snapshot(record, report)
        blocked = {
            "cleared": False, "state": "pending", "amount_due": 2500.0,
            "reason": "Patient-payable amount 2500.00 ETB outstanding",
        }
        with patch(ENGINE + ".check_financial_clearance", return_value=blocked):
            response, payload = self._validate(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (422, "radiology_report_validation_blocked"))
        self.assertIn("financial clearance has not been confirmed", payload["error"]["message"])
        self._assert_money_free(payload, response.text)
        self.assertNotIn("2500", response.text)
        self.assertEqual(self._snapshot(record, report), before)

    def test_a_malformed_legacy_request_is_refused_safely(self):
        """In progress, but with no encounter and no charges: billing's own
        integrity refusal, mapped to the fixed sentence."""
        record, _patient = self._legacy_request(state="in_progress")
        with _result_workflow_capability():
            report = self.env[RESULT].sudo().create({
                "request_id": record.id, "impression": "Legacy.",
            })
            report.sudo().write({"state": "entered"})
        response, payload = self._validate(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (422, "radiology_report_validation_blocked"))
        self._assert_money_free(payload, response.text)
        report.invalidate_recordset()
        self.assertEqual(report.state, "entered")


# ===========================================================================
# 23-32 Release
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyRelease(RadSignoffCase):

    def test_23_to_27_release_delivers_once_and_completes_the_request(self):
        record, report, _a, _i = self._validated_with_image()
        self.assertEqual(
            {c[2] for c in self._charge_snapshot(record)}, {"in_progress"},
            "undelivered before release",
        )
        response, payload = self._release(report)
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]
        self.assertEqual((report.state, record.state), ("released", "completed"))
        self.assertTrue(data["request_completed"])
        self.assertEqual((data["result"]["state"], data["request"]["lane"]), ("released", "completed"))
        charges = record.sudo().charge_line_ids
        self.assertEqual(set(charges.mapped("delivery_state")), {"delivered"})
        self.assertEqual(charges.mapped("qty_delivered"), [1.0] * len(charges))
        self.assertTrue(record.completed_at)
        self.assertEqual(record.completed_by_id, self.radiologist)

    def test_28_29_only_a_validated_report_is_released_and_only_once(self):
        record, report, _a, _i = self._entered_with_image()
        response, payload = self._release(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_not_releasable"))
        self.assertEqual(report.state, "entered")
        self._validate(report)
        self.assertEqual(self._release(report)[0].status_code, 200)
        delivered = self._charge_snapshot(record)
        response, payload = self._release(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_not_releasable"))
        self.assertEqual(self._charge_snapshot(record), delivered, "no second delivery")

    def test_30_a_completed_or_malformed_legacy_request_is_refused_safely(self):
        record, _patient = self._legacy_request(state="completed")
        with _result_workflow_capability():
            report = self.env[RESULT].sudo().create({"request_id": record.id, "impression": "L."})
            report.sudo().write({"state": "validated"})
        response, payload = self._release(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_not_releasable"))
        legacy, _patient = self._legacy_request(state="in_progress")
        with _result_workflow_capability():
            report = self.env[RESULT].sudo().create({"request_id": legacy.id, "impression": "L."})
            report.sudo().write({"state": "validated"})
        response, payload = self._release(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (422, "radiology_report_release_blocked"))
        self._assert_money_free(payload, response.text)
        report.invalidate_recordset()
        legacy.invalidate_recordset()
        self.assertEqual((report.state, legacy.state), ("validated", "in_progress"))

    def test_31_more_than_one_active_report_is_refused(self):
        record, _patient, _a, _e = self._in_progress()
        with _result_workflow_capability():
            first = self.env[RESULT].sudo().create({"request_id": record.id, "impression": "One."})
            self.env[RESULT].sudo().create({"request_id": record.id, "impression": "Two."})
            first.sudo().write({"state": "validated"})
        charges = self._charge_snapshot(record)
        response, payload = self._release(first)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_ambiguous"))
        self.assertEqual(self._charge_snapshot(record), charges)

    def test_32_a_payment_block_at_release_delivers_nothing(self):
        record, report, _a, _i = self._validated_with_image()
        before = self._snapshot(record, report)
        blocked = {"cleared": False, "state": "pending", "amount_due": 2500.0,
                   "reason": "Patient-payable amount 2500.00 ETB outstanding"}
        with patch(ENGINE + ".check_financial_clearance", return_value=blocked):
            response, payload = self._release(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (422, "radiology_report_release_blocked"))
        self._assert_money_free(payload, response.text)
        self.assertNotIn("2500", response.text)
        self.assertEqual(self._snapshot(record, report), before)

    def test_a_partial_release_is_released_with_the_request_still_in_progress(self):
        """Two studies, one line reported (a legacy shape): the release is
        honest that the request has not completed."""
        record, _patient, _a, _e = self._in_progress(exams=[self.ct_brain, self.chest_xray])
        first_line = record.line_ids[0]
        with _result_workflow_capability():
            report = self.env[RESULT].sudo().create({
                "request_id": record.id, "impression": "Brain normal.",
                "line_ids": [(0, 0, {"exam_id": first_line.exam_id.id,
                                     "request_line_id": first_line.id})],
            })
            report.sudo().write({"state": "validated"})
        response, payload = self._release(report)
        self.assertEqual(response.status_code, 200, payload)
        self.assertFalse(payload["data"]["request_completed"])
        self.assertEqual(payload["data"]["request"]["state"], "in_progress")
        self.assertEqual(payload["data"]["request"]["lane"], "anomaly")
        self._assert_money_free(payload, json.dumps(payload))


# ===========================================================================
# 33-39 The Doctor's handoff
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologySignoffDoctorHandoff(RadSignoffCase):

    def _doctor_bytes(self, appointment, image):
        self._auth(self.doctor_user, self.doctor_password)
        return self.url_open(DOCTOR_IMAGE % (appointment.id, image.id))

    def test_33_to_39_the_doctor_sees_the_report_and_images_at_release(self):
        record, report, appointment, image = self._entered_with_image()

        def assert_pending(stage):
            result = self._doctor_result(record, appointment)
            self.assertEqual((result["status"], result["result"]), ("pending", None), stage)
            self.assertFalse(self._doctor_order(record, appointment)["has_result"], stage)
            response = self._doctor_bytes(appointment, image)
            self.assertEqual(response.status_code, 404, stage)
            self.assertNotEqual(response.content, JPEG, stage)

        assert_pending("entered")
        self._validate(report)
        assert_pending("validated")
        self._release(report)

        row = self._doctor_result(record, appointment)
        result = row["result"]
        self.assertIsNotNone(result)
        self.assertEqual(result["result_code"], report.name)
        self.assertTrue(result["result_date"])
        self.assertEqual(result["radiologist"], self.radiologist.name)
        self.assertEqual(result["findings"], "No acute intracranial haemorrhage.")
        self.assertEqual(result["impression"], "Normal CT brain.")
        self.assertEqual(result["recommendations"], "Clinical correlation.")
        self.assertTrue(result["exams"])
        self.assertEqual([i["id"] for i in result["images"]], [image.id])
        self.assertTrue(self._doctor_order(record, appointment)["has_result"])
        response = self._doctor_bytes(appointment, image)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, JPEG)


# ===========================================================================
# 40-48 Atomicity
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologySignoffAtomicity(RadSignoffCase):

    def _explode(self, *args, **kwargs):
        raise RuntimeError("exploded")

    def test_40_41_a_failed_validation_response_leaves_the_report_entered(self):
        record, report, _a, image = self._entered_with_image()
        before = self._snapshot(record, report)
        with patch(CONTROLLER + ".serialize_operational_result", side_effect=self._explode), \
                self.assertLogs(CONTROLLER, level="ERROR"):
            response, payload = self._validate(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (500, "radiology_result_transition_response_failed"))
        self.assertNotIn("exploded", response.text)
        self.assertEqual(self._snapshot(record, report), before)
        # The image set is still open: nothing was validated.
        self.assertEqual(self._remove(report, image.id)[0].status_code, 200)

    def _assert_release_rolled_back(self, record, report, before, appointment, image):
        self.assertEqual(self._snapshot(record, report), before)
        self.assertEqual(report.state, "validated")
        self.assertEqual(record.state, "in_progress")
        self.assertEqual({c[2] for c in before["charges"]}, {"in_progress"})
        result = self._doctor_result(record, appointment)
        self.assertEqual((result["status"], result["result"]), ("pending", None))
        self._auth(self.doctor_user, self.doctor_password)
        self.assertEqual(
            self.url_open(DOCTOR_IMAGE % (appointment.id, image.id)).status_code, 404
        )

    def test_42_45_to_48_a_billing_failure_at_release_rolls_everything_back(self):
        record, report, appointment, image = self._validated_with_image()
        before = self._snapshot(record, report)
        with patch(ENGINE + ".mark_charge_delivered", side_effect=self._explode):
            response, payload = self._release(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (422, "radiology_report_release_blocked"))
        self.assertNotIn("exploded", response.text)
        self._assert_release_rolled_back(record, report, before, appointment, image)

    def test_43_45_to_48_a_completion_failure_rolls_everything_back(self):
        record, report, appointment, image = self._validated_with_image()
        before = self._snapshot(record, report)
        with patch(REQUEST_BILLING + "._sync_completion_from_results", side_effect=self._explode):
            response, payload = self._release(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (422, "radiology_report_release_blocked"))
        self._assert_release_rolled_back(record, report, before, appointment, image)

    def test_44_45_to_48_a_failed_release_response_rolls_everything_back(self):
        record, report, appointment, image = self._validated_with_image()
        before = self._snapshot(record, report)
        with patch(CONTROLLER + ".serialize_operational_result", side_effect=self._explode), \
                self.assertLogs(CONTROLLER, level="ERROR"):
            response, payload = self._release(report)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (500, "radiology_result_transition_response_failed"))
        self._assert_release_rolled_back(record, report, before, appointment, image)
        # And it releases cleanly once the confirmation can be built.
        self.assertEqual(self._release(report)[0].status_code, 200)
        self.assertEqual(record.state, "completed")


# ===========================================================================
# 49-53 The freeze
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologySignoffFreeze(RadSignoffCase):

    def test_49_to_53_validated_and_released_are_frozen_on_every_channel(self):
        _record, report, _a, image = self._validated_with_image()
        for stage in ("validated", "released"):
            if stage == "released":
                self._release(report)
            self.assertEqual(report.state, stage)
            response, payload = self._upload(report, PNG, "late.png")
            self.assertEqual(response.status_code, 409, stage)
            response, payload = self._remove(report, image.id)
            self.assertEqual(response.status_code, 409, stage)
            response, payload = self._save(report, {"impression": "Rewritten."})
            self.assertIn(response.status_code, (409, 422), stage)
            attachment = self._attachments(image)
            for attempt in (
                lambda: report.sudo().write({"impression": "Rewritten."}),
                lambda: report.line_ids.sudo().write({"notes": "Rewritten."}),
                lambda: image.sudo().write({"caption": "Changed"}),
                lambda: image.sudo().unlink(),
                lambda: attachment.sudo().write({"datas": base64.b64encode(PNG)}),
                lambda: attachment.sudo().unlink(),
                lambda: self.env[IMAGE_MODEL].sudo().create({
                    "result_id": report.id, "name": "Late", "filename": "late.png",
                    "file": base64.b64encode(PNG)}),
            ):
                with self.assertRaises(UserError, msg=stage):
                    with self.env.cr.savepoint():
                        attempt()
            self.assertTrue(image.exists() and image.active, stage)
            self.assertEqual(base64.b64decode(image.file), JPEG, stage)


# ===========================================================================
# 54-58 Confidentiality
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologySignoffConfidentiality(RadSignoffCase):

    def test_54_to_58_every_payload_is_money_free_for_every_author(self):
        for user, password in (
            (self.radiologist, self.radiologist_password),
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            _record, report, _a, _i = self._entered_with_image()
            for url in (RELEASE, VALIDATE, VALIDATE, RELEASE, RELEASE):
                # refused, ok, refused, ok, refused
                response, payload = self._act_on(url, report, user, password)
                self._assert_money_free(payload, response.text)


# ===========================================================================
# 59-62 Nothing else moved
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologySignoffRegression(RadSignoffCase):

    def test_59_60_report_entry_and_images_still_work_before_validation(self):
        record, _patient, _a, _e = self._in_progress()
        report, _d = self._opened(record)
        self.assertEqual(self._save(report, {"findings": "Draft."})[0].status_code, 200)
        image, _p = self._uploaded(report)
        self.assertEqual(self._enter(report, {"impression": "Normal."})[0].status_code, 200)
        self.assertEqual(self._remove(report, image.id)[0].status_code, 200)
        self._uploaded(report, JPEG, "again.jpg")

    def test_61_lanes_and_counts_follow_validation_and_release(self):
        record, report, _a, _i = self._entered_with_image()
        patient = record.patient_id
        scope = {"q": patient.name, "status": ",".join(RAD_DESK_LANES)}

        def counts():
            summary = self._worklist(**scope)["summary"]
            return (summary["awaiting_validation"], summary["awaiting_release"],
                    summary["completed"], summary["active"])

        self.assertEqual(counts(), (1, 0, 0, 1))
        self._validate(report)
        self.assertEqual(counts(), (0, 1, 0, 1))
        self.assertEqual(self._detail(record)["lane"], "awaiting_release")
        self._release(report)
        self.assertEqual(counts(), (0, 0, 1, 0))
        self.assertEqual(self._detail(record)["lane"], "completed")

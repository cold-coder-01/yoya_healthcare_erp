"""Radiology Desk Slice 3: report drafting and entry.

WHAT THESE TESTS ARE FOR
------------------------
  1. WHO. Every desk role opens the report; only the report authors --
     Radiologist, Manager, System Administrator -- save or enter it. The
     Radiology Technician opens and reads it. Every other role is refused
     before a record is touched.
  2. ONE REPORT. Opening an In progress study creates exactly one draft, with
     one line per active study, and every later open returns the same record.
     A second active report is a conflict, never a silent choice.
  3. ONLY ALLOW-LISTED TEXT. findings, impression, recommendations and each
     line's summary and notes. Every other key is refused, not dropped.
  4. ENTRY IS ONE ATOMIC ACT with a completeness gate, and records the author
     as the reporting radiologist.
  5. NOTHING BYPASSES THE MODEL. Direct state, provenance and content writes
     over genuine JSON-RPC -- any context -- are refused.
  6. ALL OR NOTHING. A failure while building a response leaves no report, no
     text and no audit row behind it.
  7. NO MONEY, and the DOCTOR still sees nothing until release, while
     validation and release themselves still work.
"""
import json
from unittest.mock import MagicMock, patch

from psycopg2 import errors as pg_errors

from odoo.tests import tagged
from odoo.tests.common import JsonRpcException

from odoo.addons.hospital_radiology.models.radiology_result import (
    _result_workflow_capability,
)

from ..controllers import radiology as rad_controller
from .test_radiology_desk_api import (
    DETAIL,
    RAD_DESK_LANES,
    _walk_keys,
)
from .test_radiology_desk_transitions import (
    DOCTOR_ORDERS,
    DOCTOR_RESULTS,
    RadTransitionCase,
)

REPORT = DETAIL + "/report"
RESULTS = "/yoya-emr/api/v1/radiology/results/%s"
SAVE = RESULTS + "/save"
ENTER = RESULTS + "/enter"
CONTROLLER = "odoo.addons.yoya_emr_api.controllers.radiology"
RESULT = "hospital.radiology.result"

FORGED = {
    "skip_radiology_result_write_audit": True,
    "hospital_radiology_result_workflow_capability": True,
    "allow_state_write": True,
}


class RadReportCase(RadTransitionCase):
    """RadTransitionCase plus an In progress study and the report routes."""

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    def _in_progress(self, exams=None):
        """A real Doctor Desk order, paid, scheduled and started through the
        request's own workflow -- the shape human UAT will use."""
        appointment, encounter, patient = self._visit()
        record = self._new_order(appointment, exams=exams)
        self._settle(encounter)
        record.sudo().action_schedule()
        record.sudo().action_mark_in_progress()
        record.invalidate_recordset()
        self.assertEqual(record.state, "in_progress")
        return record, patient, appointment, encounter

    def _reports(self, record):
        return self.env[RESULT].sudo().with_context(active_test=False).search(
            [("request_id", "=", record.id)]
        )

    def _result_audit(self, report, action_type=None):
        domain = [("model_name", "=", RESULT), ("record_id", "=", report.id)]
        if action_type:
            domain.append(("action_type", "=", action_type))
        return self.env["hospital.audit.log"].sudo().search(domain, order="id asc")

    # ------------------------------------------------------------------
    # HTTP
    # ------------------------------------------------------------------
    def _post(self, url, body=None, user=None, password=None):
        self._auth(user or self.radiologist, password or self.radiologist_password)
        response = self.url_open(
            url, data=json.dumps(body or {}),
            headers={"Content-Type": "application/json"},
        )
        return response, json.loads(response.text)

    def _open(self, record, user=None, password=None):
        return self._post(REPORT % record.id, {}, user, password)

    def _opened(self, record, user=None, password=None):
        response, payload = self._open(record, user, password)
        self.assertEqual(response.status_code, 200, payload)
        report = self.env[RESULT].sudo().browse(payload["data"]["result"]["id"])
        return report, payload["data"]

    def _save(self, report, body, user=None, password=None):
        response, payload = self._post(SAVE % report.id, body, user, password)
        report.invalidate_recordset()
        return response, payload

    def _enter(self, report, body=None, user=None, password=None):
        response, payload = self._post(ENTER % report.id, body or {}, user, password)
        report.invalidate_recordset()
        return response, payload

    def _entered(self, record, body=None):
        report, _data = self._opened(record)
        response, payload = self._enter(
            report, body or {"findings": "No acute abnormality.", "impression": "Normal study."}
        )
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(report.state, "entered")
        return report

    def _rpc(self, model, method, args, user=None, password=None, context=None):
        self._auth(user or self.manager, password or self.manager_password)
        return self.make_jsonrpc_request(
            "/web/dataset/call_kw/%s/%s" % (model, method),
            {"model": model, "method": method, "args": args,
             "kwargs": {"context": context or {}}},
        )

    def _doctor_result(self, record, appointment):
        response, payload = self._get(
            DOCTOR_RESULTS % appointment.id, self.doctor_user, self.doctor_password
        )
        self.assertEqual(response.status_code, 200)
        return next(r for r in payload["data"]["radiology"] if r["request_id"] == record.id)

    def _doctor_order(self, record, appointment):
        response, payload = self._get(
            DOCTOR_ORDERS % appointment.id, self.doctor_user, self.doctor_password
        )
        self.assertEqual(response.status_code, 200)
        return next(o for o in payload["data"]["orders"] if o["id"] == record.id)


# ===========================================================================
# 1-10 Authorization
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyReportAuthorization(RadReportCase):

    def test_01_to_03_report_authors_open_save_and_enter(self):
        for user, password in (
            (self.radiologist, self.radiologist_password),
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            record, *_rest = self._in_progress()
            report, data = self._opened(record, user, password)
            self.assertTrue(data["created"], user.login)
            self.assertEqual(
                {key: data["capabilities"][key] for key in ("open_report", "edit_report", "enter_report")},
                {"open_report": True, "edit_report": True, "enter_report": True},
            )
            response, _p = self._save(report, {"findings": "Clear."}, user, password)
            self.assertEqual(response.status_code, 200, user.login)
            response, _p = self._enter(report, {"impression": "Normal."}, user, password)
            self.assertEqual(response.status_code, 200, user.login)
            self.assertEqual((report.state, report.radiologist_id), ("entered", user))

    def test_04_the_technician_opens_and_reads_but_does_not_author(self):
        record, *_rest = self._in_progress()
        report, data = self._opened(record, self.rad_tech, self.tech_password)
        self.assertTrue(data["created"])
        self.assertEqual(report.state, "draft")
        self.assertFalse(report.radiologist_id, "opening is not interpreting")
        self.assertEqual(
            (data["capabilities"]["edit_report"], data["capabilities"]["enter_report"]),
            (False, False),
        )
        for route, body in ((SAVE, {"findings": "Tech wording."}), (ENTER, {"impression": "Tech."})):
            response, payload = self._post(route % report.id, body, self.rad_tech, self.tech_password)
            self.assertEqual(response.status_code, 403, route)
            self.assertEqual(payload["error"]["code"], "radiology_report_author_required")
        report.invalidate_recordset()
        self.assertEqual((report.state, report.findings, report.impression), ("draft", False, False))
        # Reopening returns the same record.
        again, data = self._opened(record, self.rad_tech, self.tech_password)
        self.assertEqual((again, data["created"]), (report, False))

    def test_05_to_09_other_roles_are_refused_and_nothing_is_created(self):
        record, *_rest = self._in_progress()
        report, _data = self._opened(record)
        for user, password in (
            (self.lab_tech, self.lab_password),
            (self.doctor_user, self.doctor_password),
            (self.nurse, self.nurse_password),
            (self.receptionist, self.receptionist_password),
            (self.cashier, self.cashier_password),
        ):
            for url, body in ((REPORT % record.id, {}), (SAVE % report.id, {"findings": "X"}),
                              (ENTER % report.id, {"impression": "X"})):
                response, payload = self._post(url, body, user, password)
                self.assertEqual(response.status_code, 403, (user.login, url))
                self.assertEqual(payload["error"]["code"], "radiology_desk_not_authorized")
        report.invalidate_recordset()
        self.assertEqual((report.state, report.findings), ("draft", False))
        self.assertEqual(self._reports(record), report)

    def test_10_unauthenticated_never_reaches_the_endpoint(self):
        record, *_rest = self._in_progress()
        report, _data = self._opened(record)
        self.authenticate(None, None)
        for url in (REPORT % record.id, SAVE % report.id, ENTER % report.id):
            response = self.url_open(
                url, data='{"findings": "X"}',
                headers={"Content-Type": "application/json"}, allow_redirects=False,
            )
            self.assertIn(response.status_code, (301, 302, 303, 401, 403), url)
        report.invalidate_recordset()
        self.assertEqual((report.state, report.findings), ("draft", False))


# ===========================================================================
# 11-19 Create / open
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyReportOpen(RadReportCase):

    def test_11_an_in_progress_study_gets_one_draft_report(self):
        record, *_rest = self._in_progress()
        self.assertFalse(self._reports(record))
        report, data = self._opened(record)
        self.assertTrue(data["created"])
        self.assertEqual(self._reports(record), report)
        self.assertEqual(report.state, "draft")
        self.assertEqual(data["result"]["state"], "draft")
        self.assertEqual(data["result"]["name"], report.name)
        self.assertIsNone(data["result"].get("radiologist"))
        self.assertEqual(data["request"]["lane"], "awaiting_report")
        self.assertEqual(record.state, "in_progress")
        self.assertEqual(self._result_audit(report, "create").mapped("user_id"), self.radiologist)

    def test_12_lines_are_copied_from_the_active_studies(self):
        record, *_rest = self._in_progress(exams=[self.ct_brain, self.chest_xray])
        report, data = self._opened(record)
        self.assertEqual(report.patient_id, record.patient_id)
        self.assertEqual(report.physician_id, record.physician_id)
        self.assertEqual(report.line_ids.mapped("request_line_id"), record.line_ids)
        self.assertEqual(
            sorted(line["request_line_id"] for line in data["result"]["lines"]),
            sorted(record.line_ids.ids),
        )

    def test_13_an_existing_draft_is_reused(self):
        record, *_rest = self._in_progress()
        first, _d = self._opened(record)
        self._save(first, {"findings": "Kept."})
        again, data = self._opened(record)
        self.assertEqual((again, data["created"]), (first, False))
        self.assertEqual(data["result"]["findings"], "Kept.")
        self.assertEqual(len(self._reports(record)), 1)

    def test_14_an_entered_report_is_reused_read_only(self):
        record, *_rest = self._in_progress()
        report = self._entered(record)
        again, data = self._opened(record)
        self.assertEqual((again, data["created"], data["result"]["state"]), (report, False, "entered"))
        response, payload = self._save(report, {"findings": "Late edit."})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "radiology_result_not_editable")

    def test_15_more_than_one_active_report_is_refused_never_guessed(self):
        record, *_rest = self._in_progress()
        with _result_workflow_capability():
            first = self.env[RESULT].sudo().create({"request_id": record.id})
            self.env[RESULT].sudo().create({"request_id": record.id})
        response, payload = self._open(record)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(payload["error"]["code"], "radiology_result_ambiguous")
        response, payload = self._save(first, {"findings": "X"})
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_ambiguous"))
        self.assertEqual(len(self._reports(record)), 2)

    def test_16_a_double_open_yields_one_report(self):
        """Sequential double click. The CONCURRENT case cannot be reproduced
        inside HttpCase (one shared test cursor serializes every request); it
        rests on the lock + row modification + Odoo replay, whose two
        load-bearing parts are pinned by test_16b and test_16c, and on the
        model's own one-operational-report rule, pinned by test_16d."""
        record, *_rest = self._in_progress()
        first, one = self._opened(record)
        second, two = self._opened(record)
        self.assertEqual((one["created"], two["created"]), (True, False))
        self.assertEqual(first, second)
        self.assertEqual(len(self._reports(record)), 1)

    def test_16b_concurrency_failures_reach_odoo_retry_unenveloped(self):
        for error_type in (
            pg_errors.SerializationFailure,
            pg_errors.LockNotAvailable,
            pg_errors.DeadlockDetected,
        ):
            def handler(error_type=error_type):
                raise error_type("simulated concurrent update")

            wrapped = rad_controller.rad_endpoint(handler)
            fake_request = MagicMock()
            fake_request.env.user._is_public.return_value = False
            with patch.object(rad_controller, "request", new=fake_request):
                with self.assertRaises(error_type):
                    wrapped()

    def test_16c_the_create_path_modifies_the_locked_request_row(self):
        """THE HALF OF THE GUARD A PLAIN LOCK LACKS: without the no-op row update
        a concurrent creator's stale REPEATABLE READ snapshot would find no
        report after acquiring the lock and create a second one."""
        record, *_rest = self._in_progress()
        executed = []
        original = type(self.env.cr).execute

        def spy(cursor, query, params=None, log_exceptions=True):
            executed.append(str(query))
            return original(cursor, query, params, log_exceptions)

        with patch.object(type(self.env.cr), "execute", spy):
            response, _payload = self._open(record)
        self.assertEqual(response.status_code, 200)
        lock = "SELECT id FROM hospital_radiology_request WHERE id = %s FOR UPDATE"
        touch = (
            "UPDATE hospital_radiology_request SET write_date = write_date "
            "WHERE id = %s"
        )
        self.assertIn(lock, executed)
        self.assertIn(touch, executed)
        self.assertLess(executed.index(lock), executed.index(touch))

        executed.clear()
        with patch.object(type(self.env.cr), "execute", spy):
            self._open(record)
        self.assertIn(lock, executed)
        self.assertNotIn(touch, executed)

    def test_16d_the_model_itself_refuses_a_second_operational_report(self):
        """The last line of defence if two creators ever both got past the
        desk: the second create is refused by the model, on every channel."""
        record, *_rest = self._in_progress()
        self._opened(record)
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                self.env[RESULT].with_user(self.radiologist).create({"request_id": record.id})
        self.assertEqual(len(self._reports(record)), 1)

    def test_17_to_19_no_report_outside_in_progress(self):
        requested, *_rest = self._clear_requested()
        scheduled, *_rest = self._clear_scheduled()
        completed, _patient = self._legacy_request(state="completed")
        for record in (requested, scheduled, completed):
            response, payload = self._open(record)
            self.assertEqual(response.status_code, 422, record.state)
            self.assertEqual(payload["error"]["code"], "radiology_report_not_available")
            self.assertFalse(self._reports(record), record.state)

    def test_no_report_for_a_request_without_an_active_study(self):
        record, *_rest = self._in_progress()
        record.line_ids.sudo().write({"state": "cancelled"})
        response, payload = self._open(record)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "radiology_request_no_active_study")
        self.assertFalse(self._reports(record))

    def test_a_missing_or_hidden_request_is_404(self):
        response, payload = self._open(self.env["hospital.radiology.request"].browse(99999999))
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (404, "radiology_request_not_found"))
        response, payload = self._post(SAVE % 99999999, {"findings": "X"})
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (404, "radiology_result_not_found"))


# ===========================================================================
# 20-31 Ownership and state authority over genuine RPC
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyReportAuthorityOverRpc(RadReportCase):

    def test_20_to_23_provenance_cannot_be_rewritten_over_rpc(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        other, *_rest = self._in_progress()
        for vals in (
            {"request_id": other.id},
            {"patient_id": other.patient_id.id},
            {"physician_id": self.other_doctor.id},
        ):
            for context in (None, FORGED):
                with self.assertRaises(JsonRpcException, msg=str(vals)):
                    self._rpc(RESULT, "write", [[report.id], vals], context=context)
        report.invalidate_recordset()
        self.assertEqual(
            (report.request_id, report.patient_id, report.physician_id),
            (record, record.patient_id, record.physician_id),
        )

    def test_24_to_28_direct_state_writes_over_rpc_are_refused(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        for state in ("entered", "validated", "released"):
            for context in (None, FORGED):
                with self.assertRaises(JsonRpcException, msg=(state, context)):
                    self._rpc(RESULT, "write", [[report.id], {"state": state}], context=context)
        report.invalidate_recordset()
        self.assertEqual(report.state, "draft")

        entered = self._entered(self._in_progress()[0])
        with self.assertRaises(JsonRpcException):
            self._rpc(RESULT, "write", [[entered.id], {"state": "validated"}], context=FORGED)
        entered.invalidate_recordset()
        self.assertEqual(entered.state, "entered")

    def test_the_radiologist_cannot_be_named_over_rpc(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        with self.assertRaises(JsonRpcException):
            self._rpc(RESULT, "write", [[report.id], {"radiologist_id": self.manager.id}],
                      context=FORGED)
        report.invalidate_recordset()
        self.assertFalse(report.radiologist_id)

    def test_private_workflow_helpers_are_not_callable_over_rpc(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        for method, args in (("_write_state", ["entered"]),
                             ("_workflow_write", [{"state": "entered"}])):
            with self.assertRaises(JsonRpcException, msg=method):
                self._rpc(RESULT, method, [[report.id], *args])
        report.invalidate_recordset()
        self.assertEqual(report.state, "draft")

    def test_the_freeze_holds_over_rpc_with_any_context(self):
        report = self._entered(self._in_progress()[0])
        for context in (None, FORGED):
            with self.assertRaises(JsonRpcException):
                self._rpc(RESULT, "write", [[report.id], {"findings": "Rewritten."}], context=context)
            with self.assertRaises(JsonRpcException):
                self._rpc("hospital.radiology.result.line", "write",
                          [report.line_ids.ids, {"notes": "Rewritten."}], context=context)
        report.invalidate_recordset()
        self.assertEqual(report.findings, "No acute abnormality.")

    def test_29_to_31_entry_validation_and_release_still_work(self):
        """The whole chain on a desk-entered report: billing's validation and
        release overrides still run, deliver the charge once and complete the
        request. Validation and release are NOT desk routes; they are the
        model methods the Odoo form still calls."""
        record, _patient, appointment, _encounter = self._in_progress()
        report = self._entered(record)
        self.assertEqual(report.radiologist_id, self.radiologist)
        report.with_user(self.radiologist).action_validate()
        self.assertEqual(report.state, "validated")
        report.with_user(self.radiologist).action_release()
        record.invalidate_recordset()
        self.assertEqual((report.state, record.state), ("released", "completed"))
        charges = record.sudo().charge_line_ids
        self.assertEqual(set(charges.mapped("delivery_state")), {"delivered"})
        self.assertEqual(sum(charges.mapped("qty_delivered")), float(len(charges)))
        values = self._result_audit(report, "state_change").mapped("new_value")
        self.assertEqual(values, ["State: entered", "State: validated", "State: released"])

    def test_validation_re_checks_completeness(self):
        record, *_rest = self._in_progress()
        with _result_workflow_capability():
            report = self.env[RESULT].sudo().create({"request_id": record.id})
            report.sudo().write({"state": "entered"})
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                report.with_user(self.radiologist).action_validate()
        self.assertEqual(report.state, "entered")


# ===========================================================================
# 32-41 Save
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyReportSave(RadReportCase):

    def test_32_to_36_every_allow_listed_field_saves_and_stays_draft(self):
        record, _patient, appointment, _e = self._in_progress()
        report, _d = self._opened(record)
        line = report.line_ids[:1]
        before = len(self._result_audit(report, "update"))
        response, payload = self._save(report, {
            "findings": "Hyperdense focus, left frontal.",
            "impression": "Small contusion.",
            "recommendations": "Repeat CT in 24 hours.",
            "lines": [{"id": line.id, "result_summary": "Contusion.", "notes": "Non-contrast."}],
        })
        self.assertEqual(response.status_code, 200, payload)
        data = payload["data"]["result"]
        self.assertEqual(
            (data["state"], data["findings"], data["impression"], data["recommendations"]),
            ("draft", "Hyperdense focus, left frontal.", "Small contusion.", "Repeat CT in 24 hours."),
        )
        self.assertEqual(
            (data["lines"][0]["result_summary"], data["lines"][0]["notes"]),
            ("Contusion.", "Non-contrast."),
        )
        self.assertEqual((report.state, report.findings), ("draft", "Hyperdense focus, left frontal."))
        self.assertEqual((line.result_summary, line.notes), ("Contusion.", "Non-contrast."))
        self.assertFalse(report.radiologist_id, "saving is not entering")
        self.assertEqual(len(self._result_audit(report, "update")), before + 1)
        self.assertEqual(payload["data"]["request"]["lane"], "awaiting_report")
        # A draft may be cleared again.
        response, _p = self._save(report, {"recommendations": None})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(report.recommendations)

    def test_37_an_unknown_field_is_refused_not_dropped(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        response, payload = self._save(report, {"findings": "X", "comment": "Y"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "radiology_report_field_not_allowed")
        self.assertFalse(report.findings)

    def test_38_every_protected_field_is_refused(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        line = report.line_ids[:1]
        for body in (
            {"state": "entered"}, {"request_id": 1}, {"patient_id": 1},
            {"physician_id": 1}, {"radiologist_id": self.radiologist.id},
            {"active": False}, {"result_date": "2020-01-01"}, {"image_ids": []},
            {"line_ids": []}, {"name": "X"},
            {"lines": [{"id": line.id, "exam_id": 1}]},
            {"lines": [{"id": line.id, "request_line_id": 1}]},
            {"lines": [{"id": line.id, "contrast_used": True}]},
            {"lines": [{"id": line.id, "body_part": "Chest"}]},
        ):
            response, payload = self._save(report, body)
            self.assertEqual(response.status_code, 400, body)
            self.assertEqual(payload["error"]["code"], "radiology_report_field_not_allowed", body)
        report.invalidate_recordset()
        self.assertEqual((report.state, report.active, report.radiologist_id.id), ("draft", True, False))

    def test_a_foreign_line_or_a_bad_shape_is_refused(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        other_report, _d = self._opened(self._in_progress()[0])
        for body, code in (
            ({"lines": [{"id": other_report.line_ids[:1].id, "notes": "X"}]}, "radiology_result_line_not_found"),
            ({"lines": "nope"}, "invalid_field"),
            ({"lines": [{"notes": "no id"}]}, "invalid_field"),
            ({"findings": 42}, "invalid_field"),
        ):
            response, payload = self._save(report, body)
            self.assertEqual((response.status_code, payload["error"]["code"]), (400, code), body)
        self.assertFalse(other_report.line_ids.notes)

    def test_39_to_41_a_report_past_draft_cannot_be_saved(self):
        record, *_rest = self._in_progress()
        report = self._entered(record)
        response, payload = self._save(report, {"findings": "Late."})
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_not_editable"))
        report.with_user(self.radiologist).action_validate()
        response, payload = self._save(report, {"findings": "Later."})
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_not_editable"))
        report.with_user(self.radiologist).action_release()
        response, payload = self._save(report, {"findings": "Much later."})
        # Released completes the request, so the study is no longer open.
        self.assertIn(response.status_code, (409, 422))
        report.invalidate_recordset()
        self.assertEqual(report.findings, "No acute abnormality.")


# ===========================================================================
# 42-45 Completeness, and entry
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyReportEntry(RadReportCase):

    def test_42_an_empty_report_cannot_be_entered(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        response, payload = self._enter(report, {})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "radiology_report_incomplete")
        self.assertEqual((report.state, report.radiologist_id.id), ("draft", False))

    def test_43_a_minimally_complete_report_is_entered_by_its_author(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record, self.rad_tech, self.tech_password)
        response, payload = self._enter(report, {"impression": "Normal study."})
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(report.state, "entered")
        self.assertEqual(report.radiologist_id, self.radiologist)
        data = payload["data"]
        self.assertEqual(data["result"]["state"], "entered")
        self.assertEqual(data["result"]["radiologist"], self.radiologist.name)
        self.assertEqual(data["request"]["lane"], "awaiting_validation")
        audit = self._result_audit(report, "state_change")
        self.assertEqual((audit[-1].old_value, audit[-1].new_value, audit[-1].user_id),
                         ("State: draft", "State: entered", self.radiologist))

    def test_44_line_text_neither_required_nor_sufficient(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        line = report.line_ids[:1]
        response, payload = self._enter(
            report, {"lines": [{"id": line.id, "result_summary": "Normal."}]}
        )
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (422, "radiology_report_incomplete"))
        response, payload = self._enter(report, {"findings": "No abnormality."})
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(report.state, "entered")

    def test_whitespace_only_narrative_is_incomplete(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        response, payload = self._enter(report, {"findings": "   ", "impression": "\n\t"})
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (422, "radiology_report_incomplete"))

    def test_45_a_refused_entry_saves_nothing(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        self._save(report, {"findings": "   "})
        audit_before = len(self._result_audit(report))
        response, _p = self._enter(report, {"recommendations": "Follow up.", "findings": "  "})
        self.assertEqual(response.status_code, 422)
        self.assertEqual((report.state, report.recommendations), ("draft", False))
        self.assertEqual(len(self._result_audit(report)), audit_before)

    def test_entry_saves_the_text_on_screen_and_enters_in_one_act(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        line = report.line_ids[:1]
        response, payload = self._enter(report, {
            "findings": "Final findings.",
            "impression": "Final impression.",
            "lines": [{"id": line.id, "notes": "Final note."}],
        })
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual((report.state, report.findings, line.notes),
                         ("entered", "Final findings.", "Final note."))


# ===========================================================================
# 46-50 Freeze after entry
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyReportFreeze(RadReportCase):

    def test_46_to_49_entered_validated_and_released_are_frozen_at_the_model(self):
        record, *_rest = self._in_progress()
        report = self._entered(record)
        for step in (None, "action_validate", "action_release"):
            if step:
                getattr(report.with_user(self.radiologist), step)()
            for target in (report.sudo(), report.with_user(self.radiologist)):
                with self.assertRaises(Exception, msg=(report.state, "header")):
                    with self.env.cr.savepoint():
                        target.write({"impression": "Rewritten."})
            with self.assertRaises(Exception, msg=(report.state, "line")):
                with self.env.cr.savepoint():
                    report.line_ids.sudo().write({"result_summary": "Rewritten."})
            report.invalidate_recordset()
            self.assertEqual(report.impression, "Normal study.", report.state)

    def test_50_no_context_opens_the_freeze(self):
        report = self._entered(self._in_progress()[0])
        with self.assertRaises(Exception):
            with self.env.cr.savepoint():
                report.sudo().with_context(**FORGED).write({"findings": "Rewritten."})
        report.invalidate_recordset()
        self.assertEqual(report.findings, "No acute abnormality.")


# ===========================================================================
# 51-55 Atomicity
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyReportAtomicity(RadReportCase):

    def _explode(self, *args, **kwargs):
        raise RuntimeError("serialization exploded")

    def test_51_54_a_failed_open_leaves_no_report_and_no_audit(self):
        record, *_rest = self._in_progress()
        audit_before = self.env["hospital.audit.log"].sudo().search_count(
            [("model_name", "=", RESULT)]
        )
        with patch(CONTROLLER + ".serialize_operational_result", side_effect=self._explode), \
                self.assertLogs(CONTROLLER, level="ERROR"):
            response, payload = self._open(record)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "radiology_report_response_failed")
        self.assertNotIn("exploded", response.text)
        self.assertFalse(self._reports(record))
        self.assertEqual(
            self.env["hospital.audit.log"].sudo().search_count([("model_name", "=", RESULT)]),
            audit_before,
        )
        record.invalidate_recordset()
        self.assertEqual(record.state, "in_progress")

    def test_52_54_a_failed_save_leaves_the_draft_as_it_was(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        self._save(report, {"findings": "Before."})
        audit_before = len(self._result_audit(report))
        with patch(CONTROLLER + ".serialize_operational_result", side_effect=self._explode), \
                self.assertLogs(CONTROLLER, level="ERROR"):
            response, payload = self._save(report, {"findings": "After."})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "radiology_report_response_failed")
        self.assertEqual(report.findings, "Before.")
        self.assertEqual(len(self._result_audit(report)), audit_before)

    def test_53_54_a_failed_entry_leaves_the_draft_unentered_and_unsaved(self):
        record, *_rest = self._in_progress()
        report, _d = self._opened(record)
        audit_before = len(self._result_audit(report))
        with patch(CONTROLLER + ".serialize_operational_result", side_effect=self._explode), \
                self.assertLogs(CONTROLLER, level="ERROR"):
            response, payload = self._enter(report, {"impression": "Normal."})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "radiology_report_response_failed")
        self.assertEqual(
            (report.state, report.impression, report.radiologist_id.id), ("draft", False, False)
        )
        self.assertEqual(len(self._result_audit(report)), audit_before)
        # And the same entry succeeds once the confirmation can be built.
        response, _p = self._enter(report, {"impression": "Normal."})
        self.assertEqual((response.status_code, report.state), (200, "entered"))


# ===========================================================================
# 56-58 Confidentiality
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyReportConfidentiality(RadReportCase):

    def test_56_to_58_every_payload_is_money_free_for_every_author(self):
        for user, password in (
            (self.radiologist, self.radiologist_password),
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            record, *_rest = self._in_progress()
            response, payload = self._open(record, user, password)
            self._assert_money_free(payload, response.text)
            report = self.env[RESULT].sudo().browse(payload["data"]["result"]["id"])
            for route, body in (
                (ENTER, {}),                      # refused: incomplete
                (SAVE, {"bogus": 1}),             # refused: not allowed
                (SAVE, {"findings": "Clear."}),
                (ENTER, {"impression": "Normal."}),
                (SAVE, {"findings": "Late."}),    # refused: not editable
            ):
                response, payload = self._post(route % report.id, body, user, password)
                self._assert_money_free(payload, response.text)
            response, payload = self._open(record, user, password)
            self._assert_money_free(payload, response.text)
        blocked, *_rest = self._clear_requested()
        response, payload = self._open(blocked)
        self.assertEqual(response.status_code, 422)
        self._assert_money_free(payload, response.text)


# ===========================================================================
# 59-66 Doctor visibility and lanes
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyReportRegression(RadReportCase):

    def test_59_to_62_the_doctor_sees_nothing_until_release(self):
        record, _patient, appointment, _e = self._in_progress()
        report, _d = self._opened(record)
        self._save(report, {"findings": "SECRETDRAFTFINDING", "impression": "SECRETIMPRESSION"})

        def assert_pending(stage):
            result = self._doctor_result(record, appointment)
            self.assertEqual((result["status"], result["result"]), ("pending", None), stage)
            order = self._doctor_order(record, appointment)
            self.assertFalse(order["has_result"], stage)
            response, _payload = self._get(
                DOCTOR_RESULTS % appointment.id, self.doctor_user, self.doctor_password
            )
            self.assertNotIn("SECRET", response.text, stage)

        assert_pending("draft")
        self._enter(report, {})
        assert_pending("entered")
        report.with_user(self.radiologist).action_validate()
        assert_pending("validated")
        report.with_user(self.radiologist).action_release()
        result = self._doctor_result(record, appointment)
        self.assertIsNotNone(result["result"])
        self.assertEqual(result["result"]["findings"], "SECRETDRAFTFINDING")
        self.assertEqual(result["result"]["radiologist"], self.radiologist.name)
        self.assertEqual(result["result"]["images"], [])

    def test_64_to_66_lanes_and_counts_follow_the_report(self):
        record, patient, *_rest = self._in_progress()
        scope = {"q": patient.name, "status": ",".join(RAD_DESK_LANES)}

        def counts():
            summary = self._worklist(**scope)["summary"]
            return summary["awaiting_report"], summary["awaiting_validation"]

        self.assertEqual(counts(), (1, 0))
        report, _d = self._opened(record)
        self._save(report, {"findings": "Draft."})
        self.assertEqual(counts(), (1, 0))
        self.assertEqual(self._detail(record)["lane"], "awaiting_report")
        self._enter(report, {})
        self.assertEqual(counts(), (0, 1))
        self.assertEqual(self._detail(record)["lane"], "awaiting_validation")
        data = self._worklist(**scope)
        self.assertEqual(self._rows(data)[record.id]["lane"], "awaiting_validation")

    def test_the_payloads_carry_no_billing_or_file_handle_keys(self):
        record, *_rest = self._in_progress()
        _response, payload = self._open(record)
        keys = set(_walk_keys(payload))
        self.assertNotIn("image_ids", keys)
        self.assertNotIn("radiologist_id", keys)

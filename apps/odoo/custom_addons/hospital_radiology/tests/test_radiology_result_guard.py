"""Radiology Slice 3: the report model's own authority.

WHAT THIS PINS
--------------
Before Slice 3, hospital.radiology.result accepted:
  * a report created against a request in ANY state, several per request;
  * a direct `state` write -- draft straight to released, on an empty report;
  * re-pointing a report at another request, patient or ordering physician;
  * text edits on a validated report, and on every line of any report;
  * a context key (skip_radiology_result_write_audit) that opened the release
    freeze;
  * whoever CREATED the record, recorded as the interpreting radiologist.

Now, on every channel -- sudo, a forged context, any role:
  * a report is created only for an In progress request, born draft and
    unattributed, one operational report per request;
  * `state` and `radiologist_id` move only through the workflow methods;
  * request, patient and ordering physician never change;
  * a report's content and its lines freeze when it leaves draft;
  * only a report author (Radiologist, Manager, Administrator) writes the
    narrative or marks it entered, and entry requires a narrative;
  * entry records the author as the reporting radiologist.

Validation and release are overridden by hospital_billing and need real charges;
they are exercised end to end by yoya_emr_api's Radiology Desk report tests.
"""
import uuid

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.radiology_request import _request_state_capability
from ..models.radiology_result import (
    _result_workflow_capability,
    has_result_workflow_capability,
)

TECHNICIAN = "hospital_radiology.group_hospital_radiology_technician"
RADIOLOGIST = "hospital_radiology.group_hospital_radiologist"
DOCTOR = "hospital_management.group_hospital_doctor"
MANAGER = "hospital_management.group_hospital_manager"
SYSADMIN = "hospital_management.group_hospital_system_administrator"

RESULT = "hospital.radiology.result"

# Every context key a caller might try, including the one that USED to open
# the release freeze.
FORGED_CONTEXTS = (
    {"skip_radiology_result_write_audit": True},
    {"hospital_radiology_result_workflow_capability": True},
    {"radiology_result_workflow_capability": True},
    {"allow_state_write": True, "workflow": True, "force_state": True},
    {"install_mode": True, "module": "hospital_radiology"},
)


@tagged("post_install", "-at_install", "radiology_result_guard")
class TestRadiologyResultGuard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]
        cls.technician = cls._make_user("rrg_tech", TECHNICIAN)
        cls.radiologist = cls._make_user("rrg_rad", RADIOLOGIST)
        cls.manager = cls._make_user("rrg_mgr", MANAGER)
        cls.doctor_user = cls._make_user("rrg_doc", DOCTOR)
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Result Guard Doctor %s" % tag, "user_id": cls.doctor_user.id}
        )
        cls.other_doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Result Guard Other Doctor %s" % tag}
        )
        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Result Guard Patient %s" % tag}
        )
        cls.other_patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Result Guard Other Patient %s" % tag}
        )
        cls.exam = cls.env["hospital.radiology.exam"].sudo().create(
            {"name": "Result Guard CT %s" % tag, "code": "RGX%s" % tag.upper(),
             "modality": "ct", "body_part": "Brain"}
        )
        cls.second_exam = cls.env["hospital.radiology.exam"].sudo().create(
            {"name": "Result Guard MRI %s" % tag, "code": "RGM%s" % tag.upper(),
             "modality": "mri"}
        )

    @classmethod
    def _make_user(cls, login, group_xmlid):
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s_%s@example.test" % (login, uuid.uuid4().hex[:6]),
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "groups_id": [(6, 0, [cls.env.ref("base.group_user").id, cls.env.ref(group_xmlid).id])],
        })

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    def _request(self, state="in_progress", exams=None, patient=None):
        record = self.env["hospital.radiology.request"].sudo().create({
            "patient_id": (patient or self.patient).id,
            "physician_id": self.doctor.id,
            "line_ids": [(0, 0, {"exam_id": exam.id}) for exam in (exams or [self.exam])],
        })
        if state != "draft":
            # FIXTURE ONLY: a legacy request with no charges, placed in `state`
            # through the request model's own capability.
            with _request_state_capability():
                record.sudo().write({"state": state})
        record.invalidate_recordset()
        return record

    def _report(self, request=None, user=None, **values):
        """A report created the way the desk creates one: a plain ORM create."""
        request = request or self._request()
        Result = self.env[RESULT].with_user(user or self.radiologist)
        return Result.create(dict({"request_id": request.id}, **values))

    def _report_in(self, state, **values):
        """FIXTURE ONLY: a report placed in `state` through the capability, for
        tests about what a report in that state refuses."""
        report = self._report(findings="Initial findings.", impression="Initial.", **values)
        with _result_workflow_capability():
            if state == "cancelled":
                report.sudo().write({"state": "cancelled"})
            else:
                for step in ("entered", "validated", "released"):
                    if report.state == state:
                        break
                    report.sudo().write({"state": step})
        self.assertEqual(report.state, state)
        return report

    def _audit(self, report, action_type=None):
        domain = [("model_name", "=", RESULT), ("record_id", "=", report.id)]
        if action_type:
            domain.append(("action_type", "=", action_type))
        return self.env["hospital.audit.log"].sudo().search(domain, order="id asc")

    # ==================================================================
    # CREATE
    # ==================================================================
    def test_a_report_is_started_only_for_an_in_progress_request(self):
        for state in ("draft", "requested", "scheduled", "completed", "cancelled"):
            request = self._request(state)
            with self.assertRaises(UserError, msg=state):
                self._report(request)
            self.assertFalse(
                self.env[RESULT].sudo().search([("request_id", "=", request.id)]), state
            )

    def test_sudo_cannot_start_a_report_for_a_request_that_has_not_started(self):
        request = self._request("scheduled")
        with self.assertRaises(UserError):
            self.env[RESULT].sudo().create({"request_id": request.id})

    def test_a_new_report_is_a_draft_derived_entirely_from_its_request(self):
        request = self._request(exams=[self.exam, self.second_exam])
        report = self._report(request)
        self.assertEqual(report.state, "draft")
        self.assertNotEqual(report.name, "New")
        self.assertEqual(report.patient_id, request.patient_id)
        self.assertEqual(report.physician_id, request.physician_id)
        self.assertEqual(report.line_ids.mapped("request_line_id"), request.line_ids)
        for line in report.line_ids:
            self.assertEqual(line.exam_id, line.request_line_id.exam_id)

    def test_a_cancelled_study_gets_no_report_line(self):
        request = self._request(exams=[self.exam, self.second_exam])
        cancelled = request.line_ids.filtered(lambda l: l.exam_id == self.second_exam)
        cancelled.sudo().write({"state": "cancelled"})
        report = self._report(request)
        self.assertEqual(report.line_ids.mapped("request_line_id"), request.line_ids - cancelled)

    def test_creating_a_report_records_no_radiologist(self):
        """THE PROVENANCE FIX. Creating the container is not interpreting it."""
        for user in (self.technician, self.radiologist, self.manager):
            report = self._report(user=user)
            self.assertFalse(report.radiologist_id, user.login)

    def test_a_report_cannot_be_created_past_draft(self):
        for state in ("entered", "validated", "released", "cancelled"):
            with self.assertRaises(UserError, msg=state):
                self._report(state=state)

    def test_the_radiologist_cannot_be_chosen_at_creation(self):
        with self.assertRaises(UserError):
            self._report(radiologist_id=self.radiologist.id)
        with self.assertRaises(UserError):
            self.env[RESULT].sudo().create(
                {"request_id": self._request().id, "radiologist_id": self.radiologist.id}
            )

    def test_the_ordering_physician_cannot_be_chosen_at_creation(self):
        with self.assertRaises(UserError):
            self._report(physician_id=self.other_doctor.id)

    def test_a_report_cannot_name_another_patient(self):
        with self.assertRaises(ValidationError):
            self._report(patient_id=self.other_patient.id)

    def test_one_operational_report_per_request(self):
        request = self._request()
        first = self._report(request)
        with self.assertRaises(UserError):
            self._report(request)
        with self.assertRaises(UserError):
            self.env[RESULT].sudo().create({"request_id": request.id})
        self.assertEqual(
            self.env[RESULT].sudo().search([("request_id", "=", request.id)]), first
        )

    def test_a_cancelled_report_makes_room_for_a_new_one_but_cannot_return_beside_it(self):
        request = self._request()
        first = self._report(request)
        first.with_user(self.radiologist).action_cancel()
        second = self._report(request)
        self.assertEqual(second.state, "draft")
        with self.assertRaises(UserError):
            first.with_user(self.manager).action_reset_to_draft()
        first.invalidate_recordset()
        self.assertEqual(first.state, "cancelled")

    def test_an_archived_report_cannot_be_unarchived_beside_an_operational_one(self):
        request = self._request()
        first = self._report(request)
        first.sudo().write({"active": False})
        self._report(request)
        with self.assertRaises(UserError):
            first.sudo().write({"active": True})

    def test_the_technician_opens_the_container_but_writes_no_narrative(self):
        report = self._report(user=self.technician)
        self.assertEqual(report.state, "draft")
        with self.assertRaises(UserError):
            self._report(user=self.technician, findings="Tech impression.")

    def test_a_doctor_is_denied_before_any_workflow_rule(self):
        with self.assertRaises(AccessError):
            self.env[RESULT].with_user(self.doctor_user).create(
                {"request_id": self._request("requested").id}
            )

    # ==================================================================
    # STATE AUTHORITY
    # ==================================================================
    def _assert_state_refused(self, report, new_state, target=None):
        target = target if target is not None else report.sudo()
        before = report.state
        with self.assertRaises(UserError):
            target.write({"state": new_state})
        report.invalidate_recordset()
        self.assertEqual(report.state, before)

    def test_direct_draft_to_entered_is_refused(self):
        self._assert_state_refused(self._report_in("draft"), "entered")

    def test_direct_entered_to_validated_is_refused(self):
        self._assert_state_refused(self._report_in("entered"), "validated")

    def test_direct_validated_to_released_is_refused(self):
        self._assert_state_refused(self._report_in("validated"), "released")

    def test_direct_draft_to_released_is_refused(self):
        self._assert_state_refused(self._report_in("draft"), "released")

    def test_the_guard_binds_every_role(self):
        report = self._report_in("draft")
        for user in (self.radiologist, self.manager, self.technician):
            self._assert_state_refused(report, "entered", report.with_user(user))

    def test_no_context_key_opens_the_state_guard(self):
        report = self._report_in("entered")
        for context in FORGED_CONTEXTS:
            self._assert_state_refused(report, "validated", report.sudo().with_context(**context))
            self._assert_state_refused(
                report, "validated", report.with_user(self.radiologist).with_context(**context)
            )
        self.assertFalse(has_result_workflow_capability())

    def test_the_capability_is_released_even_when_the_guarded_write_raises(self):
        report = self._report_in("draft")
        with self.assertRaises(ZeroDivisionError):
            with _result_workflow_capability():
                self.assertTrue(has_result_workflow_capability())
                raise ZeroDivisionError
        self.assertFalse(has_result_workflow_capability())
        self._assert_state_refused(report, "entered")

    def test_the_controlled_write_path_is_private(self):
        """Underscore methods are refused by Odoo's RPC dispatcher."""
        Result = self.env[RESULT]
        for name in ("_workflow_write", "_write_state"):
            self.assertTrue(hasattr(Result, name), name)
            self.assertFalse(hasattr(Result, name.lstrip("_")), name)

    def test_the_radiologist_cannot_be_written_directly(self):
        report = self._report_in("draft")
        for target in (report.sudo(), report.with_user(self.radiologist)):
            with self.assertRaises(UserError):
                target.write({"radiologist_id": self.radiologist.id})
        report.invalidate_recordset()
        self.assertFalse(report.radiologist_id)

    # ==================================================================
    # PROVENANCE
    # ==================================================================
    def test_the_request_patient_and_physician_never_change(self):
        report = self._report_in("draft")
        other_request = self._request(patient=self.other_patient)
        attempts = (
            {"request_id": other_request.id},
            {"patient_id": self.other_patient.id},
            {"physician_id": self.other_doctor.id},
            {"request_id": False},
        )
        for vals in attempts:
            for target in (
                report.sudo(),
                report.with_user(self.radiologist),
                report.sudo().with_context(skip_radiology_result_write_audit=True),
            ):
                with self.assertRaises(UserError, msg=str(vals)):
                    with self.env.cr.savepoint():
                        target.write(vals)
            # Not even the workflow capability opens provenance.
            with self.assertRaises(UserError, msg=str(vals)):
                with self.env.cr.savepoint(), _result_workflow_capability():
                    report.sudo().write(vals)
        report.invalidate_recordset()
        self.assertEqual(
            (report.request_id.patient_id, report.patient_id, report.physician_id),
            (self.patient, self.patient, self.doctor),
        )

    def test_writing_the_same_provenance_is_not_a_change(self):
        report = self._report_in("draft")
        report.sudo().write({
            "request_id": report.request_id.id,
            "patient_id": report.patient_id.id,
            "physician_id": report.physician_id.id,
        })

    # ==================================================================
    # AUTHORSHIP
    # ==================================================================
    def test_only_a_report_author_writes_the_narrative(self):
        report = self._report_in("draft")
        for field_name in ("findings", "impression", "recommendations"):
            with self.assertRaises(UserError, msg=field_name):
                report.with_user(self.technician).write({field_name: "Tech wording."})
        report.with_user(self.radiologist).write({"findings": "Radiologist wording."})
        report.with_user(self.manager).write({"impression": "Manager wording."})
        self.assertEqual(
            (report.findings, report.impression),
            ("Radiologist wording.", "Manager wording."),
        )

    def test_the_technician_cannot_write_report_lines(self):
        report = self._report_in("draft")
        with self.assertRaises(AccessError):
            report.line_ids.with_user(self.technician).write({"result_summary": "Tech."})

    def test_the_technician_cannot_mark_a_report_entered(self):
        report = self._report_in("draft")
        with self.assertRaises(UserError):
            report.with_user(self.technician).action_mark_entered()
        self.assertEqual(report.state, "draft")

    # ==================================================================
    # COMPLETENESS
    # ==================================================================
    def _draft(self, **values):
        report = self._report()
        if values:
            report.with_user(self.radiologist).write(values)
        return report

    def test_an_empty_report_cannot_be_entered(self):
        report = self._draft()
        with self.assertRaises(ValidationError):
            report.with_user(self.radiologist).action_mark_entered()
        self.assertEqual((report.state, report.radiologist_id.id), ("draft", False))

    def test_whitespace_is_not_a_report(self):
        report = self._draft(findings="   \n\t ", impression="  ")
        with self.assertRaises(ValidationError):
            report.with_user(self.radiologist).action_mark_entered()

    def test_recommendations_alone_are_not_a_report(self):
        report = self._draft(recommendations="Follow up in six weeks.")
        with self.assertRaises(ValidationError):
            report.with_user(self.radiologist).action_mark_entered()

    def test_findings_or_impression_alone_is_enough(self):
        for values in ({"findings": "No acute abnormality."}, {"impression": "Normal study."}):
            report = self._draft(**values)
            report.with_user(self.radiologist).action_mark_entered()
            self.assertEqual(report.state, "entered", values)

    def test_a_per_study_summary_is_not_required(self):
        report = self._draft(impression="Normal study.")
        self.assertFalse(any(report.line_ids.mapped("result_summary")))
        report.with_user(self.radiologist).action_mark_entered()
        self.assertEqual(report.state, "entered")

    def test_a_report_with_no_study_line_cannot_be_entered(self):
        request = self._request()
        # A legacy shape: a report whose lines were never built.
        with _result_workflow_capability():
            report = self.env[RESULT].sudo().create(
                {"request_id": request.id, "line_ids": [(5, 0, 0)]}
            )
        self.assertFalse(report.line_ids)
        report.with_user(self.radiologist).write({"impression": "Normal."})
        with self.assertRaises(ValidationError):
            report.with_user(self.radiologist).action_mark_entered()

    # ==================================================================
    # ENTRY AND PROVENANCE
    # ==================================================================
    def test_a_report_authors_sudo_call_still_records_the_author(self):
        report = self._draft(impression="Normal.")
        report.with_user(self.radiologist).sudo().action_mark_entered()
        self.assertEqual(report.radiologist_id, self.radiologist)

    def test_entry_records_the_author_as_the_reporting_radiologist(self):
        for user in (self.radiologist, self.manager):
            report = self._draft(impression="Normal.")
            report.with_user(user).action_mark_entered()
            self.assertEqual(report.radiologist_id, user, user.login)
            audit = self._audit(report, "state_change")
            self.assertEqual(
                (audit[-1].old_value, audit[-1].new_value),
                ("State: draft", "State: entered"),
            )

    def test_superuser_entry_records_no_system_user(self):
        """Genuine superuser code -- self.env here -- records nobody. A report
        author's sudo()'d call still records the author: sudo() keeps env.user,
        and the author is who is entering it."""
        report = self._draft(impression="Normal.")
        self.env[RESULT].browse(report.id).action_mark_entered()
        self.assertEqual(report.state, "entered")
        self.assertFalse(report.radiologist_id)

    def test_an_entered_report_cannot_be_entered_again(self):
        report = self._draft(impression="Normal.")
        report.with_user(self.radiologist).action_mark_entered()
        with self.assertRaises(UserError):
            report.with_user(self.radiologist).action_mark_entered()

    # ==================================================================
    # THE FREEZE
    # ==================================================================
    def test_an_entered_report_and_everything_after_it_is_frozen(self):
        for state in ("entered", "validated", "released", "cancelled"):
            report = self._report_in(state)
            for vals in (
                {"findings": "Changed."},
                {"impression": "Changed."},
                {"recommendations": "Changed."},
                {"result_date": "2020-01-01"},
                {"line_ids": [(1, report.line_ids[:1].id, {"notes": "Changed."})]},
            ):
                for target in (report.sudo(), report.with_user(self.radiologist)):
                    with self.assertRaises(UserError, msg="%s %s" % (state, vals)):
                        with self.env.cr.savepoint():
                            target.write(vals)
            report.invalidate_recordset()
            self.assertEqual(report.findings, "Initial findings.", state)

    def test_frozen_lines_refuse_direct_writes(self):
        for state in ("entered", "validated", "released"):
            line = self._report_in(state).line_ids[:1]
            for vals in ({"result_summary": "Changed."}, {"notes": "Changed."},
                         {"body_part": "Chest"}):
                with self.assertRaises(UserError, msg="%s %s" % (state, vals)):
                    with self.env.cr.savepoint():
                        line.sudo().write(vals)

    def test_no_context_key_opens_the_freeze(self):
        """skip_radiology_result_write_audit USED to open the release freeze."""
        for state in ("entered", "released"):
            report = self._report_in(state)
            for context in FORGED_CONTEXTS:
                with self.assertRaises(UserError, msg="%s %s" % (state, context)):
                    with self.env.cr.savepoint():
                        report.sudo().with_context(**context).write({"findings": "Changed."})
                with self.assertRaises(UserError, msg="%s %s" % (state, context)):
                    with self.env.cr.savepoint():
                        report.line_ids.sudo().with_context(**context).write({"notes": "Changed."})

    def test_not_even_the_workflow_capability_opens_the_content(self):
        report = self._report_in("validated")
        with self.assertRaises(UserError):
            with _result_workflow_capability():
                report.sudo().write({"impression": "Changed."})

    def test_a_frozen_report_can_still_be_archived(self):
        report = self._report_in("released")
        report.sudo().write({"active": False})
        self.assertFalse(report.active)

    def test_writing_unchanged_text_to_a_frozen_report_is_not_a_change(self):
        report = self._report_in("entered")
        report.sudo().write({"findings": "Initial findings."})

    def test_frozen_lines_cannot_be_added_or_removed(self):
        report = self._report_in("entered")
        request_line = report.request_id.line_ids[:1]
        with self.assertRaises(UserError):
            self.env["hospital.radiology.result.line"].sudo().create({
                "result_id": report.id,
                "exam_id": request_line.exam_id.id,
                "request_line_id": request_line.id,
            })
        with self.assertRaises(UserError):
            report.line_ids.sudo().unlink()

    # ==================================================================
    # LINE STRUCTURE
    # ==================================================================
    def test_a_draft_line_keeps_its_structure(self):
        report = self._report_in("draft")
        line = report.line_ids[:1]
        other = self._request(exams=[self.second_exam])
        for vals in (
            {"exam_id": self.second_exam.id},
            {"request_line_id": other.line_ids.id},
            {"request_line_id": False},
            {"result_id": self._report_in("draft").id},
        ):
            with self.assertRaises(UserError, msg=str(vals)):
                with self.env.cr.savepoint():
                    line.sudo().write(vals)

    def test_a_draft_line_accepts_its_text(self):
        report = self._report_in("draft")
        line = report.line_ids[:1]
        line.with_user(self.radiologist).write({"result_summary": "Normal.", "notes": "No contrast."})
        self.assertEqual((line.result_summary, line.notes), ("Normal.", "No contrast."))

    def test_a_line_must_report_on_a_study_of_its_own_request(self):
        report = self._report_in("draft")
        foreign = self._request(exams=[self.second_exam])
        with self.assertRaises(ValidationError):
            self.env["hospital.radiology.result.line"].sudo().create({
                "result_id": report.id,
                "exam_id": self.second_exam.id,
                "request_line_id": foreign.line_ids.id,
            })

    def test_a_free_standing_line_is_refused(self):
        report = self._report_in("draft")
        with self.assertRaises(UserError):
            self.env["hospital.radiology.result.line"].sudo().create({
                "result_id": report.id,
                "exam_id": self.exam.id,
            })

    # ==================================================================
    # THE WORKFLOW STILL WORKS
    # ==================================================================
    def test_cancel_and_reset_still_work_through_the_workflow(self):
        report = self._draft(impression="Normal.")
        report.with_user(self.radiologist).action_cancel()
        self.assertEqual(report.state, "cancelled")
        report.with_user(self.manager).action_reset_to_draft()
        self.assertEqual(report.state, "draft")
        values = self._audit(report, "state_change").mapped("new_value")
        self.assertIn("State: cancelled", values)
        self.assertIn("State: draft", values)

    def test_draft_text_edits_are_audited(self):
        report = self._report_in("draft")
        before = len(self._audit(report, "update"))
        report.with_user(self.radiologist).write({"findings": "Edited."})
        self.assertEqual(len(self._audit(report, "update")), before + 1)

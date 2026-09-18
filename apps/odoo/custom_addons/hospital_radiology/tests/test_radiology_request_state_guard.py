"""Radiology Slice 2: request `state` moves only through workflow methods.

WHAT THIS PINS
--------------
Before this guard, hospital.radiology.request.write() accepted any `state`, so
anyone holding write access -- a doctor on their own order, a desk role, sudo
code, an RPC client -- could move a study to in_progress and skip
hospital_billing's clearance gate, its charge moves, the encounter start and the
transition audit.

Now:
  * a direct state CHANGE is refused on every channel, sudo included;
  * no context key opens it, whatever the caller sends;
  * a request cannot be CREATED in a later state either;
  * the workflow methods still work, and still write their audit rows;
  * the capability is released even when the guarded write raises.

The transitions that need hospital_billing's charges (confirmation, Mark In
Progress) are exercised end to end by yoya_emr_api's Radiology Desk transition
tests; this module pins the guard itself with transitions that need no money.
"""
import uuid

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from ..models.radiology_request import (
    _request_state_capability,
    has_request_state_capability,
)

TECHNICIAN = "hospital_radiology.group_hospital_radiology_technician"
DOCTOR = "hospital_management.group_hospital_doctor"
MANAGER = "hospital_management.group_hospital_manager"

# Every context key a caller might try, including ones named after the guard.
FORGED_CONTEXTS = (
    {"skip_radiology_request_write_audit": True},
    {"hospital_radiology_request_state_capability": True},
    {"radiology_request_state_capability": True},
    {"allow_state_write": True, "workflow": True, "force_state": True},
    {"install_mode": True, "module": "hospital_radiology"},
)


@tagged("post_install", "-at_install", "radiology_request_state_guard")
class TestRadiologyRequestStateGuard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]
        cls.technician = cls._make_user("rsg_tech", TECHNICIAN)
        cls.manager = cls._make_user("rsg_mgr", MANAGER)
        cls.doctor_user = cls._make_user("rsg_doc", DOCTOR)
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "State Guard Doctor %s" % tag, "user_id": cls.doctor_user.id}
        )
        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "State Guard Patient %s" % tag}
        )
        cls.exam = cls.env["hospital.radiology.exam"].sudo().create(
            {"name": "State Guard Exam %s" % tag, "code": "SGX%s" % tag.upper(), "modality": "xray"}
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

    def _request(self, state="draft"):
        record = self.env["hospital.radiology.request"].sudo().create({
            "patient_id": self.patient.id,
            "physician_id": self.doctor.id,
            "line_ids": [(0, 0, {"exam_id": self.exam.id})],
        })
        if state != "draft":
            # FIXTURE ONLY: the tests below are about direct writes, so the
            # starting state is placed through the model's own capability.
            with _request_state_capability():
                record.sudo().write({"state": state})
        record.invalidate_recordset()
        self.assertEqual(record.state, state)
        return record

    def _audit(self, record):
        return self.env["hospital.audit.log"].sudo().search([
            ("model_name", "=", "hospital.radiology.request"),
            ("record_id", "=", record.id),
            ("action_type", "=", "state_change"),
        ], order="id asc")

    # ------------------------------------------------------------------
    # Direct writes are refused
    # ------------------------------------------------------------------
    def _assert_refused(self, record, new_state, env_record=None):
        target = env_record if env_record is not None else record.sudo()
        before = record.state
        with self.assertRaises(UserError):
            target.write({"state": new_state})
        record.invalidate_recordset()
        self.assertEqual(record.state, before)

    def test_direct_requested_to_scheduled_is_refused(self):
        self._assert_refused(self._request("requested"), "scheduled")

    def test_direct_scheduled_to_in_progress_is_refused(self):
        self._assert_refused(self._request("scheduled"), "in_progress")

    def test_direct_requested_to_in_progress_is_refused(self):
        self._assert_refused(self._request("requested"), "in_progress")

    def test_direct_draft_to_completed_is_refused(self):
        self._assert_refused(self._request("draft"), "completed")

    def test_the_guard_binds_sudo_and_every_desk_role(self):
        record = self._request("requested")
        for user in (self.technician, self.manager):
            self._assert_refused(record, "scheduled", record.with_user(user))
        self._assert_refused(record, "scheduled", record.sudo())

    def test_no_context_key_opens_the_guard(self):
        record = self._request("scheduled")
        for context in FORGED_CONTEXTS:
            self._assert_refused(record, "in_progress", record.sudo().with_context(**context))
            self._assert_refused(
                record, "in_progress", record.with_user(self.technician).with_context(**context)
            )
        self.assertFalse(has_request_state_capability())

    def test_a_doctor_cannot_move_their_own_order_by_direct_write(self):
        record = self._request("requested")
        own = record.with_user(self.doctor_user)
        self.assertEqual(own.read(["state"])[0]["state"], "requested", "the doctor can read it")
        for new_state in ("scheduled", "in_progress", "completed"):
            # The guard runs before any access check, so the refusal is the guard's.
            with self.assertRaises(UserError, msg=new_state):
                own.write({"state": new_state})
        record.invalidate_recordset()
        self.assertEqual(record.state, "requested")

    def test_writing_the_current_state_is_not_a_transition(self):
        record = self._request("requested")
        record.sudo().write({"state": "requested", "priority": "urgent"})
        self.assertEqual((record.state, record.priority), ("requested", "urgent"))

    def test_other_fields_stay_writable(self):
        record = self._request("scheduled")
        record.with_user(self.technician).write({"instructions": "Bring prior films."})
        self.assertEqual(record.instructions, "Bring prior films.")

    def test_a_request_cannot_be_created_past_draft(self):
        for state in ("requested", "scheduled", "in_progress", "completed", "cancelled"):
            with self.assertRaises(UserError, msg=state):
                self.env["hospital.radiology.request"].sudo().create({
                    "patient_id": self.patient.id,
                    "physician_id": self.doctor.id,
                    "state": state,
                })

    def test_a_duplicate_starts_as_a_draft(self):
        record = self._request("scheduled")
        duplicate = record.sudo().copy()
        self.assertEqual(duplicate.state, "draft")

    def test_the_capability_is_released_even_when_the_guarded_write_raises(self):
        record = self._request("requested")
        with self.assertRaises(ZeroDivisionError):
            with _request_state_capability():
                self.assertTrue(has_request_state_capability())
                raise ZeroDivisionError
        self.assertFalse(has_request_state_capability())
        self._assert_refused(record, "scheduled")

    def test_the_controlled_write_path_is_private(self):
        """Underscore methods are refused by Odoo's RPC dispatcher."""
        Request = self.env["hospital.radiology.request"]
        self.assertTrue(hasattr(Request, "_workflow_write"))
        self.assertTrue(hasattr(Request, "_write_state"))
        self.assertFalse(hasattr(Request, "workflow_write"))
        self.assertFalse(hasattr(Request, "write_state"))

    # ------------------------------------------------------------------
    # The workflow methods still work, with their audit
    # ------------------------------------------------------------------
    def test_action_schedule_still_moves_requested_to_scheduled_with_audit(self):
        record = self._request("requested")
        before = len(self._audit(record))
        record.with_user(self.technician).action_schedule()
        self.assertEqual(record.state, "scheduled")
        audit = self._audit(record)
        self.assertEqual(len(audit), before + 1)
        self.assertEqual(audit[-1].old_value, "State: requested")
        self.assertEqual(audit[-1].new_value, "State: scheduled")
        self.assertFalse(has_request_state_capability())

    def test_action_schedule_still_refuses_the_wrong_state(self):
        record = self._request("scheduled")
        with self.assertRaises(UserError):
            record.sudo().action_schedule()
        self.assertEqual(record.state, "scheduled")

    def test_cancel_and_reset_still_work_through_the_helper(self):
        record = self._request("requested")
        record.sudo().action_cancel()
        self.assertEqual(record.state, "cancelled")
        record.sudo().action_reset_to_draft()
        self.assertEqual(record.state, "draft")
        new_values = self._audit(record).mapped("new_value")
        self.assertIn("State: cancelled", new_values)
        self.assertIn("State: draft", new_values)

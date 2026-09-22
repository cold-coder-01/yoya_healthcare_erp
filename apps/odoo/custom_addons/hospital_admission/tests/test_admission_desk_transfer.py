"""Admissions Slice 3: desk TRANSFER and desk CANCEL REQUEST, at the model.

Every call runs inside a savepoint, the way the HTTP layer runs it, so a
refused call is checked for leaving NOTHING behind: no bed moved, no history
row, no revision, no operation row.

CONCURRENCY, AS SLICE 2 TESTS IT. A TransactionCase shares one cursor, so two
real transactions cannot be raced here. What decides every race is (1) the
revision and replay checks made on LOCKED rows, and (2) the lock ORDER. The
first is exercised by running the losing request second with the state the
winner left; the second by capturing the SQL the lock helper issues for a
mirror-image pair.
"""
import uuid
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    DESK_TRANSFER_GROUPS,
    AdmissionWorkflowError,
)

from .common import G_DPO
from .test_admission_desk_mutations import AdmissionDeskMutationCase


def token():
    return str(uuid.uuid4())


@tagged("post_install", "-at_install", "admission_desk_transfer")
class DeskTransferCase(AdmissionDeskMutationCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.bed_c = cls._make_bed(cls.room, "S3-A-101-3")
        # hospital_billing is a dependency, so its Cashier group always exists.
        cls.cashier = cls._make_user("adm_cashier", ["hospital_billing.group_hospital_cashier"])
        cls.dpo = cls._make_user("adm_dpo", [G_DPO])
        front_desk = cls.env.ref("yoya_reception_bridge.group_hospital_front_desk_nurse", raise_if_not_found=False)
        cls.front_desk_nurse = (
            cls._make_user("adm_fdn", ["yoya_reception_bridge.group_hospital_front_desk_nurse"])
            if front_desk else None
        )

    def _inpatient(self, bed=None, **kwargs):
        """Requested by the doctor, admitted by the clerk: the real path."""
        appointment, encounter = self._visit(**kwargs)
        admission, _ = self._request(appointment)
        admission = admission.sudo()
        self._admit(admission, bed or self.bed_a)
        return admission, appointment, encounter

    def _transfer(self, admission, bed, user=None, op=None, revision=None, reason="Closer to the nursing station"):
        user = user or self.receptionist
        record = admission.with_user(user)
        with self.env.cr.savepoint():
            return record._desk_transfer(
                bed.id if bed else None,
                reason,
                op or token(),
                admission.workflow_revision if revision is None else revision,
            )

    def _cancel(self, admission, user=None, op=None, revision=None):
        user = user or self.receptionist
        record = admission.with_user(user)
        with self.env.cr.savepoint():
            return record._desk_cancel_request(
                op or token(),
                admission.workflow_revision if revision is None else revision,
            )

    def _history(self, admission):
        return self.env["hospital.admission.transfer"].sudo().search(
            [("admission_id", "=", admission.id)]
        )

    def _assert_unmoved(self, admission, bed, revision):
        self.env.invalidate_all()
        self.assertEqual(admission.bed_id, bed)
        self.assertEqual(bed.state, "occupied")
        self.assertEqual(bed.current_admission_id, admission)
        self.assertEqual(admission.workflow_revision, revision)


@tagged("post_install", "-at_install", "admission_desk_transfer")
class TestDeskTransfer(DeskTransferCase):
    def test_a_transfer_moves_the_patient_and_records_everything_once(self):
        admission, _, encounter = self._inpatient(self.bed_a)
        self.assertEqual(admission.workflow_revision, 2)
        result, replayed = self._transfer(admission, self.bed_other)
        self.assertFalse(replayed)
        self.assertEqual(result, admission)
        self.env.invalidate_all()

        self.assertEqual(admission.state, "transferred")
        self.assertEqual(
            (admission.ward_id, admission.room_id, admission.bed_id),
            (self.ward_other, self.room_other, self.bed_other),
        )
        self.assertEqual(self.bed_a.state, "available")
        self.assertFalse(self.bed_a.current_admission_id)
        self.assertEqual(self.bed_other.state, "occupied")
        self.assertEqual(self.bed_other.current_admission_id, admission)
        self.assertEqual(admission.workflow_revision, 3)
        self.assertEqual(admission.encounter_id, encounter, "same episode, no new visit")

        history = self._history(admission)
        self.assertEqual(len(history), 1)
        self.assertEqual(
            (history.from_ward_id, history.from_room_id, history.from_bed_id),
            (self.ward, self.room, self.bed_a),
        )
        self.assertEqual(
            (history.to_ward_id, history.to_room_id, history.to_bed_id),
            (self.ward_other, self.room_other, self.bed_other),
        )
        self.assertEqual(history.transferred_by, self.receptionist)
        self.assertEqual(history.reason, "Closer to the nursing station")
        self.assertTrue(history.transfer_date)
        self.assertTrue(history.rate_snapshot_taken)
        self.assertEqual(history.to_daily_rate, 800.0)
        self.assertEqual(
            sorted(self._ops(admission).mapped("operation_type")), ["admit", "request", "transfer"]
        )
        # No second admission was made for the move.
        self.assertEqual(
            self.env["hospital.admission"].sudo().search_count(
                [("patient_id", "=", admission.patient_id.id), ("state", "in", ("admitted", "transferred"))]
            ),
            1,
        )

    def test_a_transferred_patient_may_move_again(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self._transfer(admission, self.bed_b)
        self._transfer(admission, self.bed_other)
        self.env.invalidate_all()
        self.assertEqual(admission.bed_id, self.bed_other)
        self.assertEqual(len(self._history(admission)), 2)
        self.assertEqual(admission.workflow_revision, 4)
        segments, reasons = admission._stay_segments()
        self.assertEqual([s["bed"] for s in segments], [self.bed_a, self.bed_b, self.bed_other])
        self.assertEqual(reasons, [])

    # ------------------------------------------------------------------
    # Authority
    # ------------------------------------------------------------------
    def test_only_the_admissions_clerk_manager_and_admin_may_transfer(self):
        admission, _, _ = self._inpatient(self.bed_a)
        refused = [self.doctor_user, self.nurse, self.pharmacist, self.lab_tech, self.accountant, self.dpo]
        refused += [user for user in (self.cashier, self.front_desk_nurse) if user]
        for user in refused:
            with self.subTest(user=user.login):
                self.assertEqual(
                    self._code(self._transfer, admission, self.bed_b, user=user),
                    "admission_not_authorized",
                )
        self._assert_unmoved(admission, self.bed_a, 2)
        for user, bed in ((self.manager, self.bed_b), (self.admin, self.bed_c), (self.receptionist, self.bed_a)):
            with self.subTest(user=user.login):
                self._transfer(admission, bed, user=user)
        self.assertEqual(len(DESK_TRANSFER_GROUPS), 3)

    def test_the_backend_transfer_obeys_the_same_rule(self):
        """action_transfer() -- and so the backend wizard -- checks the role
        itself; the desk gate is not the only control."""
        admission, _, _ = self._inpatient(self.bed_a)
        for user in (self.doctor_user, self.nurse):
            with self.subTest(user=user.login), self.assertRaises(AdmissionWorkflowError) as caught:
                with self.env.cr.savepoint():
                    admission.with_user(user).action_transfer(
                        to_ward=self.ward, to_room=self.room, to_bed=self.bed_b
                    )
            self.assertEqual(caught.exception.code, "admission_transfer_not_authorized")
        self._assert_unmoved(admission, self.bed_a, 2)

    def test_the_payload_is_validated(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self.assertEqual(self._code(self._transfer, admission, None), "admission_bed_required")
        self.assertEqual(self._code(self._transfer, admission, self.bed_b, reason="  "), "admission_invalid_payload")
        self.assertEqual(self._code(self._transfer, admission, self.bed_b, reason=None), "admission_invalid_payload")
        self.assertEqual(self._code(self._transfer, admission, self.bed_b, op="nope"), "admission_invalid_payload")
        self.assertEqual(self._code(self._transfer, admission, self.bed_b, revision=-1), "admission_invalid_payload")
        self.assertEqual(self._code(self._transfer, admission, self.bed_a), "admission_bed_unavailable")
        self._assert_unmoved(admission, self.bed_a, 2)

    def test_a_draft_cannot_be_transferred(self):
        appointment, _ = self._visit()
        draft, _ = self._request(appointment)
        self.assertEqual(self._code(self._transfer, draft.sudo(), self.bed_b), "admission_invalid_state")

    # ------------------------------------------------------------------
    # Idempotency and revisions
    # ------------------------------------------------------------------
    def test_the_same_token_and_payload_replays_without_moving_again(self):
        admission, _, _ = self._inpatient(self.bed_a)
        op = token()
        self._transfer(admission, self.bed_b, op=op, revision=2)
        _, replayed = self._transfer(admission, self.bed_b, op=op, revision=2)
        self.assertTrue(replayed)
        self.env.invalidate_all()
        self.assertEqual(admission.workflow_revision, 3)
        self.assertEqual(len(self._history(admission)), 1)
        self.assertEqual(len(self._ops(admission).filtered(lambda o: o.operation_type == "transfer")), 1)

    def test_the_same_token_for_another_destination_is_a_conflict(self):
        admission, _, _ = self._inpatient(self.bed_a)
        op = token()
        self._transfer(admission, self.bed_b, op=op, revision=2)
        self.assertEqual(
            self._code(self._transfer, admission, self.bed_other, op=op, revision=2),
            "admission_operation_conflict",
        )
        self.env.invalidate_all()
        self.assertEqual(admission.bed_id, self.bed_b)
        self.assertEqual(self.bed_other.state, "available")

    def test_a_stale_revision_is_refused_and_nothing_moves(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self.assertEqual(self._code(self._transfer, admission, self.bed_b, revision=1), "admission_revision_conflict")
        self._assert_unmoved(admission, self.bed_a, 2)
        self.assertEqual(self.bed_b.state, "available")
        self.assertFalse(self._history(admission))

    # ------------------------------------------------------------------
    # PART 17: concurrency
    # ------------------------------------------------------------------
    def test_two_transfers_of_the_same_admission(self):
        """Both clerks loaded revision 2; the first wins, the second is told
        the admission changed -- it does not move the patient a second time."""
        admission, _, _ = self._inpatient(self.bed_a)
        self._transfer(admission, self.bed_b, revision=2)
        self.assertEqual(
            self._code(self._transfer, admission, self.bed_other, revision=2, user=self.manager),
            "admission_revision_conflict",
        )
        self.env.invalidate_all()
        self.assertEqual(admission.bed_id, self.bed_b)
        self.assertEqual(self.bed_other.state, "available")
        self.assertEqual(len(self._history(admission)), 1)

    def test_two_admissions_targeting_the_same_bed(self):
        first, _, _ = self._inpatient(self.bed_a)
        second, _, _ = self._inpatient(self.bed_b)
        self._transfer(first, self.bed_c)
        self.assertEqual(self._code(self._transfer, second, self.bed_c), "admission_bed_conflict")
        self._assert_unmoved(second, self.bed_b, 2)
        self.assertEqual(self.bed_c.current_admission_id, first)

    def test_a_mirror_image_swap_cannot_deadlock_or_double_book(self):
        """A in bed_a wants bed_b; B in bed_b wants bed_a. Neither bed is
        free, so both are refused cleanly -- and both lock the SAME two beds in
        the SAME ascending order, which is why two real clerks attempting this
        at once queue instead of deadlocking."""
        a, _, _ = self._inpatient(self.bed_a)
        b, _, _ = self._inpatient(self.bed_b)
        self.assertEqual(self._code(self._transfer, a, self.bed_b), "admission_bed_conflict")
        self.assertEqual(self._code(self._transfer, b, self.bed_a), "admission_bed_conflict")
        self._assert_unmoved(a, self.bed_a, 2)
        self._assert_unmoved(b, self.bed_b, 2)

        orders = []
        original = self.cr.execute

        def recording(query, params=None, *args, **kwargs):
            if "pg_advisory_xact_lock" in str(query) and params and "bed.occupancy" in str(params[0]):
                orders[-1].append(int(str(params[0]).rsplit(":", 1)[1]))
            return original(query, params, *args, **kwargs)

        self.patch(self.cr, "execute", recording)
        for mover, target in ((a, self.bed_b), (b, self.bed_a)):
            orders.append([])
            self._code(self._transfer, mover, target)
        self.assertEqual(orders[0][:2], sorted([self.bed_a.id, self.bed_b.id]))
        self.assertEqual(orders[1][:2], sorted([self.bed_a.id, self.bed_b.id]))

        # The swap done properly: through a free bed.
        self._transfer(a, self.bed_c)
        self._transfer(b, self.bed_a)
        self._transfer(a, self.bed_b)
        self.env.invalidate_all()
        self.assertEqual((a.bed_id, b.bed_id), (self.bed_b, self.bed_a))
        self.assertEqual(self.bed_c.state, "available")

    def test_a_bed_that_changed_after_the_screen_loaded_is_refused(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self.bed_b._set_occupancy("cleaning")
        self.assertEqual(self._code(self._transfer, admission, self.bed_b), "admission_bed_unavailable")
        self.bed_b._set_occupancy("available")
        self.bed_b.sudo().write({"active": False})
        self.assertEqual(self._code(self._transfer, admission, self.bed_b), "admission_bed_unavailable")
        self._assert_unmoved(admission, self.bed_a, 2)

    def test_a_patient_moved_elsewhere_meanwhile_is_a_conflict(self):
        """The screen was loaded when the patient was in bed_a; someone moved
        them through the back office, which does not bump the desk revision.
        The desk sees the bed it locked is not the patient's any more."""
        admission, _, _ = self._inpatient(self.bed_a)
        Admission = type(self.env["hospital.admission"])
        original = Admission._lock_for_occupancy
        moved = []

        def move_before_the_lock(records, beds):
            # The desk has already read bed_a and chosen its lock set. Before
            # the lock is granted, a back-office transfer lands.
            if not moved:
                moved.append(True)
                admission.sudo().action_transfer(
                    to_ward=self.ward, to_room=self.room, to_bed=self.bed_b
                )
            return original(records, beds)

        with patch.object(Admission, "_lock_for_occupancy", move_before_the_lock):
            code = self._code(self._transfer, admission, self.bed_c, revision=2)
        self.assertEqual(moved, [True])
        self.assertEqual(code, "admission_revision_conflict")
        self.env.invalidate_all()
        self.assertEqual(self.bed_c.state, "available")

    def test_an_old_bed_this_admission_does_not_hold_is_not_released(self):
        a, _, _ = self._inpatient(self.bed_a)
        b, _, _ = self._inpatient(self.bed_b)
        # Corrupt the pointer: bed_a claims B.
        self._raw(
            "UPDATE hospital_bed SET current_admission_id = %s WHERE id = %s", (b.id, self.bed_a.id)
        )
        self.assertEqual(self._code(self._transfer, a, self.bed_c), "admission_integrity_error")
        self.env.invalidate_all()
        self.assertEqual(self.bed_a.current_admission_id, b)
        self.assertEqual(self.bed_c.state, "available")
        self.assertEqual(a.bed_id, self.bed_a)
        self.assertFalse(self._history(a))

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------
    def _assert_rolled_back(self, admission):
        self.env.invalidate_all()
        self.assertEqual(admission.state, "admitted")
        self.assertEqual(admission.bed_id, self.bed_a)
        self.assertEqual(self.bed_a.state, "occupied")
        self.assertEqual(self.bed_a.current_admission_id, admission)
        self.assertEqual(self.bed_b.state, "available")
        self.assertFalse(self.bed_b.current_admission_id)
        self.assertEqual(admission.workflow_revision, 2)
        self.assertFalse(self._history(admission))
        self.assertNotIn("transfer", self._ops(admission).mapped("operation_type"))

    def test_a_failure_after_the_history_row_rolls_everything_back(self):
        admission, _, _ = self._inpatient(self.bed_a)
        Transfer = type(self.env["hospital.admission.transfer"])
        original = Transfer.create

        def create_then_fail(records, vals_list):
            original(records, vals_list)
            raise UserError("boom after history")

        with patch.object(Transfer, "create", create_then_fail):
            with self.assertRaises(UserError):
                self._transfer(admission, self.bed_b)
        self._assert_rolled_back(admission)

    def test_a_failure_taking_the_rate_snapshot_rolls_everything_back(self):
        admission, _, _ = self._inpatient(self.bed_a)
        with patch(
            "odoo.addons.hospital_admission.models.admission.resolve_location_rate",
            side_effect=UserError("boom in snapshot"),
        ):
            with self.assertRaises(UserError):
                self._transfer(admission, self.bed_b)
        self._assert_rolled_back(admission)

    def test_a_failure_recording_the_operation_rolls_everything_back(self):
        admission, _, _ = self._inpatient(self.bed_a)
        with patch.object(type(self.env["hospital.admission"]), "_desk_record_operation",
                          side_effect=UserError("boom at the ledger")):
            with self.assertRaises(UserError):
                self._transfer(admission, self.bed_b)
        self._assert_rolled_back(admission)

    # ------------------------------------------------------------------
    # PART 14: ward nurse handoff
    # ------------------------------------------------------------------
    def test_the_old_ward_nurse_loses_the_patient_and_the_new_one_gains_them(self):
        admission, _, _ = self._inpatient(self.bed_a)              # nurse's ward
        Admission = self.env["hospital.admission"]
        self.assertEqual(Admission.with_user(self.nurse).search([("id", "=", admission.id)]), admission)
        self.assertFalse(Admission.with_user(self.other_nurse).search([("id", "=", admission.id)]))

        self._transfer(admission, self.bed_other)                  # other_nurse's ward
        self.env.invalidate_all()
        self.assertFalse(Admission.with_user(self.nurse).search([("id", "=", admission.id)]))
        theirs = Admission.with_user(self.other_nurse).search([("id", "=", admission.id)])
        self.assertEqual(theirs, admission, "the SAME admission reference")
        self.assertEqual(theirs.bed_id, self.bed_other)


@tagged("post_install", "-at_install", "admission_desk_transfer")
class TestDeskCancelRequest(DeskTransferCase):
    def _requested(self, **kwargs):
        appointment, encounter = self._visit(**kwargs)
        admission, _ = self._request(appointment)
        return admission.sudo(), appointment, encounter

    def test_the_requesting_doctor_cancels_their_own_request(self):
        admission, _, _ = self._requested()
        result, replayed = self._cancel(admission, user=self.doctor_user)
        self.assertFalse(replayed)
        self.assertEqual(result, admission)
        self.env.invalidate_all()
        self.assertEqual(admission.state, "cancelled")
        self.assertEqual(admission.workflow_revision, 2)
        self.assertEqual(sorted(self._ops(admission).mapped("operation_type")), ["cancel_request", "request"])

    def test_who_may_cancel(self):
        admission, _, _ = self._requested()
        refused = [self.other_doctor_user, self.nurse, self.pharmacist, self.lab_tech, self.accountant, self.dpo]
        refused += [user for user in (self.cashier, self.front_desk_nurse) if user]
        for user in refused:
            with self.subTest(user=user.login):
                self.assertEqual(self._code(self._cancel, admission, user=user), "admission_not_authorized")
        self.env.invalidate_all()
        self.assertEqual(admission.state, "draft")
        for user in (self.receptionist, self.manager, self.admin):
            with self.subTest(user=user.login):
                other, _, _ = self._requested()
                self._cancel(other, user=user)
                self.assertEqual(other.state, "cancelled")

    def test_only_a_draft_request_may_be_cancelled(self):
        admission, _, _ = self._inpatient(self.bed_a)
        self.assertEqual(self._code(self._cancel, admission), "admission_invalid_state")
        self._assert_unmoved(admission, self.bed_a, 2)

    def test_replay_and_stale_revision(self):
        admission, _, _ = self._requested()
        self.assertEqual(self._code(self._cancel, admission, revision=0), "admission_revision_conflict")
        op = token()
        self._cancel(admission, op=op, revision=1)
        _, replayed = self._cancel(admission, op=op, revision=1)
        self.assertTrue(replayed)
        self.assertEqual(admission.workflow_revision, 2)
        self.assertEqual(self._code(self._cancel, admission, revision=2), "admission_invalid_state")

    def test_a_draft_found_holding_a_bed_is_not_cancelled_quietly(self):
        admission, _, _ = self._requested()
        self._raw(
            "UPDATE hospital_bed SET current_admission_id = %s, state = 'occupied' WHERE id = %s",
            (admission.id, self.bed_b.id),
        )
        self._raw("UPDATE hospital_admission SET bed_id = %s, room_id = %s, ward_id = %s WHERE id = %s",
                  (self.bed_b.id, self.room.id, self.ward.id, admission.id))
        self.assertEqual(self._code(self._cancel, admission), "admission_integrity_error")
        self.env.invalidate_all()
        self.assertEqual(admission.state, "draft")

    def test_a_cancelled_request_lets_the_doctor_request_again(self):
        admission, appointment, _ = self._requested()
        self._cancel(admission, user=self.doctor_user)
        again, _ = self._request(appointment)
        self.assertNotEqual(again, admission)
        self.assertEqual(again.sudo().state, "draft")

    # ------------------------------------------------------------------
    # Deferred consultation completion
    # ------------------------------------------------------------------
    def test_cancelling_releases_the_completion_the_request_deferred(self):
        admission, appointment, encounter = self._requested()
        appointment.with_user(self.doctor_user).action_done()
        self.env.invalidate_all()
        self.assertEqual(appointment.state, "done")
        self.assertEqual(encounter.state, "active", "Slice 2 deferred completion")

        self._cancel(admission, user=self.doctor_user)
        self.env.invalidate_all()
        self.assertEqual(encounter.state, "completed")

    def test_a_consultation_still_in_progress_is_not_completed(self):
        admission, appointment, encounter = self._requested()
        self.assertNotEqual(appointment.state, "done")
        self._cancel(admission)
        self.env.invalidate_all()
        self.assertEqual(encounter.state, "active")

    def test_another_open_admission_keeps_the_visit_open(self):
        admission, appointment, encounter = self._requested()
        appointment.with_user(self.doctor_user).action_done()
        # A second draft on the same visit, from the back office.
        self.env["hospital.admission"].sudo().create({
            "patient_id": admission.patient_id.id,
            "encounter_id": encounter.id,
            "company_id": self.company.id,
        })
        self._cancel(admission)
        self.env.invalidate_all()
        self.assertEqual(encounter.state, "active")

    def test_a_visit_that_refuses_to_complete_does_not_undo_the_cancellation(self):
        admission, appointment, encounter = self._requested()
        appointment.with_user(self.doctor_user).action_done()
        Encounter = type(self.env["hospital.encounter"])
        with patch.object(Encounter, "action_complete", side_effect=UserError("another blocker")):
            self._cancel(admission)
        self.env.invalidate_all()
        self.assertEqual(admission.state, "cancelled")
        self.assertEqual(encounter.state, "active")

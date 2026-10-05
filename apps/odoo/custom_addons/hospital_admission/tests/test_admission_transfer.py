"""Transfer foundation (PART 12), discharge structure (PART 13), audit (PART 20),
billing compatibility (PART 18) and the deferred rate bug (PART 19).

THE TRANSITION MOVED. The whole transfer used to live in
hospital.admission.transfer.wizard.action_do_transfer: a TransientModel,
reachable only from the Odoo backend form, holding occupancy logic no other
caller could reuse without copying it. It now lives on hospital.admission as
action_transfer(); the wizard collects input and calls it, deciding nothing.

No API route is registered for any of this. That is Slice 3.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    AdmissionWorkflowError,
)

from .common import AdmissionCase


@tagged("post_install", "-at_install", "admission_transfer")
class TestAdmissionTransfer(AdmissionCase):
    # ==================================================================
    # PART 12: transfer foundation
    # ==================================================================
    def test_a_transfer_moves_occupancy_atomically(self):
        admission = self._admitted(bed=self.bed_a)
        admission.action_transfer(
            to_ward=self.ward, to_room=self.room, to_bed=self.bed_b, reason="closer obs"
        )

        self.env.invalidate_all()
        self.assertEqual(admission.state, "transferred")
        self.assertEqual(admission.bed_id, self.bed_b)
        self.assertEqual(self.bed_a.state, "available")
        self.assertFalse(self.bed_a.current_admission_id)
        self.assertEqual(self.bed_b.state, "occupied")
        self.assertEqual(self.bed_b.current_admission_id, admission)

    def test_a_transfer_writes_its_history(self):
        admission = self._admitted(bed=self.bed_a)
        admission.action_transfer(
            to_ward=self.ward_other,
            to_room=self.room_other,
            to_bed=self.bed_other,
            reason="needs isolation",
        )
        transfer = admission.transfer_ids
        self.assertEqual(len(transfer), 1)
        self.assertEqual(transfer.from_bed_id, self.bed_a)
        self.assertEqual(transfer.to_bed_id, self.bed_other)
        self.assertEqual(transfer.from_ward_id, self.ward)
        self.assertEqual(transfer.to_ward_id, self.ward_other)
        self.assertEqual(transfer.reason, "needs isolation")

    def test_transfer_from_a_non_active_admission_is_refused(self):
        admission = self._draft()
        with self.assertRaises(UserError):
            admission.action_transfer(
                to_ward=self.ward, to_room=self.room, to_bed=self.bed_b
            )

    def test_an_incoherent_destination_is_refused(self):
        admission = self._admitted(bed=self.bed_a)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.action_transfer(
                to_ward=self.ward,          # ward A
                to_room=self.room_other,    # room in ward B
                to_bed=self.bed_other,
            )
        self.assertEqual(caught.exception.code, "admission_location_incoherent")
        self.env.invalidate_all()
        self.assertEqual(admission.bed_id, self.bed_a)
        self.assertEqual(self.bed_a.state, "occupied")

    def test_a_transfer_into_an_occupied_bed_is_refused(self):
        first = self._admitted(bed=self.bed_a)
        second = self._admitted(bed=self.bed_b)

        with self.assertRaises(AdmissionWorkflowError) as caught:
            second.action_transfer(
                to_ward=self.ward, to_room=self.room, to_bed=self.bed_a
            )
        self.assertIn(
            caught.exception.code,
            ("admission_bed_not_available", "admission_bed_owned_by_other"),
        )
        self.env.invalidate_all()
        # NEITHER patient moved.
        self.assertEqual(first.bed_id, self.bed_a)
        self.assertEqual(second.bed_id, self.bed_b)
        self.assertEqual(self.bed_b.state, "occupied")

    def test_a_transfer_to_the_same_bed_is_refused(self):
        admission = self._admitted(bed=self.bed_a)
        with self.assertRaises(AdmissionWorkflowError):
            admission.action_transfer(
                to_ward=self.ward, to_room=self.room, to_bed=self.bed_a
            )
        self.assertFalse(admission.transfer_ids)

    def test_the_wizard_delegates_to_the_model(self):
        """The wizard decides nothing. It is input collection."""
        admission = self._admitted(bed=self.bed_a)
        wizard = self.env["hospital.admission.transfer.wizard"].sudo().create(
            {
                "admission_id": admission.id,
                "current_ward_id": self.ward.id,
                "current_room_id": self.room.id,
                "current_bed_id": self.bed_a.id,
                "to_ward_id": self.ward.id,
                "to_room_id": self.room.id,
                "to_bed_id": self.bed_b.id,
                "reason": "via wizard",
            }
        )
        wizard.action_do_transfer()
        self.env.invalidate_all()
        self.assertEqual(admission.bed_id, self.bed_b)
        self.assertEqual(admission.transfer_ids.reason, "via wizard")

    def test_a_transferred_admission_may_transfer_again(self):
        admission = self._admitted(bed=self.bed_a)
        admission.action_transfer(
            to_ward=self.ward, to_room=self.room, to_bed=self.bed_b
        )
        admission.action_transfer(
            to_ward=self.ward_other, to_room=self.room_other, to_bed=self.bed_other
        )
        self.assertEqual(len(admission.transfer_ids), 2)
        self.assertEqual(admission.bed_id, self.bed_other)

    # ==================================================================
    # PART 20: audit and provenance
    # ==================================================================
    def test_transferred_by_is_server_derived_not_caller_supplied(self):
        """The field used to carry a default the caller could simply override.

        Slice 3 closes the channel entirely: a transfer row is created ONLY by
        action_transfer(), which stamps the actor from the session. A direct
        create -- even under sudo(), even naming a forged actor -- is refused,
        because every row starts a billed stay segment.
        """
        admission = self._admitted(bed=self.bed_a)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            self.env["hospital.admission.transfer"].sudo().create(
                {
                    "admission_id": admission.id,
                    "to_ward_id": self.ward.id,
                    "to_room_id": self.room.id,
                    "to_bed_id": self.bed_b.id,
                    "transferred_by": self.other_doctor_user.id,   # the forgery
                }
            )
        self.assertEqual(caught.exception.code, "admission_transfer_history_refused")

        admission.with_user(self.receptionist).action_transfer(
            to_ward=self.ward, to_room=self.room, to_bed=self.bed_b, reason="moved"
        )
        self.assertEqual(
            admission.transfer_ids.transferred_by,
            self.receptionist,
            "Provenance must come from the session, not the payload",
        )

    def test_transfer_history_cannot_be_reattributed_afterwards(self):
        admission = self._admitted(bed=self.bed_a)
        admission.action_transfer(
            to_ward=self.ward, to_room=self.room, to_bed=self.bed_b
        )
        transfer = admission.transfer_ids
        with self.assertRaises(AdmissionWorkflowError):
            transfer.sudo().write({"transferred_by": self.other_doctor_user.id})

    def test_the_workflow_writes_an_audit_trail(self):
        admission = self._admitted(bed=self.bed_a)
        admission.action_transfer(
            to_ward=self.ward, to_room=self.room, to_bed=self.bed_b
        )
        logs = self.env["hospital.audit.log"].sudo().search(
            [("model_name", "in", ("hospital.admission", "hospital.bed"))]
        )
        descriptions = " | ".join(logs.mapped("description") or [])
        self.assertIn("occupied", descriptions)
        self.assertIn("freed", descriptions)
        self.assertIn("transferred", descriptions)

    def test_the_audit_actor_is_the_acting_user(self):
        admission = self._draft()
        admission.with_user(self.receptionist).action_confirm_admission()
        log = self.env["hospital.audit.log"].sudo().search(
            [
                ("model_name", "=", "hospital.admission"),
                ("record_id", "=", admission.id),
                ("action_type", "=", "state_change"),
            ],
            limit=1,
        )
        self.assertTrue(log)
        self.assertEqual(log.user_id, self.receptionist)


@tagged("post_install", "-at_install", "admission_transfer")
class TestDischargeStructure(AdmissionCase):
    # ==================================================================
    # PART 13: structural discharge safety only
    # ==================================================================
    def test_discharge_stamps_its_own_timestamp(self):
        admission = self._admitted()
        self.assertFalse(admission.discharge_date)
        admission.action_discharge()
        self.assertTrue(admission.discharge_date)

    def test_discharge_from_a_draft_is_refused(self):
        admission = self._draft()
        with self.assertRaises(UserError):
            admission.action_discharge()

    def test_discharge_twice_is_refused(self):
        admission = self._admitted()
        admission.action_discharge()
        with self.assertRaises(UserError):
            admission.action_discharge()

    def test_business_discharge_policy_is_deliberately_absent(self):
        """PART 13 says structure only. This pins that decision.

        Discharge here does NOT consult billing clearance, a doctor sign-off or
        pending lab and imaging orders. That is Slice 4's work, and it becomes
        expressible only because this slice attaches the encounter. If a later
        slice adds those gates, this test is the one that should change, on
        purpose, rather than the omission being discovered in production.
        """
        admission = self._admitted()
        self.assertFalse(
            hasattr(admission, "action_request_discharge"),
            "discharge_pending is Slice 4",
        )
        admission.action_discharge()
        self.assertEqual(admission.state, "discharged")


@tagged("post_install", "-at_install", "admission_transfer")
class TestBillingCompatibility(AdmissionCase):
    # ==================================================================
    # PART 18: adding the bridge must not break legacy billing
    # ==================================================================
    def _visitless_discharged(self):
        """Slice 4: the legacy bill is for stays with NO visit (history)."""
        admission = self._admitted()
        admission.action_discharge()
        self._raw("UPDATE hospital_admission SET encounter_id = NULL WHERE id = %s", (admission.id,))
        return admission

    def test_bill_generation_still_works_after_the_bridge(self):
        admission = self._visitless_discharged()
        admission.action_generate_admission_bill()
        self.assertTrue(admission.bill_id)
        self.assertEqual(admission.billing_state, "billed")
        self.assertEqual(len(admission.bill_id.line_ids), 2)

    def test_a_second_bill_is_refused(self):
        admission = self._visitless_discharged()
        admission.action_generate_admission_bill()
        with self.assertRaises(UserError):
            admission.action_generate_admission_bill()

    def test_billing_before_discharge_is_refused(self):
        admission = self._admitted()
        with self.assertRaises(UserError):
            admission.action_generate_admission_bill()

    # ==================================================================
    # PART 19 (Slice 0) -> FIXED in Admissions Slice 3
    # ==================================================================
    def test_rate_segmentation_is_fixed(self):
        """Slice 0 pinned the retroactive rate defect here, and said this test
        should be REWRITTEN, not repaired, once the fix landed. It has.

        After a transfer the stay is no longer priced at the current rate
        throughout: each 24-hour period is billed at the location the patient
        was in when that period started, from the rate recorded as that
        segment opened. The current segment's rate is still what
        daily_rate_amount reports -- it is the rate being accrued NOW -- but it
        no longer multiplies the stay before it.
        """
        cheap_ward = self._make_ward("Slice0 Cheap", self.department)
        cheap_ward.sudo().write({"daily_ward_rate": 100.0, "admission_fee": 0.0})
        cheap_room = self._make_room(cheap_ward, "CHEAP-1")
        cheap_bed = self._make_bed(cheap_room, "CHEAP-1-1")

        dear_ward = self._make_ward("Slice0 Dear", self.department)
        dear_ward.sudo().write({"daily_ward_rate": 900.0, "admission_fee": 0.0})
        dear_room = self._make_room(dear_ward, "DEAR-1")
        dear_bed = self._make_bed(dear_room, "DEAR-1-1")

        admission = self._admitted(bed=cheap_bed)
        self.assertEqual(admission.daily_rate_amount, 100.0)
        # Admitted ten and a half days ago...
        self._raw(
            "UPDATE hospital_admission SET admission_date = admission_date - interval '10 days 12 hours' "
            "WHERE id = %s",
            (admission.id,),
        )
        admission.action_transfer(
            to_ward=dear_ward, to_room=dear_room, to_bed=dear_bed
        )
        # ...and moved exactly nine days in.
        self._raw(
            "UPDATE hospital_admission_transfer t SET transfer_date = a.admission_date + interval '9 days' "
            "FROM hospital_admission a WHERE t.admission_id = a.id AND a.id = %s",
            (admission.id,),
        )
        admission.invalidate_recordset()

        self.assertEqual(admission.daily_rate_amount, 900.0, "the rate accruing now")
        # Eleven started periods: nine began on the cheap ward, two on the
        # dear one -- not eleven dear days.
        self.assertEqual(admission.stay_days, 11)
        self.assertEqual(admission.bed_charge_amount, 9 * 100.0 + 2 * 900.0)

        # THE INPUT TO THE FIX, guaranteed. Every move is recorded, in order,
        # with both ends and a server-derived actor.
        transfers = admission.transfer_ids
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers.from_ward_id, cheap_ward)
        self.assertEqual(transfers.to_ward_id, dear_ward)
        self.assertTrue(transfers.transfer_date)
        self.assertEqual(transfers.transferred_by.id, self.env.uid)

    def test_the_audit_actor_cannot_be_forged_through_the_context(self):
        """PART 20. hospital.audit.log.create_log() reads its actor as

            self.env.context.get("audit_user_id") or self.env.user.id

        and env.context is attacker-controlled on every RPC call. Without the
        _audit() helper that strips it, any caller could sign their own
        admission, transfer or discharge with somebody else's name -- on
        exactly the records whose provenance matters most.
        """
        admission = self._draft()
        admission.with_user(self.receptionist).with_context(
            audit_user_id=self.other_doctor_user.id
        ).action_confirm_admission()

        log = self.env["hospital.audit.log"].sudo().search(
            [
                ("model_name", "=", "hospital.admission"),
                ("record_id", "=", admission.id),
                ("action_type", "=", "state_change"),
            ],
            limit=1,
        )
        self.assertTrue(log)
        self.assertEqual(
            log.user_id,
            self.receptionist,
            "The audit actor must be the acting session, not the payload",
        )

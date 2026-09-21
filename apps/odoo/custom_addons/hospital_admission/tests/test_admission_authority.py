"""Groups A, B, C, F: state authority, location coherence, bed authority, ownership.

WHAT THESE PROTECT. Before Slice 0 an admission was an ordinary record with
ordinary writable fields. write({"state": "discharged"}) moved a patient out of
the hospital without releasing their bed; a bed's occupancy could be rewritten
by anyone holding write on hospital.bed; and discharging one admission freed
whatever bed it named, whether or not it held it.

Every refusal below is asserted against a DIRECT ORM WRITE, under sudo(), and
with a forged context. sudo() is included deliberately: the guards ignore
env.su on purpose, because "may this code bypass ACLs" is a different question
from "is this the workflow method that owns this fact".
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    AdmissionWorkflowError,
)

from .common import AdmissionCase


@tagged("post_install", "-at_install", "admission_authority")
class TestAdmissionStateAuthority(AdmissionCase):
    # ==================================================================
    # A. STATE AUTHORITY
    # ==================================================================
    def test_direct_state_write_is_refused(self):
        admission = self._admitted()
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.write({"state": "discharged"})
        self.assertEqual(caught.exception.code, "admission_state_write_refused")
        admission.invalidate_recordset()
        self.assertEqual(admission.state, "admitted")

    def test_direct_state_write_under_sudo_is_refused(self):
        """sudo() IS NOT A CAPABILITY. This is the assertion that says so."""
        admission = self._admitted()
        with self.assertRaises(AdmissionWorkflowError):
            admission.sudo().write({"state": "discharged"})
        admission.invalidate_recordset()
        self.assertEqual(admission.state, "admitted")

    def test_forged_context_flag_does_not_open_the_state(self):
        """A ContextVar is server memory. No RPC payload can raise it."""
        admission = self._admitted()
        for forged in (
            {"admission_workflow_capability": True},
            {"has_admission_workflow_capability": True},
            {"allow_state_write": True},
            {"bypass_admission_authority": True},
        ):
            with self.assertRaises(AdmissionWorkflowError):
                admission.with_context(**forged).sudo().write({"state": "discharged"})
        admission.invalidate_recordset()
        self.assertEqual(admission.state, "admitted")

    def test_creating_an_admission_already_admitted_is_refused(self):
        patient = self._patient()
        self._encounter(patient)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            self.env["hospital.admission"].sudo().create(
                {
                    "patient_id": patient.id,
                    "state": "admitted",
                    "ward_id": self.ward.id,
                    "room_id": self.room.id,
                    "bed_id": self.bed_a.id,
                }
            )
        self.assertEqual(caught.exception.code, "admission_state_write_refused")

    def test_writing_the_same_state_is_not_a_change(self):
        """A form posts back every field it loaded. That must still save."""
        admission = self._admitted()
        admission.write({"state": "admitted", "notes": "ward round done"})
        self.assertEqual(admission.notes, "ward round done")

    def test_workflow_transitions_still_work(self):
        admission = self._admitted()
        admission.action_discharge()
        self.assertEqual(admission.state, "discharged")
        self.assertTrue(admission.discharge_date)

    def test_direct_discharge_date_write_is_refused(self):
        """Backdating a stay rewrites its billable length."""
        admission = self._admitted()
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.sudo().write({"discharge_date": "2020-01-01 00:00:00"})
        self.assertEqual(caught.exception.code, "admission_timeline_write_refused")

    def test_identity_fields_are_frozen_on_an_active_admission(self):
        admission = self._admitted()
        other_patient = self._patient()
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.sudo().write({"patient_id": other_patient.id})
        self.assertEqual(caught.exception.code, "admission_identity_write_refused")

    def test_attribution_fields_are_frozen_on_an_active_admission(self):
        admission = self._admitted()
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.sudo().write({"physician_id": self.other_doctor.id})
        self.assertEqual(caught.exception.code, "admission_attribution_write_refused")

    def test_a_draft_is_still_freely_editable(self):
        """Tier one of the guard. A draft is a work in progress."""
        admission = self._draft()
        admission.write(
            {
                "physician_id": self.other_doctor.id,
                "ward_id": self.ward_other.id,
                "room_id": self.room_other.id,
                "bed_id": self.bed_other.id,
            }
        )
        self.assertEqual(admission.bed_id, self.bed_other)

    def test_bill_link_cannot_be_written_directly(self):
        admission = self._admitted()
        admission.action_discharge()
        bill = self.env["hospital.patient.bill"].sudo().create(
            {"patient_id": admission.patient_id.id}
        )
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.sudo().write({"bill_id": bill.id})
        self.assertEqual(caught.exception.code, "admission_bill_write_refused")


@tagged("post_install", "-at_install", "admission_authority")
class TestAdmissionLocationCoherence(AdmissionCase):
    # ==================================================================
    # B. LOCATION COHERENCE
    # ==================================================================
    def test_ward_room_mismatch_is_refused(self):
        patient = self._patient()
        self._encounter(patient)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            self.env["hospital.admission"].sudo().create(
                {
                    "patient_id": patient.id,
                    "ward_id": self.ward.id,
                    "room_id": self.room_other.id,
                    "bed_id": self.bed_other.id,
                }
            )
        self.assertEqual(caught.exception.code, "admission_location_incoherent")

    def test_room_bed_mismatch_is_refused(self):
        patient = self._patient()
        self._encounter(patient)
        with self.assertRaises(AdmissionWorkflowError):
            self.env["hospital.admission"].sudo().create(
                {
                    "patient_id": patient.id,
                    "ward_id": self.ward.id,
                    "room_id": self.room.id,
                    "bed_id": self.bed_other.id,
                }
            )

    def test_bed_from_another_ward_is_refused(self):
        patient = self._patient()
        self._encounter(patient)
        with self.assertRaises(AdmissionWorkflowError):
            self.env["hospital.admission"].sudo().create(
                {
                    "patient_id": patient.id,
                    "ward_id": self.ward_other.id,
                    "room_id": self.room.id,
                    "bed_id": self.bed_a.id,
                }
            )

    def test_a_bed_with_no_ward_or_room_named_is_refused(self):
        """An admission that names a bed and nothing else bills at no rate."""
        patient = self._patient()
        self._encounter(patient)
        with self.assertRaises(AdmissionWorkflowError):
            self.env["hospital.admission"].sudo().create(
                {"patient_id": patient.id, "bed_id": self.bed_a.id}
            )

    def test_valid_hierarchy_is_accepted(self):
        admission = self._draft()
        self.assertEqual(admission.bed_id.room_id, admission.room_id)
        self.assertEqual(admission.room_id.ward_id, admission.ward_id)
        admission.action_confirm_admission()
        self.assertEqual(admission.state, "admitted")

    def test_location_on_an_active_admission_cannot_be_rewritten(self):
        """Relinking a bed under a patient with no occupancy bookkeeping."""
        admission = self._admitted()
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.sudo().write(
                {
                    "ward_id": self.ward_other.id,
                    "room_id": self.room_other.id,
                    "bed_id": self.bed_other.id,
                }
            )
        self.assertEqual(caught.exception.code, "admission_location_write_refused")
        admission.invalidate_recordset()
        self.assertEqual(admission.bed_id, self.bed_a)


@tagged("post_install", "-at_install", "admission_authority")
class TestBedOccupancyAuthority(AdmissionCase):
    # ==================================================================
    # C. BED AUTHORITY
    # ==================================================================
    def test_direct_bed_state_write_is_refused(self):
        with self.assertRaises(AdmissionWorkflowError) as caught:
            self.bed_a.sudo().write({"state": "occupied"})
        self.assertEqual(caught.exception.code, "admission_occupancy_write_refused")

    def test_direct_current_admission_write_is_refused(self):
        admission = self._draft()
        with self.assertRaises(AdmissionWorkflowError):
            self.bed_a.sudo().write({"current_admission_id": admission.id})

    def test_freeing_an_occupied_bed_directly_is_refused(self):
        """The write that used to strand a patient in an 'available' bed."""
        admission = self._admitted()
        with self.assertRaises(AdmissionWorkflowError):
            admission.bed_id.sudo().write(
                {"state": "available", "current_admission_id": False}
            )
        admission.bed_id.invalidate_recordset()
        self.assertEqual(admission.bed_id.state, "occupied")

    def test_creating_a_bed_already_occupied_is_refused(self):
        with self.assertRaises(AdmissionWorkflowError):
            self.env["hospital.bed"].sudo().create(
                {"name": "Forged", "room_id": self.room.id, "state": "occupied"}
            )

    def test_writing_the_same_occupancy_is_not_a_change(self):
        self.bed_a.sudo().write({"state": "available", "name": self.bed_a.name})
        self.assertEqual(self.bed_a.state, "available")

    def test_occupancy_moves_through_the_workflow(self):
        admission = self._admitted()
        self.assertEqual(admission.bed_id.state, "occupied")
        self.assertEqual(admission.bed_id.current_admission_id, admission)
        admission.action_discharge()
        self.assertEqual(admission.bed_id.state, "available")
        self.assertFalse(admission.bed_id.current_admission_id)

    def test_an_unavailable_bed_cannot_be_taken(self):
        admission = self._draft()
        # Maintenance is set through the capability, the way an estates module
        # would; the point is that confirm then refuses it.
        self.bed_a._set_occupancy("maintenance", admission=None)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_bed_not_available")


@tagged("post_install", "-at_install", "admission_authority")
class TestBedReleaseOwnership(AdmissionCase):
    # ==================================================================
    # F. BED RELEASE OWNERSHIP
    # ==================================================================
    def _stranded_pair(self):
        """Admission A names bed_a, but bed_a's pointer says admission B holds it.

        THE SHAPE A PRE-SLICE-0 DOUBLE BOOKING LEAVES BEHIND, and the only one
        still reachable: the partial unique index now forbids two ACTIVE
        admissions naming one bed, so the corruption that survives is a stale
        POINTER rather than a duplicate row. Built with raw SQL because no
        current code path produces it, which is exactly the point -- the guard
        has to cope with damage it did not cause.
        """
        admission_a = self._admitted(bed=self.bed_a)
        admission_b = self._admitted(bed=self.bed_b)
        self._raw(
            "UPDATE hospital_bed SET current_admission_id = %s WHERE id = %s",
            (admission_b.id, self.bed_a.id),
        )
        return admission_a, admission_b

    def test_discharge_cannot_free_another_admissions_bed(self):
        """THE BUG. Discharging A used to free whatever bed A named."""
        admission_a, admission_b = self._stranded_pair()
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission_a.action_discharge()
        self.assertEqual(caught.exception.code, "admission_bed_not_owned")
        self.env.invalidate_all()
        self.assertEqual(self.bed_a.state, "occupied")
        self.assertEqual(self.bed_a.current_admission_id, admission_b)

    def test_transfer_cannot_free_another_admissions_bed(self):
        admission_a, admission_b = self._stranded_pair()
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission_a.action_transfer(
                to_ward=self.ward_other,
                to_room=self.room_other,
                to_bed=self.bed_other,
                reason="test",
            )
        self.assertEqual(caught.exception.code, "admission_bed_not_owned")
        self.env.invalidate_all()
        self.assertEqual(self.bed_a.current_admission_id, admission_b)
        self.assertEqual(self.bed_other.state, "available")

    def test_cancel_is_safe_when_the_bed_is_not_ours(self):
        """action_cancel already had the ownership check. It keeps it."""
        admission_a, admission_b = self._stranded_pair()
        admission_a.action_cancel()
        self.assertEqual(admission_a.state, "cancelled")
        self.env.invalidate_all()
        self.assertEqual(self.bed_a.state, "occupied")
        self.assertEqual(self.bed_a.current_admission_id, admission_b)

    def test_cancelling_a_draft_that_names_a_bed_is_not_an_error(self):
        admission = self._draft()
        admission.action_cancel()
        self.assertEqual(admission.state, "cancelled")
        self.assertEqual(self.bed_a.state, "available")

    def test_discharge_releases_a_bed_we_do_own(self):
        admission = self._admitted()
        admission.action_discharge()
        self.assertEqual(self.bed_a.state, "available")
        self.assertFalse(self.bed_a.current_admission_id)

    def test_reset_to_draft_does_not_silently_reclaim_a_bed(self):
        admission = self._admitted()
        admission.action_cancel()
        self.assertEqual(self.bed_a.state, "available")
        admission.action_reset_to_draft()
        self.assertEqual(admission.state, "draft")
        self.bed_a.invalidate_recordset()
        self.assertEqual(self.bed_a.state, "available")
        self.assertFalse(self.bed_a.current_admission_id)

    def test_reset_to_draft_only_from_cancelled(self):
        admission = self._admitted()
        with self.assertRaises(UserError):
            admission.action_reset_to_draft()

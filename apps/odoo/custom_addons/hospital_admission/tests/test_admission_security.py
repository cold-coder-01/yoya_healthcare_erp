"""Group H: the role matrix, and Group I: hospital_insurance compatibility.

WHAT CHANGED. Before Slice 0 there were ZERO record rules on hospital.admission
and hospital.admission.transfer, so every group holding a read ACL saw every
inpatient in the building: a pharmacist and a lab technician could enumerate
the entire census, and a doctor could open, write and discharge a patient they
had never met, on any ward.

TWO LAYERS, AND THE DIFFERENCE MATTERS. Record rules OR together, so a group
rule can only ever WIDEN access -- it can never deny anything to a user who
also holds a more permissive group. Genuine denial therefore lives in the ACL
layer. That is why pharmacist, lab technician and DPO are removed from
ir.model.access.csv rather than given a narrow rule, and why the tests for them
assert AccessError on a plain search rather than an empty result.
"""
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import AdmissionCase


@tagged("post_install", "-at_install", "admission_security")
class TestAdmissionRoleMatrix(AdmissionCase):
    # ==================================================================
    # H. SECURITY -- denial at the ACL layer
    # ==================================================================
    @mute_logger("odoo.addons.base.models.ir_model")
    def test_a_pharmacist_cannot_enumerate_the_inpatient_census(self):
        self._admitted()
        with self.assertRaises(AccessError):
            self.env["hospital.admission"].with_user(self.pharmacist).search([])

    @mute_logger("odoo.addons.base.models.ir_model")
    def test_a_lab_technician_cannot_enumerate_the_inpatient_census(self):
        self._admitted()
        with self.assertRaises(AccessError):
            self.env["hospital.admission"].with_user(self.lab_tech).search([])

    @mute_logger("odoo.addons.base.models.ir_model")
    def test_neither_may_read_transfer_history(self):
        self._admitted()
        for user in (self.pharmacist, self.lab_tech):
            with self.assertRaises(AccessError):
                self.env["hospital.admission.transfer"].with_user(user).search([])

    # ==================================================================
    # H. SECURITY -- scope at the record-rule layer
    # ==================================================================
    def test_a_doctor_sees_only_their_own_patients(self):
        mine = self._admitted()                     # physician_id = self.doctor
        theirs = self._draft(bed=self.bed_b, physician_id=self.other_doctor.id)
        theirs.action_confirm_admission()

        visible = self.env["hospital.admission"].with_user(self.doctor_user).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(
            theirs, visible, "A doctor must not see another doctor's inpatient"
        )

    @mute_logger("odoo.addons.base.models.ir_rule", "odoo.models")
    def test_a_doctor_cannot_discharge_a_patient_who_is_not_theirs(self):
        """The rule that ends hospital-wide arbitrary discharge."""
        theirs = self._draft(bed=self.bed_b, physician_id=self.other_doctor.id)
        theirs.action_confirm_admission()

        with self.assertRaises(AccessError):
            theirs.with_user(self.doctor_user).action_discharge()

    def test_a_doctor_reaches_their_patient_through_the_visit_too(self):
        """Three routes to a care relationship; the visit is one of them."""
        patient = self._patient()
        encounter = self._encounter(patient)
        encounter.sudo().write({"primary_doctor_id": self.other_doctor.id})
        admission = self._draft(
            patient=patient, encounter=False, physician_id=False
        )
        admission.action_confirm_admission()

        visible = (
            self.env["hospital.admission"]
            .with_user(self.other_doctor_user)
            .search([("id", "=", admission.id)])
        )
        self.assertEqual(visible, admission)

    def test_a_nurse_sees_only_their_own_wards(self):
        mine = self._admitted(bed=self.bed_a)            # ward -> department
        theirs = self._admitted(bed=self.bed_other)      # ward_other -> other_department

        visible = self.env["hospital.admission"].with_user(self.nurse).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible, "A nurse must not see another ward")

        other_visible = (
            self.env["hospital.admission"].with_user(self.other_nurse).search([])
        )
        self.assertIn(theirs, other_visible)
        self.assertNotIn(mine, other_visible)

    def test_an_unrostered_nurse_sees_nothing(self):
        """An unrostered nurse is not a nurse of every ward in the hospital."""
        self._admitted()
        stranger = self._make_user("adm_nurse_unrostered", [
            "hospital_management.group_hospital_nurse"
        ])
        visible = self.env["hospital.admission"].with_user(stranger).search([])
        self.assertFalse(visible)

    def test_the_admissions_desk_sees_every_ward(self):
        """Scoping the desk by ward would break placing a patient."""
        mine = self._admitted(bed=self.bed_a)
        theirs = self._admitted(bed=self.bed_other)
        visible = (
            self.env["hospital.admission"].with_user(self.receptionist).search([])
        )
        self.assertIn(mine, visible)
        self.assertIn(theirs, visible)

    def test_manager_and_admin_keep_full_oversight(self):
        mine = self._admitted(bed=self.bed_a)
        theirs = self._admitted(bed=self.bed_other)
        for user in (self.manager, self.admin):
            visible = self.env["hospital.admission"].with_user(user).search([])
            self.assertIn(mine, visible)
            self.assertIn(theirs, visible)

    # ==================================================================
    # PART 16: the receptionist / bed ACL contradiction
    # ==================================================================
    @mute_logger("odoo.addons.base.models.ir_model")
    def test_a_receptionist_still_cannot_edit_beds_directly(self):
        """The contradiction is NOT solved by widening their bed ACL."""
        with self.assertRaises(AccessError):
            self.bed_a.with_user(self.receptionist).write({"name": "Renamed"})

    def test_but_a_receptionist_can_admit_a_patient(self):
        """...because occupancy moves inside _set_occupancy(), not in the ACL.

        This is the test that proves PART 16 is actually fixed: the same user
        who cannot write a single field on hospital.bed can nonetheless take
        one through the admission workflow, and the bed really does change.
        """
        admission = self._draft()
        admission.with_user(self.receptionist).action_confirm_admission()

        admission.invalidate_recordset()
        self.bed_a.invalidate_recordset()
        self.assertEqual(admission.state, "admitted")
        self.assertEqual(self.bed_a.state, "occupied")
        self.assertEqual(self.bed_a.current_admission_id, admission)

    def test_and_a_receptionist_can_discharge_and_free_the_bed(self):
        admission = self._draft()
        admission.with_user(self.receptionist).action_confirm_admission()
        admission.with_user(self.receptionist).action_discharge()

        self.bed_a.invalidate_recordset()
        self.assertEqual(self.bed_a.state, "available")
        self.assertFalse(self.bed_a.current_admission_id)

    def test_the_capability_is_not_left_raised_after_a_workflow_call(self):
        """Each window is released in a `finally`, so an exception cannot leak it."""
        from odoo.addons.hospital_admission.models.admission_authority import (
            has_admission_workflow_capability,
            has_bed_occupancy_capability,
            has_admission_location_capability,
        )

        admission = self._draft()
        admission.action_confirm_admission()
        self.assertFalse(has_admission_workflow_capability())
        self.assertFalse(has_bed_occupancy_capability())
        self.assertFalse(has_admission_location_capability())

        # And after a REFUSAL, which is the path that actually risks leaking.
        contender = self._draft(bed=self.bed_a)
        with self.assertRaises(Exception):
            contender.action_confirm_admission()
        self.assertFalse(has_admission_workflow_capability())
        self.assertFalse(has_bed_occupancy_capability())


@tagged("post_install", "-at_install", "admission_security")
class TestInsuranceCompatibility(AdmissionCase):
    # ==================================================================
    # I. hospital_insurance COMPATIBILITY
    # ==================================================================
    #
    # hospital_insurance is INSTALLED in the live database and lives OUTSIDE
    # this repository, at C:\custom_addons. It adds coverage_id, guarantee_id
    # and insurance_claim_ids to hospital.admission. The guards in this slice
    # must tolerate those fields, which is why they are deliberately absent
    # from ADMISSION_IDENTITY_FIELDS: insurance cover is a payer fact that is
    # legitimately attached and re-attached during and after a stay, and
    # freezing it would break that module without improving safety.
    #
    # These tests skip themselves when the addon is not on the addons path, so
    # a clean clone of this repository still runs green.
    #
    def _skip_without_insurance(self):
        if "coverage_id" not in self.env["hospital.admission"]._fields:
            self.skipTest("hospital_insurance is not installed")

    def test_the_extension_fields_are_present_and_writable_on_a_draft(self):
        self._skip_without_insurance()
        admission = self._draft()
        admission.write({"coverage_id": False, "guarantee_id": False})
        self.assertFalse(admission.coverage_id)

    def test_cover_may_still_be_attached_to_an_active_admission(self):
        """The guards must not freeze somebody else's module's fields."""
        self._skip_without_insurance()
        admission = self._admitted()
        # Writing the current value is the minimum that must not be refused;
        # a real coverage record needs hospital_insurance fixtures this suite
        # deliberately does not reach into.
        admission.write({"coverage_id": admission.coverage_id.id or False})
        self.assertEqual(admission.state, "admitted")

    def test_creating_an_admission_with_extension_fields_works(self):
        self._skip_without_insurance()
        patient = self._patient()
        self._encounter(patient)
        admission = self.env["hospital.admission"].sudo().create(
            {
                "patient_id": patient.id,
                "ward_id": self.ward.id,
                "room_id": self.room.id,
                "bed_id": self.bed_a.id,
                "coverage_id": False,
                "guarantee_id": False,
            }
        )
        admission.action_confirm_admission()
        self.assertEqual(admission.state, "admitted")

    def test_the_claim_one2many_still_resolves(self):
        self._skip_without_insurance()
        admission = self._admitted()
        self.assertEqual(len(admission.insurance_claim_ids), 0)
        self.assertEqual(admission.claim_count, 0)

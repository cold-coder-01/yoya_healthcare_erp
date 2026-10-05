"""Group G: the encounter bridge, and Group J: legacy tolerance.

THE KEYSTONE OF SLICE 0. Before this, hospital.admission had no encounter_id at
all, so inpatient care was invisible to the billing engine, to payer
resolution, to insurance and to the single-active-episode rule. The database
held 369 encounters and not one of them was inpatient.

WHY ADOPTION AND NOT CREATION. yoya_reception_bridge enforces one active
episode per patient in hospital.encounter.create(), under an advisory lock,
because two live episodes for one person means two consultation charges and two
draws against the same benefit. An inpatient stay is not a second episode of
care -- it is what happens to the episode the patient already has. So this
module never calls hospital.encounter.create(). It adopts, and when there is
nothing to adopt it refuses rather than inventing a visit with no registration,
no payer capture and no front-desk provenance.
"""
from odoo.tests import tagged

from odoo.addons.hospital_admission.models.admission_authority import (
    AdmissionWorkflowError,
)

from .common import AdmissionCase


@tagged("post_install", "-at_install", "admission_encounter")
class TestAdmissionEncounterBridge(AdmissionCase):
    # ==================================================================
    # G. ENCOUNTER
    # ==================================================================
    def test_confirm_adopts_the_patients_open_visit(self):
        patient = self._patient()
        encounter = self._encounter(patient)
        admission = self._draft(patient=patient, encounter=False)
        self.assertFalse(admission.encounter_id)

        admission.action_confirm_admission()

        self.assertEqual(admission.encounter_id, encounter)
        self.assertEqual(admission.state, "admitted")

    def test_the_adopted_visit_is_retyped_inpatient(self):
        patient = self._patient()
        encounter = self._encounter(patient)
        self.assertEqual(encounter.encounter_type, "outpatient")

        admission = self._draft(patient=patient, encounter=False)
        admission.action_confirm_admission()

        encounter.invalidate_recordset()
        self.assertEqual(encounter.encounter_type, "inpatient")

    def test_no_second_episode_is_created(self):
        """The invariant yoya_reception_bridge exists to protect."""
        patient = self._patient()
        self._encounter(patient)
        before = self.env["hospital.encounter"].sudo().search_count(
            [("patient_id", "=", patient.id)]
        )

        admission = self._draft(patient=patient, encounter=False)
        admission.action_confirm_admission()

        after = self.env["hospital.encounter"].sudo().search_count(
            [("patient_id", "=", patient.id)]
        )
        self.assertEqual(before, after, "Admission must adopt, never create")

    def test_confirm_without_an_open_visit_is_refused(self):
        """The smallest safe behaviour, chosen deliberately over inventing one."""
        patient = self._patient()
        admission = self._draft(patient=patient, encounter=False)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_encounter_required")
        self.assertEqual(admission.state, "draft")
        self.assertEqual(self.bed_a.state, "available")

    def test_a_completed_visit_is_not_adoptable(self):
        patient = self._patient()
        encounter = self._encounter(patient)
        encounter.sudo().write({"state": "completed"})
        admission = self._draft(patient=patient, encounter=False)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_encounter_required")

    def test_a_patient_mismatch_is_refused(self):
        patient = self._patient()
        other = self._patient()
        self._encounter(patient)
        foreign = self._encounter(other)

        admission = self._draft(patient=patient, encounter=False)
        admission.sudo().write({"encounter_id": foreign.id})
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.action_confirm_admission()
        self.assertEqual(
            caught.exception.code, "admission_encounter_patient_mismatch"
        )

    def test_a_company_mismatch_is_refused(self):
        other_company = self.env["res.company"].sudo().create(
            {"name": "Slice0 Other Company"}
        )
        patient = self._patient()
        encounter = self._encounter(patient)
        admission = self._draft(patient=patient, encounter=False)
        # Reach past the guard to build the mismatch the constraint must catch.
        self.cr.execute(
            "UPDATE hospital_encounter SET company_id = %s WHERE id = %s",
            (other_company.id, encounter.id),
        )
        self.env.invalidate_all()

        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.action_confirm_admission()
        # No eligible encounter in THIS company any more.
        self.assertEqual(caught.exception.code, "admission_encounter_required")

    def test_ambiguous_open_visits_are_refused(self):
        """Two live episodes is a data problem, not a choice to make silently."""
        patient = self._patient()
        self._encounter(patient)
        # A second open episode can only exist by reaching past the reception
        # guard, which is exactly the corrupt state this refusal is for.
        self.cr.execute(
            """
            INSERT INTO hospital_encounter
                (name, patient_id, encounter_type, state, company_id,
                 payer_type, opened_at, active, create_uid, write_uid,
                 create_date, write_date)
            VALUES (%s, %s, 'outpatient', 'active', %s, 'self_pay', now(), true,
                    %s, %s, now(), now())
            """,
            (
                "ENC-DUP-%s" % patient.id,
                patient.id,
                self.company.id,
                self.env.uid,
                self.env.uid,
            ),
        )
        self.env.invalidate_all()

        admission = self._draft(patient=patient, encounter=False)
        with self.assertRaises(AdmissionWorkflowError) as caught:
            admission.action_confirm_admission()
        self.assertEqual(caught.exception.code, "admission_encounter_ambiguous")

    def test_an_active_admission_cannot_be_left_without_an_encounter(self):
        admission = self._admitted()
        with self.assertRaises(AdmissionWorkflowError):
            admission.sudo().write({"encounter_id": False})

    # ------------------------------------------------------------------
    # PART 17: encounter <-> admission coherence
    # ------------------------------------------------------------------
    def test_a_visit_with_a_patient_in_a_bed_cannot_be_closed(self):
        """hospital_billing's comment said this would be enforced here. It is."""
        admission = self._admitted()
        encounter = admission.encounter_id
        # Since Admissions Slice 2, action_complete() DEFERS on a visit with an
        # open admission, so the visit can no longer reach `completed` through
        # the workflow at all. Force it there to prove the second line of
        # defence -- the _check_can_close() hook -- still refuses.
        encounter.sudo().action_complete()
        self.assertEqual(encounter.state, "active")
        encounter.sudo().write({"state": "completed"})

        with self.assertRaises(AdmissionWorkflowError) as caught:
            encounter.sudo().action_close()
        self.assertEqual(
            caught.exception.code, "admission_encounter_has_active_admission"
        )

    def test_a_visit_with_a_patient_in_a_bed_cannot_be_cancelled(self):
        """action_close consults _check_can_close; action_cancel does not."""
        admission = self._admitted()
        encounter = admission.encounter_id
        with self.assertRaises(AdmissionWorkflowError) as caught:
            encounter.sudo().action_cancel()
        self.assertEqual(
            caught.exception.code, "admission_encounter_has_active_admission"
        )
        encounter.invalidate_recordset()
        self.assertNotEqual(encounter.state, "cancelled")

    def test_the_visit_may_be_closed_once_the_patient_is_discharged(self):
        admission = self._admitted()
        encounter = admission.encounter_id
        admission.action_discharge()

        encounter.sudo().write({"state": "active"})
        encounter.sudo().action_complete()
        encounter.sudo().action_close()
        self.assertEqual(encounter.state, "closed")

    def test_the_encounter_exposes_its_active_admission(self):
        admission = self._admitted()
        encounter = admission.encounter_id
        encounter.invalidate_recordset()
        self.assertTrue(encounter.is_inpatient_active)
        self.assertEqual(encounter.active_admission_id, admission)

        admission.action_discharge()
        encounter.invalidate_recordset()
        self.assertFalse(encounter.is_inpatient_active)


@tagged("post_install", "-at_install", "admission_encounter")
class TestLegacyAdmissionTolerance(AdmissionCase):
    # ==================================================================
    # J. LEGACY
    # ==================================================================
    def _legacy_discharged_row(self):
        """An ADM00001-equivalent: discharged, no encounter, no company logic.

        Inserted with raw SQL because that is what it is -- a row that predates
        the field, not a row any current code path can produce.
        """
        patient = self._patient("Legacy Patient")
        self.cr.execute(
            """
            INSERT INTO hospital_admission
                (name, patient_id, state, ward_id, room_id, bed_id,
                 company_id, admission_date, discharge_date, active,
                 create_uid, write_uid, create_date, write_date)
            VALUES (%s, %s, 'discharged', %s, %s, %s, %s,
                    now() - interval '10 days', now() - interval '9 days', true,
                    %s, %s, now(), now())
            RETURNING id
            """,
            (
                "ADMLEG-%s" % patient.id,
                patient.id,
                self.ward.id,
                self.room.id,
                self.bed_a.id,
                self.company.id,
                self.env.uid,
                self.env.uid,
            ),
        )
        admission_id = self.cr.fetchone()[0]
        self.env.invalidate_all()
        return self.env["hospital.admission"].sudo().browse(admission_id)

    def test_a_historical_discharged_admission_may_have_no_encounter(self):
        """Do not manufacture clinical history."""
        legacy = self._legacy_discharged_row()
        self.assertFalse(legacy.encounter_id)
        self.assertEqual(legacy.state, "discharged")

    def test_a_legacy_row_still_opens(self):
        """The form reads every field, including the computes."""
        legacy = self._legacy_discharged_row()
        self.assertTrue(legacy.display_name)
        self.assertTrue(legacy.stay_days >= 1)
        # billing_state is a STORED compute, and a raw insert never populated
        # it -- which is precisely what makes this a legacy row rather than a
        # synthetic one. Either value is correct here; what matters is that
        # reading the record does not raise.
        self.assertIn(legacy.billing_state, (False, "not_billed"))
        self.assertTrue(legacy.read())

    def test_a_legacy_row_passes_its_own_constraints(self):
        """Validating it explicitly is what an upgrade or a recompute does."""
        legacy = self._legacy_discharged_row()
        legacy._check_active_admission_has_encounter()
        legacy._check_location_coherence()
        legacy._check_location_company()

    def test_a_legacy_row_may_still_be_billed(self):
        legacy = self._legacy_discharged_row()
        legacy.action_generate_admission_bill()
        self.assertTrue(legacy.bill_id)
        self.assertEqual(legacy.billing_state, "billed")

    def test_a_legacy_row_cannot_be_reactivated_without_an_encounter(self):
        """History stays tolerated; it does not become a loophole."""
        legacy = self._legacy_discharged_row()
        with self.assertRaises(AdmissionWorkflowError):
            legacy._write_state("admitted")

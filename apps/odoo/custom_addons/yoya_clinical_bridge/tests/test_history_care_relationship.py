"""Slice 9A: longitudinal history access is bounded by an ACTIVE care relationship.

WHAT THIS FILE PINS
===================
Two separable things, and keeping them separable is the point:

  1. THE COMPUTE. res.users.yoya_care_relationship_patient_ids answers "which
     patients do I currently have an active appointment for". It is the input
     to every History record rule, so its edges ARE security edges: a state
     wrongly counted as active is a patient's whole clinical past wrongly
     disclosed.

  2. THE RULES. Fifteen read-only ir.rules that widen the doctor scope to those
     patients' prior records, and to nothing else.

WHY THE STATE VOCABULARY IS ASSERTED RATHER THAN ASSUMED.
ACTIVE_CARE_APPOINTMENT_STATES names two of the five values
hospital.appointment.state can hold. If a later slice adds a state, or renames
one, this file fails loudly instead of silently granting or silently revoking
longitudinal access. The 'done' case is tested by name because including it is
the specific mistake that would turn a bounded relationship into "every patient
I have ever treated".

WHY THE UNRESTRICTED RULES ARE TESTED TOO.
group_hospital_manager IMPLIES group_hospital_doctor, and Odoo ORs the domains
of every rule whose groups the user holds. A manager must keep their existing
ceiling rather than inheriting the doctor's care-relationship scope as a limit.

FIXTURES DRIVE STATE, NOT THE WORKFLOW, for the reason the sibling rule tests
give: these are questions about ir.rule composition, and driving the billing
apparatus to ask them would exercise machinery this file has no business
touching.
"""
import uuid

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.yoya_clinical_bridge.models.res_users import (
    ACTIVE_CARE_APPOINTMENT_STATES,
)


@tagged("post_install", "-at_install", "history_care_relationship")
class HistoryCareRelationshipCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.user_hana = cls._make_user(
            "hist_hana", "hospital_management.group_hospital_doctor"
        )
        cls.user_sara = cls._make_user(
            "hist_sara", "hospital_management.group_hospital_doctor"
        )
        cls.user_manager = cls._make_user(
            "hist_manager", "hospital_management.group_hospital_manager"
        )
        cls.user_sysadmin = cls._make_user(
            "hist_sysadmin",
            "hospital_management.group_hospital_system_administrator",
        )
        cls.hana = cls.env["hospital.doctor"].sudo().create(
            {"name": "Dr History Hana", "user_id": cls.user_hana.id}
        )
        cls.sara = cls.env["hospital.doctor"].sudo().create(
            {"name": "Dr History Sara", "user_id": cls.user_sara.id}
        )
        tag = uuid.uuid4().hex[:6]
        cls.disease = cls.env["hospital.disease"].sudo().create(
            {"name": "History Disease %s" % tag, "code": "HD%s" % tag.upper()}
        )

    @classmethod
    def _make_user(cls, login, *group_xmlids):
        groups = [cls.env.ref("base.group_user").id] + [
            cls.env.ref(x).id for x in group_xmlids
        ]
        suffix = uuid.uuid4().hex[:6]
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s_%s@example.test" % (login, suffix),
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, groups)],
        })

    # ------------------------------------------------------------------
    def _patient(self, label="History Patient"):
        return self.env["hospital.patient"].sudo().create(
            {"name": "%s %s" % (label, uuid.uuid4().hex[:8])}
        )

    def _appointment(self, patient, doctor, state="confirmed"):
        return self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": state,
        })

    def _encounter(self, patient, appointment, doctor, state="completed"):
        return self.env["hospital.encounter"].sudo().create({
            "patient_id": patient.id,
            "appointment_id": appointment.id,
            "primary_doctor_id": doctor.id,
            "encounter_type": "outpatient",
            "state": state,
        })

    def _care_patients(self, user):
        """The computed set, read as that user, exactly as an ir.rule reads it."""
        return user.with_user(user).yoya_care_relationship_patient_ids


# ======================================================================
# 1. THE COMPUTE
# ======================================================================
@tagged("post_install", "-at_install", "history_care_relationship")
class TestCareRelationshipCompute(HistoryCareRelationshipCase):

    def test_the_active_state_vocabulary_is_exactly_two_states(self):
        """Pinned by name. A state added or renamed upstream must break here
        rather than quietly change who can read a patient's history."""
        self.assertEqual(
            tuple(ACTIVE_CARE_APPOINTMENT_STATES),
            ("confirmed", "in_consultation"),
        )
        field_states = dict(
            self.env["hospital.appointment"]._fields["state"].selection
        )
        for state in ACTIVE_CARE_APPOINTMENT_STATES:
            self.assertIn(state, field_states)

    def test_confirmed_appointment_establishes_the_relationship(self):
        patient = self._patient()
        self._appointment(patient, self.hana, state="confirmed")
        self.assertIn(patient, self._care_patients(self.user_hana))

    def test_in_consultation_appointment_establishes_the_relationship(self):
        patient = self._patient()
        self._appointment(patient, self.hana, state="in_consultation")
        self.assertIn(patient, self._care_patients(self.user_hana))

    def test_a_DONE_appointment_does_NOT_establish_the_relationship(self):
        """THE exclusion the policy turns on. If 'done' counted, one completed
        visit would grant permanent longitudinal access to that patient, which
        is precisely the 'every patient ever treated' scope this refuses."""
        patient = self._patient()
        self._appointment(patient, self.hana, state="done")
        self.assertNotIn(patient, self._care_patients(self.user_hana))

    def test_a_cancelled_appointment_does_NOT_establish_the_relationship(self):
        patient = self._patient()
        self._appointment(patient, self.hana, state="cancelled")
        self.assertNotIn(patient, self._care_patients(self.user_hana))

    def test_a_draft_appointment_does_NOT_establish_the_relationship(self):
        """An unconfirmed booking may never become a visit at all."""
        patient = self._patient()
        self._appointment(patient, self.hana, state="draft")
        self.assertNotIn(patient, self._care_patients(self.user_hana))

    def test_another_doctors_active_appointment_is_not_mine(self):
        patient = self._patient()
        self._appointment(patient, self.sara, state="confirmed")
        self.assertNotIn(patient, self._care_patients(self.user_hana))

    def test_the_relationship_LAPSES_when_the_episode_closes(self):
        """The documented consequence of the Phase-1 policy, asserted."""
        patient = self._patient()
        appointment = self._appointment(patient, self.hana, state="confirmed")
        self.assertIn(patient, self._care_patients(self.user_hana))

        appointment.sudo().write({"state": "done"})
        self.user_hana.invalidate_recordset()
        self.assertNotIn(patient, self._care_patients(self.user_hana))

    def test_a_patient_with_several_active_appointments_appears_once(self):
        patient = self._patient()
        self._appointment(patient, self.hana, state="confirmed")
        self._appointment(patient, self.hana, state="in_consultation")
        care = self._care_patients(self.user_hana)
        self.assertEqual(len(care.filtered(lambda p: p == patient)), 1)

    def test_the_compute_writes_nothing(self):
        """It is a projection. No row is created and no patient is touched."""
        patient = self._patient()
        self._appointment(patient, self.hana, state="confirmed")
        before = patient.write_date
        self._care_patients(self.user_hana)
        patient.invalidate_recordset()
        self.assertEqual(patient.write_date, before)

    def test_a_manager_computes_their_OWN_patients_not_the_hospitals(self):
        """A manager's appointment rule is unrestricted, so the compute must
        get its narrowness from its own domain rather than from the rules."""
        mine = self._patient()
        theirs = self._patient()
        manager_doctor = self.env["hospital.doctor"].sudo().create(
            {"name": "Dr History Manager", "user_id": self.user_manager.id}
        )
        self._appointment(mine, manager_doctor, state="confirmed")
        self._appointment(theirs, self.sara, state="confirmed")

        care = self._care_patients(self.user_manager)
        self.assertIn(mine, care)
        self.assertNotIn(theirs, care)


# ======================================================================
# 2. CROSS-PROVIDER READ
# ======================================================================
@tagged("post_install", "-at_install", "history_care_relationship")
class TestCrossProviderHistoryRules(HistoryCareRelationshipCase):

    def _prior_episode(self, patient, doctor):
        """A settled episode conducted by `doctor`, with a full clinical record."""
        appointment = self._appointment(patient, doctor, state="done")
        encounter = self._encounter(patient, appointment, doctor, state="completed")
        consultation = self.env["hospital.consultation"].sudo().create({
            "encounter_id": encounter.id,
            "doctor_id": doctor.id,
            "assessment": "Prior assessment",
        })
        diagnosis = self.env["hospital.patient.diagnosis"].sudo().create({
            "patient_id": patient.id,
            "disease_id": self.disease.id,
            "physician_id": doctor.id,
            "consultation_id": consultation.id,
            # REQUIRED alongside consultation_id: the bridge's
            # _check_consultation_references refuses a consultation diagnosis
            # that does not carry exactly the consultation's own episode AND
            # its own visit.
            "encounter_id": encounter.id,
            "appointment_id": appointment.id,
            "diagnosis_type": "primary",
        })
        prescription = self.env["hospital.prescription"].sudo().create({
            "patient_id": patient.id,
            "physician_id": doctor.id,
            "consultation_id": consultation.id,
            # Same bridge invariant the diagnosis above satisfies: a
            # consultation-linked record must cite the consultation's own visit.
            "appointment_id": appointment.id,
        })
        return {
            "appointment": appointment,
            "encounter": encounter,
            "consultation": consultation,
            "diagnosis": diagnosis,
            "prescription": prescription,
        }

    def _with_active_care(self, patient):
        """Give Hana a live episode for this patient."""
        return self._appointment(patient, self.hana, state="in_consultation")

    # ---- the property the slice exists for -------------------------
    def test_hana_reads_saras_prior_encounter_for_a_shared_patient(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self._with_active_care(patient)

        encounter = prior["encounter"].with_user(self.user_hana)
        self.assertEqual(encounter.read(["name"])[0]["name"], prior["encounter"].name)

    def test_hana_reads_saras_prior_consultation(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self._with_active_care(patient)

        note = prior["consultation"].with_user(self.user_hana)
        self.assertEqual(note.read(["assessment"])[0]["assessment"], "Prior assessment")

    def test_hana_reads_saras_prior_diagnosis(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self._with_active_care(patient)

        diagnosis = prior["diagnosis"].with_user(self.user_hana)
        self.assertEqual(
            diagnosis.read(["diagnosis_type"])[0]["diagnosis_type"], "primary"
        )

    def test_hana_reads_saras_prior_prescription(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self._with_active_care(patient)

        prescription = prior["prescription"].with_user(self.user_hana)
        self.assertTrue(prescription.read(["name"])[0]["name"])

    # ---- and only while the relationship lasts ---------------------
    def test_WITHOUT_an_active_relationship_none_of_it_is_readable(self):
        """The defect these rules must not introduce: hospital-wide reading."""
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        # No appointment for Hana at all.
        for key in ("encounter", "consultation", "diagnosis", "prescription"):
            with self.assertRaises(AccessError):
                prior[key].with_user(self.user_hana).read(["display_name"])

    def test_a_DONE_relationship_does_not_grant_history(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self._appointment(patient, self.hana, state="done")

        with self.assertRaises(AccessError):
            prior["consultation"].with_user(self.user_hana).read(["assessment"])

    def test_access_LAPSES_when_hanas_episode_closes(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        current = self._with_active_care(patient)

        self.assertTrue(
            prior["consultation"].with_user(self.user_hana).read(["assessment"])
        )

        current.sudo().write({"state": "done"})
        self.user_hana.invalidate_recordset()
        prior["consultation"].invalidate_recordset()
        with self.assertRaises(AccessError):
            prior["consultation"].with_user(self.user_hana).read(["assessment"])

    def test_a_DIFFERENT_patients_history_stays_invisible(self):
        """Treating patient P grants P's history and nothing else."""
        treated = self._patient("Treated")
        stranger = self._patient("Stranger")
        self._with_active_care(treated)
        other = self._prior_episode(stranger, self.sara)

        with self.assertRaises(AccessError):
            other["consultation"].with_user(self.user_hana).read(["assessment"])

    def test_another_patients_encounter_is_absent_from_search(self):
        """Invisible, not merely unreadable."""
        treated = self._patient("Treated")
        stranger = self._patient("Stranger")
        self._with_active_care(treated)
        mine = self._prior_episode(treated, self.sara)
        theirs = self._prior_episode(stranger, self.sara)

        visible = self.env["hospital.encounter"].with_user(self.user_hana).search([])
        self.assertIn(mine["encounter"], visible)
        self.assertNotIn(theirs["encounter"], visible)

    # ---- READ ONLY, without exception ------------------------------
    def test_history_access_is_READ_ONLY_write_is_refused(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self._with_active_care(patient)

        with self.assertRaises(AccessError):
            prior["consultation"].with_user(self.user_hana).write(
                {"assessment": "tampered"}
            )

    def test_history_access_is_READ_ONLY_diagnosis_write_is_refused(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self._with_active_care(patient)

        with self.assertRaises(AccessError):
            prior["diagnosis"].with_user(self.user_hana).write(
                {"diagnosis_type": "secondary"}
            )

    def test_history_access_is_READ_ONLY_unlink_is_refused(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self._with_active_care(patient)

        with self.assertRaises(AccessError):
            prior["diagnosis"].with_user(self.user_hana).unlink()

    def test_history_access_does_not_permit_creating_into_anothers_episode(self):
        """A widened READ must not become a widened CREATE."""
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self._with_active_care(patient)

        with self.assertRaises(AccessError):
            self.env["hospital.patient.diagnosis"].with_user(self.user_hana).create({
                "patient_id": patient.id,
                "disease_id": self.disease.id,
                "consultation_id": prior["consultation"].id,
                # Otherwise VALID, so the refusal below can only be about
                # access. Omitting these would trip the bridge's reference
                # constraint first and the test would pass for the wrong reason.
                "encounter_id": prior["encounter"].id,
                "appointment_id": prior["appointment"].id,
                "diagnosis_type": "secondary",
            })

    def test_the_rule_domain_admits_EXACTLY_the_specified_patients(self):
        """THE EQUIVALENCE THAT KEEPS TWO STATEMENTS OF ONE POLICY HONEST.

        res.users.yoya_care_relationship_patient_ids is the readable
        specification; the ir.rule domains are a query-time traversal written
        that way so no patient id is ever baked into ir.rule's ormcache. Those
        are two expressions of the same rule, so this asserts they agree.
        """
        treated = self._patient("Treated")
        stranger = self._patient("Stranger")
        closed = self._patient("Closed")
        self._appointment(treated, self.hana, state="in_consultation")
        self._appointment(stranger, self.sara, state="confirmed")
        self._appointment(closed, self.hana, state="done")

        specified = self._care_patients(self.user_hana)
        self.assertIn(treated, specified)
        self.assertNotIn(stranger, specified)
        self.assertNotIn(closed, specified)

        for patient, reachable in (
            (treated, True), (stranger, False), (closed, False)
        ):
            prior = self._prior_episode(patient, self.sara)
            visible = (
                self.env["hospital.encounter"]
                .with_user(self.user_hana)
                .search([("id", "=", prior["encounter"].id)])
            )
            self.assertEqual(
                bool(visible),
                reachable,
                "domain and specification disagree for %s" % patient.name,
            )

    # ---- the senior roles keep their ceiling -----------------------
    def test_manager_keeps_hospital_wide_visibility(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self.assertTrue(
            prior["consultation"].with_user(self.user_manager).read(["assessment"])
        )

    def test_sysadmin_keeps_hospital_wide_visibility(self):
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        self.assertTrue(
            prior["consultation"].with_user(self.user_sysadmin).read(["assessment"])
        )

    def test_manager_write_is_not_narrowed_by_the_history_rules(self):
        """The history rules are perm_read only; they must not clip a manager's
        existing write ceiling by being OR'd alongside it."""
        patient = self._patient()
        prior = self._prior_episode(patient, self.sara)
        prior["consultation"].with_user(self.user_manager).write(
            {"assessment": "manager edit"}
        )
        self.assertEqual(prior["consultation"].assessment, "manager edit")

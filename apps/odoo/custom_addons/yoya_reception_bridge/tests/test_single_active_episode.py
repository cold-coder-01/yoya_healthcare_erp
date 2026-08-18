"""One active episode of care per patient.

THE CASE THAT PROMPTED THIS. Manual UAT registered a second visit for a patient
whose first was still awaiting the cashier. Two live episodes for one person
means two consultation charges, two cashier liabilities, two draws against the
same corporate benefit and two triage flows for one body.

THE RULE IS ABOUT STATE, NOT TIME. Repeated vitals, several lab requests and a
walk to the cashier all belong to ONE episode and stay legal. What is refused is
opening a SECOND episode while the first is unfinished. A "not within an hour"
rule would forbid the legitimate case and permit the illegitimate one an hour
later.

THE GUARD IS ON hospital.encounter.create(), not on the reception service,
because every route to a new episode passes through it: the workflow, a direct
ORM call, a second front end, a double-clicked button.
"""

import uuid

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"

# Mirrors hospital.encounter.EPISODE_CLOSED_STATES. Restated so a change there
# has to be a deliberate change here too.
CLOSED = ("completed", "closed", "cancelled")
OPEN = ("planned", "checked_in", "active")


@tagged("post_install", "-at_install", "single_active_episode")
class TestSingleActiveEpisode(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.workflow = cls.env["hospital.reception.workflow"]
        cls.front_desk = cls._make_user("episode_nurse", [G_FRONT_DESK_NURSE])
        cls.receptionist = cls._make_user("episode_reception", [G_RECEPTIONIST])
        cls.department = cls.env["hospital.department"].sudo().search([], limit=1)

    @classmethod
    def _make_user(cls, login, groups):
        return cls.env["res.users"].sudo().create(
            {
                "name": login,
                "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
                "groups_id": [
                    (6, 0, [cls.env.ref("base.group_user").id]
                     + [cls.env.ref(g).id for g in groups])
                ],
            }
        )

    def _patient(self, name="Episode Patient"):
        return self.env["hospital.patient"].sudo().create(
            {"name": "%s %s" % (name, uuid.uuid4().hex[:6])}
        )

    def _encounter(self, patient, state=None, **overrides):
        vals = {
            "patient_id": patient.id,
            "encounter_type": "outpatient",
            "company_id": self.company.id,
            "opened_at": fields.Datetime.now(),
        }
        vals.update(overrides)
        encounter = self.env["hospital.encounter"].sudo().create(vals)
        if state:
            encounter.write({"state": state})
        return encounter

    def _register(self, patient):
        """A real front-desk registration."""
        return self.workflow.with_user(self.receptionist).create_visit(
            patient=patient, visit_type="routine",
        )

    # ==================================================================
    # THE RULE
    # ==================================================================
    def test_01_a_patient_with_no_episode_may_be_registered(self):
        patient = self._patient()
        result = self._register(patient)
        self.assertTrue(result["encounter"])
        self.assertEqual(result["encounter"].patient_id, patient)

    def test_02_every_open_state_blocks_a_second_episode(self):
        for state in OPEN:
            with self.subTest(state=state):
                patient = self._patient()
                self._encounter(patient, state=state)
                with self.assertRaises(ValidationError) as caught:
                    self._encounter(patient)
                self.assertIn("already has an active visit", str(caught.exception))

    def test_03_every_closed_state_allows_a_new_episode(self):
        for state in CLOSED:
            with self.subTest(state=state):
                patient = self._patient()
                self._encounter(patient, state=state)
                second = self._encounter(patient)
                self.assertTrue(
                    second.exists(),
                    "A finished visit must not block the next attendance.",
                )

    def test_04_a_registered_visit_blocks_a_second_registration(self):
        """The reported UAT case, through the real front-desk path."""
        patient = self._patient("Beza Belete")
        self._register(patient)
        with self.assertRaises(ValidationError) as caught:
            self._register(patient)
        message = str(caught.exception)
        self.assertIn("already has an active visit", message)
        self.assertIn(
            "Complete or cancel", message,
            "The message must tell the desk what to do next.",
        )

    def test_05_the_message_names_the_existing_visit(self):
        patient = self._patient()
        first = self._register(patient)
        code = first["appointment"].appointment_code
        with self.assertRaises(ValidationError) as caught:
            self._register(patient)
        self.assertIn(
            code, str(caught.exception),
            "Staff must be able to find the visit they are being sent to.",
        )

    def test_06_a_completed_visit_allows_the_next_registration(self):
        patient = self._patient()
        first = self._register(patient)
        first["encounter"].sudo().write({"state": "completed"})
        second = self._register(patient)
        self.assertNotEqual(second["encounter"], first["encounter"])

    def test_07_a_cancelled_visit_allows_the_next_registration(self):
        patient = self._patient()
        first = self._register(patient)
        first["encounter"].sudo().write({"state": "cancelled"})
        second = self._register(patient)
        self.assertTrue(second["encounter"].exists())

    def test_08_a_different_patient_is_unaffected(self):
        blocked = self._patient()
        self._register(blocked)
        other = self._patient()
        self.assertTrue(self._register(other)["encounter"].exists())

    # ==================================================================
    # THE GUARD CANNOT BE ROUTED AROUND
    # ==================================================================
    def test_20_a_direct_orm_create_is_refused(self):
        """The workflow is not the boundary; the model is."""
        patient = self._patient()
        self._encounter(patient, state="active")
        with self.assertRaises(ValidationError):
            self.env["hospital.encounter"].sudo().create(
                {
                    "patient_id": patient.id,
                    "encounter_type": "outpatient",
                    "company_id": self.company.id,
                }
            )

    def test_21_a_batch_create_cannot_smuggle_a_duplicate(self):
        """Two encounters for one patient in ONE create() call."""
        patient = self._patient()
        with self.assertRaises(ValidationError):
            self.env["hospital.encounter"].sudo().create(
                [
                    {"patient_id": patient.id, "encounter_type": "outpatient",
                     "company_id": self.company.id},
                    {"patient_id": patient.id, "encounter_type": "outpatient",
                     "company_id": self.company.id},
                ]
            )
        self.assertFalse(
            self.env["hospital.encounter"].sudo().search_count(
                [("patient_id", "=", patient.id)]
            ),
            "A refused batch must leave nothing behind.",
        )

    def test_22_the_guard_takes_a_lock_before_it_looks(self):
        """Serialization, not a hopeful SELECT-then-INSERT.

        Two concurrent registrations both read "no active episode" before
        either writes unless the check and the insert are one critical section.
        The advisory lock is what makes them one; this asserts it is actually
        taken, because a check without it is the bug in a costume.
        """
        import inspect

        from odoo.addons.yoya_reception_bridge.models import hospital_encounter

        source = inspect.getsource(
            hospital_encounter.HospitalEncounter._assert_no_active_episode
        )
        self.assertIn("_lock_patient_episode", source)
        lock_source = inspect.getsource(
            hospital_encounter.HospitalEncounter._lock_patient_episode
        )
        self.assertIn("pg_advisory_xact_lock", lock_source)

    # ==================================================================
    # WHAT MUST STAY ALLOWED
    # ==================================================================
    def test_30_repeated_observations_on_one_visit_remain_allowed(self):
        """The guard is about EPISODES, not observations.

        Vitals in this repository live on hospital.patient.evaluation, which
        carries a unique constraint on appointment_id -- so one visit has one
        evaluation record, updated as often as clinically needed. Recording
        into it repeatedly must stay legal and must not look like a new visit.
        """
        patient = self._patient()
        result = self._register(patient)
        encounter = result["encounter"]
        appointment = result["appointment"]

        evaluation = self.env["hospital.patient.evaluation"].sudo().create(
            {"patient_id": patient.id, "appointment_id": appointment.id}
        )
        for temperature in (37.1, 37.4, 38.0):
            evaluation.write({"temperature": temperature})
        self.assertAlmostEqual(evaluation.temperature, 38.0, places=1)

        self.assertEqual(encounter.state, "checked_in")
        self.assertEqual(
            self.env["hospital.encounter"].sudo().search_count(
                [("patient_id", "=", patient.id),
                 ("state", "not in", list(CLOSED))]
            ),
            1,
            "Repeated observations must not multiply the episode.",
        )

    # ==================================================================
    # ATOMICITY
    #
    # UAT hit this: the guard refused, the client saw the error, and two
    # phantom rows appeared in the queue anyway. hospital.encounter.create()
    # fires AFTER reception.workflow has already created the appointment, and
    # the API decorator catches the ValidationError and RETURNS a response --
    # which Odoo reads as a served request and commits.
    #
    # The pre-flight check in create_visit() is what makes the refusal happen
    # before anything exists. These tests pin that nothing survives.
    # ==================================================================
    def test_40_a_refused_registration_leaves_no_appointment(self):
        """THE reported defect. An orphan appointment is what UAT saw."""
        patient = self._patient()
        self._register(patient)
        before = self.env["hospital.appointment"].sudo().search_count(
            [("patient_id", "=", patient.id)]
        )

        with self.assertRaises(ValidationError):
            self._register(patient)

        self.assertEqual(
            self.env["hospital.appointment"].sudo().search_count(
                [("patient_id", "=", patient.id)]
            ),
            before,
            "A refused registration must not leave an appointment behind.",
        )

    def test_41_a_refused_registration_leaves_no_orphan_appointment(self):
        """Specifically: no confirmed appointment without an encounter."""
        patient = self._patient()
        self._register(patient)
        with self.assertRaises(ValidationError):
            self._register(patient)

        orphans = self.env["hospital.appointment"].sudo().search(
            [("patient_id", "=", patient.id), ("encounter_id", "=", False)]
        )
        self.assertFalse(
            orphans,
            "An appointment with no encounter is the phantom queue row: %s"
            % orphans.mapped("appointment_code"),
        )

    def test_42_the_check_happens_before_the_first_write(self):
        """Structural: the pre-flight must precede appointment creation.

        A guard that runs after the appointment exists depends entirely on the
        transaction being rolled back, and the API decorator is exactly the
        thing that stops that happening.
        """
        import inspect

        from odoo.addons.yoya_reception_bridge.models import reception_workflow

        source = inspect.getsource(
            reception_workflow.HospitalReceptionWorkflow.create_visit
        )
        preflight = source.index("assert_patient_has_no_active_episode")
        creation = source.index('["hospital.appointment"].create')
        self.assertLess(
            preflight, creation,
            "The active-episode check must run BEFORE the appointment is created.",
        )

    def test_43_the_api_route_wraps_the_mutation_in_a_savepoint(self):
        """Defence in depth: the decorator returns JSON, so the dispatcher
        commits. The savepoint is what unwinds the write first."""
        import inspect

        from odoo.addons.yoya_emr_api.controllers import reception

        source = inspect.getsource(
            reception.YoyaEmrReceptionController.create_visit
        )
        self.assertIn("env.cr.savepoint()", source)
        # The response must be built INSIDE the block, or a serialization
        # failure still commits the visit.
        savepoint = source.index("env.cr.savepoint()")
        serialize = source.index("serialize_visit_detail")
        self.assertLess(savepoint, serialize)

    def test_44_a_successful_registration_still_commits_everything(self):
        """The rollback path must not have cost the happy path anything."""
        patient = self._patient()
        result = self._register(patient)
        self.assertTrue(result["appointment"].exists())
        self.assertTrue(result["encounter"].exists())
        self.assertEqual(result["appointment"].encounter_id, result["encounter"])
        self.assertTrue(
            self.env["hospital.billing.account"].sudo().search_count(
                [("encounter_id", "=", result["encounter"].id)]
            ),
            "A successful visit still opens its billing account.",
        )

    def test_45_a_second_attempt_after_refusal_still_works_once_closed(self):
        """The refusal must not poison the patient's ability to return."""
        patient = self._patient()
        first = self._register(patient)
        with self.assertRaises(ValidationError):
            self._register(patient)

        first["encounter"].sudo().write({"state": "completed"})
        second = self._register(patient)
        self.assertTrue(second["encounter"].exists())
        self.assertNotEqual(second["encounter"], first["encounter"])

    def test_31_a_blocked_registration_creates_no_second_charge(self):
        """The refusal must roll back everything, not just the encounter."""
        patient = self._patient()
        self._register(patient)
        account = self.env["hospital.billing.account"].sudo().search(
            [("patient_id", "=", patient.id)]
        )
        charges_before = self.env["hospital.charge.line"].sudo().search_count(
            [("patient_id", "=", patient.id)]
        )

        with self.assertRaises(ValidationError):
            self._register(patient)

        self.assertEqual(
            self.env["hospital.billing.account"].sudo().search_count(
                [("patient_id", "=", patient.id)]
            ),
            len(account),
            "A refused registration must not open a second billing account.",
        )
        self.assertEqual(
            self.env["hospital.charge.line"].sudo().search_count(
                [("patient_id", "=", patient.id)]
            ),
            charges_before,
            "A refused registration must not raise a second consultation charge.",
        )

    def test_32_a_blocked_registration_consumes_no_sponsor_benefit(self):
        patient = self._patient()
        self._register(patient)
        before = self.env["hospital.charge.responsibility"].sudo().search_count(
            [("patient_id", "=", patient.id)]
        )
        with self.assertRaises(ValidationError):
            self._register(patient)
        self.assertEqual(
            self.env["hospital.charge.responsibility"].sudo().search_count(
                [("patient_id", "=", patient.id)]
            ),
            before,
            "A refused registration must not draw against the member's benefit.",
        )

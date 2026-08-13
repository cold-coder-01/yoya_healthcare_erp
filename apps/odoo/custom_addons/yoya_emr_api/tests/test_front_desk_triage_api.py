"""Phase B2.2A: the backend triage foundation the /front-desk UI will sit on.

Three things are proven here:

  1. clinical_scope now recognises the Front Desk Nurse EXPLICITLY, so the
     clinical evaluation endpoints stop answering 403 out_of_scope on the first
     triage of a brand new visit -- and plain Hospital Nurse scoping is
     untouched.
  2. POST .../start-triage claims the evaluation through
     action_start_evaluation(), creating at most one, and survives losing the
     unique(appointment_id) race.
  3. POST .../doctor keeps appointment / encounter / evaluation from drifting.

Nothing here takes money or posts accounting, and two tests assert that the
front desk still cannot.
"""
import json
import uuid

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import HttpCase, tagged

from odoo.addons.yoya_emr_api.services.clinical_scope import (
    build_appointment_scope_domain,
    hospital_groups,
)

G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_NURSE = "hospital_management.group_hospital_nurse"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_MANAGER = "hospital_management.group_hospital_manager"

VISIT = "/yoya-emr/api/v1/front-desk/visits/%s"
START_TRIAGE = "/yoya-emr/api/v1/front-desk/visits/%s/start-triage"
ASSIGN_DOCTOR = "/yoya-emr/api/v1/front-desk/visits/%s/doctor"
COMPLETE = "/yoya-emr/api/v1/clinical/evaluations/%s/complete"

EXPECTED_VITAL_KEYS = {
    "weight", "height", "temperature", "heart_rate", "respiratory_rate",
    "systolic_bp", "diastolic_bp", "spo2", "rbs", "head_circumference",
    "bmi", "bmi_state", "pain_level", "pain_note",
}


@tagged("post_install", "-at_install", "front_desk_triage_api")
class TestFrontDeskTriageApi(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.service = (
            cls.env["hospital.billing.service"]
            .sudo()
            .get_default_consultation_service(cls.company)
        )
        cls.service.sudo().write(
            {
                "default_price": 300.0, "fixed_fee": True,
                "prepayment_required": True, "coverage_auth_required": False,
                "active": True, "is_default_consultation": True,
            }
        )
        one = uuid.uuid4().hex[:6]
        two = uuid.uuid4().hex[:6]
        cls.department = cls.env["hospital.department"].sudo().create(
            {"name": "FDT API Dept %s" % one, "code": "TAA%s" % one.upper()}
        )
        cls.other_department = cls.env["hospital.department"].sudo().create(
            {"name": "FDT API Other %s" % two, "code": "TAB%s" % two.upper()}
        )

        cls.fd_password = "fdt-api-pw-1"
        cls.front_desk = cls._make_user("ta_fd", cls.fd_password, [G_FRONT_DESK_NURSE])
        cls.front_desk_b = cls._make_user("ta_fd_b", "fdt-api-pw-2", [G_FRONT_DESK_NURSE])
        cls.ward_nurse = cls._make_user("ta_nurse", "fdt-api-pw-3", [G_NURSE])
        cls.cashier = cls._make_user("ta_cashier", "fdt-api-pw-4", [G_CASHIER])
        cls.receptionist = cls._make_user(
            "ta_receptionist", "fdt-api-pw-5", [G_RECEPTIONIST]
        )
        cls.manager = cls._make_user("ta_manager", "fdt-api-pw-6", [G_MANAGER])

        cls.doctor_user = cls._make_user("ta_doc", "fdt-api-pw-7", [G_DOCTOR])
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {
                "name": "FDT API Doctor",
                "user_id": cls.doctor_user.id,
                "department_id": cls.department.id,
            }
        )
        cls.second_doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "FDT API Second Doctor", "department_id": cls.department.id}
        )
        cls.other_doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "FDT API Other Doctor", "department_id": cls.other_department.id}
        )

        # The plain ward nurse is department-scoped; the front desk deliberately
        # is not, and holds no permitted departments anywhere in this suite.
        cls.ward_nurse.sudo().write(
            {"yoya_permitted_department_ids": [(6, 0, cls.department.ids)]}
        )

    # Flipped by the first setUp in the class; see _warm_up_http below.
    _http_warmed = False

    def setUp(self):
        super().setUp()
        self._warm_up_http()

    def _warm_up_http(self):
        """Absorb the cost of the FIRST HTTP request of the run, once.

        Odoo builds its routing map lazily, on first request, by walking every
        controller in every installed module. On a cold instance with the
        accounting stack installed that regularly exceeds url_open's 12 second
        default, and the cost lands on whichever test happens to make the first
        request -- alphabetically, test_a_blocked_completion_leaves_no_trace.
        That test then failed with a socket read timeout rather than an
        assertion, which looked like a Start Triage defect and was not one:
        every later request in the same class, including identical Start Triage
        posts, succeeded.

        This pays that cost deliberately, once per class, against the auth=none
        health route -- no session, no ORM, nothing but routing and dispatch.
        The assertion-carrying requests keep the standard 12 second timeout, so
        a genuine hang in a real endpoint still fails the suite.
        """
        if type(self)._http_warmed:
            return
        type(self)._http_warmed = True
        self.url_open("/yoya-emr/api/v1/health", timeout=180)

    @classmethod
    def _make_user(cls, login, password, group_xmlids):
        return (
            cls.env["res.users"].sudo().create(
                {
                    "name": login,
                    "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
                    "password": password,
                    "company_id": cls.env.company.id,
                    "company_ids": [(6, 0, cls.env.company.ids)],
                    "groups_id": [
                        (6, 0, [cls.env.ref("base.group_user").id]
                         + [cls.env.ref(x).id for x in group_xmlids])
                    ],
                }
            )
        )

    # ------------------------------------------------------------------
    def _register(self, department=None, doctor=None):
        result = self.env["hospital.reception.workflow"].with_user(
            self.front_desk
        ).create_visit(
            patient_values={"name": "TA Patient %s" % uuid.uuid4().hex[:6]},
            department=department or self.department,
            doctor=doctor,
        )
        return result["appointment"].sudo(), result["encounter"].sudo()

    def _post(self, url, body=None, user=None, password=None):
        self.authenticate((user or self.front_desk).login, password or self.fd_password)
        response = self.url_open(
            url,
            data=json.dumps(body or {}),
            headers={"Content-Type": "application/json"},
        )
        return response, json.loads(response.text)

    def _get(self, url, user=None, password=None):
        self.authenticate((user or self.front_desk).login, password or self.fd_password)
        response = self.url_open(url)
        return response, json.loads(response.text)

    def _evaluations_for(self, appointment):
        return self.env["hospital.patient.evaluation"].sudo().search(
            [("appointment_id", "=", appointment.id)]
        )

    def _complete(self, appointment, complaint="Fever since yesterday", priority="routine"):
        """Complete a triage the way the UI does: minimum data, then action_done.

        _assert_triage_minimum_data now rejects an empty completion, so every
        test that just wants a DONE evaluation has to record the complaint and
        priority first.
        """
        evaluation = self._evaluations_for(appointment).with_user(self.front_desk)
        evaluation.write({"chief_complaint": complaint, "triage_priority": priority})
        evaluation.action_done()
        return evaluation

    # ==================================================================
    # 1. clinical_scope
    # ==================================================================
    def test_front_desk_nurse_is_recognised_explicitly(self):
        groups = hospital_groups(self.env(user=self.front_desk))
        self.assertTrue(groups["front_desk_nurse"])
        # Still a nurse -- that is where the evaluation ACL comes from.
        self.assertTrue(groups["nurse"])

        self.assertFalse(
            hospital_groups(self.env(user=self.ward_nurse))["front_desk_nurse"]
        )

    def test_front_desk_scopes_a_new_visit_with_no_evaluation(self):
        """THE blocker. No evaluation exists yet, and the desk has no departments.

        The old nurse branch answered DENY_ALL here, so the clinical save
        endpoint 403'd on the first triage of every visit.
        """
        appointment, _encounter = self._register()
        env = self.env(user=self.front_desk)
        self.assertFalse(self.front_desk.yoya_permitted_department_ids)
        self.assertFalse(self._evaluations_for(appointment))

        self.assertEqual(build_appointment_scope_domain(env, [("id", "=", appointment.id)]), [])
        self.assertEqual(
            env["hospital.appointment"].search_count([("id", "=", appointment.id)]), 1
        )

    def test_front_desk_scopes_a_visit_outside_any_nurse_department(self):
        appointment, _encounter = self._register(department=self.other_department)
        env = self.env(user=self.front_desk)

        self.assertEqual(build_appointment_scope_domain(env, [("id", "=", appointment.id)]), [])

    def test_plain_nurse_scope_is_unchanged(self):
        """Department-scoped, and denied outright with nothing to match on."""
        in_scope, _ = self._register(department=self.department)
        out_of_scope, _ = self._register(department=self.other_department)
        env = self.env(user=self.ward_nurse)

        domain = build_appointment_scope_domain(env, [("id", "in", [in_scope.id, out_of_scope.id])])
        self.assertNotEqual(domain, [], "plain nurse must not become unrestricted")

        reachable = env["hospital.appointment"].search(
            domain + [("id", "in", [in_scope.id, out_of_scope.id])]
        )
        self.assertIn(in_scope.id, reachable.ids)
        self.assertNotIn(out_of_scope.id, reachable.ids)

    def test_other_roles_are_unchanged(self):
        appointment, _encounter = self._register()
        base = [("id", "=", appointment.id)]

        # Manager stays unrestricted, doctor stays assignment-scoped,
        # receptionist stays evaluation-linked, and a role with none of the
        # hospital groups stays denied.
        self.assertEqual(build_appointment_scope_domain(self.env(user=self.manager), base), [])
        self.assertEqual(
            build_appointment_scope_domain(self.env(user=self.doctor_user), base),
            [("doctor_id.user_id", "=", self.doctor_user.id)],
        )
        self.assertNotEqual(
            build_appointment_scope_domain(self.env(user=self.receptionist), base), []
        )
        self.assertEqual(
            build_appointment_scope_domain(self.env(user=self.cashier), base),
            [("id", "=", 0)],
        )

    # ==================================================================
    # 2. Start Triage
    # ==================================================================
    def test_start_triage_creates_exactly_one_evaluation_and_stamps_started_at(self):
        appointment, _encounter = self._register()

        response, payload = self._post(START_TRIAGE % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        evaluations = self._evaluations_for(appointment)
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations.state, "draft")
        self.assertTrue(evaluations.started_at)
        self.assertEqual(evaluations.assigned_nurse_id, self.front_desk)
        self.assertEqual(evaluations.encounter_id, appointment.encounter_id)
        self.assertEqual(payload["data"]["evaluation"]["id"], evaluations.id)

    def test_start_triage_moves_intake_to_triage(self):
        appointment, _encounter = self._register()

        _, before = self._get(VISIT % appointment.id)
        self.assertEqual(before["data"]["row"]["queue_stage"], "intake")

        _, payload = self._post(START_TRIAGE % appointment.id)
        self.assertEqual(payload["data"]["row"]["queue_stage"], "triage")

        _, after = self._get(VISIT % appointment.id)
        self.assertEqual(after["data"]["row"]["queue_stage"], "triage")

    def test_start_triage_is_idempotent(self):
        appointment, _encounter = self._register()

        _, first = self._post(START_TRIAGE % appointment.id)
        first_started = self._evaluations_for(appointment).started_at

        response, second = self._post(START_TRIAGE % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self._evaluations_for(appointment)), 1)
        self.assertEqual(self._evaluations_for(appointment).started_at, first_started)
        self.assertEqual(
            second["data"]["evaluation"]["id"], first["data"]["evaluation"]["id"]
        )
        self.assertEqual(second["data"]["row"]["queue_stage"], "triage")

    def test_start_triage_reuses_an_existing_unstarted_evaluation(self):
        """A draft created by the clinical save endpoint must be claimed, not duplicated."""
        appointment, _encounter = self._register()
        existing = self.env["hospital.patient.evaluation"].with_user(
            self.front_desk
        ).create(
            {"patient_id": appointment.patient_id.id, "appointment_id": appointment.id}
        )
        self.assertFalse(existing.sudo().started_at)

        _, payload = self._post(START_TRIAGE % appointment.id)

        self.assertEqual(len(self._evaluations_for(appointment)), 1)
        self.assertEqual(payload["data"]["evaluation"]["id"], existing.id)
        self.assertTrue(self._evaluations_for(appointment).started_at)

    def test_start_triage_rejects_a_completed_evaluation_cleanly(self):
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        self._complete(appointment)

        response, payload = self._post(START_TRIAGE % appointment.id)

        self.assertEqual(response.status_code, 409)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "triage_not_startable")

    def test_start_triage_recovers_from_the_unique_appointment_race(self):
        """Simulates losing the INSERT race: another transaction got there first.

        The evaluation is created behind the endpoint's back, exactly as a
        concurrent request would have, and the endpoint must return the normal
        canonical state instead of leaking an IntegrityError as a 500.
        """
        appointment, _encounter = self._register()
        winner = self.env["hospital.patient.evaluation"].sudo().create(
            {
                "patient_id": appointment.patient_id.id,
                "appointment_id": appointment.id,
                "assigned_nurse_id": self.front_desk_b.id,
            }
        )

        response, payload = self._post(START_TRIAGE % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["evaluation"]["id"], winner.id)
        self.assertEqual(len(self._evaluations_for(appointment)), 1)
        self.assertEqual(payload["data"]["row"]["queue_stage"], "triage")
        # The winner keeps the record; starting never steals ownership.
        self.assertEqual(self._evaluations_for(appointment).assigned_nurse_id, self.front_desk_b)

    def test_start_triage_needs_no_payment(self):
        appointment, encounter = self._register()
        encounter.invalidate_recordset()
        self.assertFalse(encounter.reception_clearance_ok)

        _, payload = self._post(START_TRIAGE % appointment.id)

        self.assertEqual(payload["data"]["row"]["queue_stage"], "triage")
        self.assertFalse(payload["data"]["clearance"]["ok"])
        self.assertGreater(payload["data"]["clearance"]["outstanding"], 0.0)

    def test_second_front_desk_nurse_sees_the_started_triage(self):
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)

        _, payload = self._get(
            VISIT % appointment.id, user=self.front_desk_b, password="fdt-api-pw-2"
        )

        self.assertEqual(payload["data"]["row"]["queue_stage"], "triage")
        self.assertIsNotNone(payload["data"]["evaluation"])
        self.assertEqual(
            payload["data"]["evaluation"]["assigned_nurse"]["id"], self.front_desk.id
        )

    def test_start_triage_is_denied_to_the_cashier(self):
        appointment, _encounter = self._register()

        response, payload = self._post(
            START_TRIAGE % appointment.id, user=self.cashier, password="fdt-api-pw-4"
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(payload["success"])
        self.assertFalse(self._evaluations_for(appointment))

    # ==================================================================
    # 3. Doctor assignment
    # ==================================================================
    def test_assign_doctor_synchronizes_appointment_and_encounter(self):
        appointment, encounter = self._register()
        self.assertFalse(appointment.doctor_id)
        self.assertFalse(encounter.primary_doctor_id)

        response, payload = self._post(
            ASSIGN_DOCTOR % appointment.id, {"doctor_id": self.doctor.id}
        )

        self.assertEqual(response.status_code, 200)
        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertEqual(appointment.doctor_id, self.doctor)
        self.assertEqual(encounter.primary_doctor_id, self.doctor)
        self.assertEqual(payload["data"]["visit"]["doctor"]["id"], self.doctor.id)

    def test_assign_doctor_rejects_another_department(self):
        appointment, _encounter = self._register()

        response, payload = self._post(
            ASSIGN_DOCTOR % appointment.id, {"doctor_id": self.other_doctor.id}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "validation_error")
        appointment.invalidate_recordset()
        self.assertFalse(appointment.doctor_id)

    def test_assign_doctor_validates_its_payload(self):
        appointment, _encounter = self._register()

        _, missing = self._post(ASSIGN_DOCTOR % appointment.id, {})
        self.assertEqual(missing["error"]["code"], "invalid_field")

        _, unknown = self._post(
            ASSIGN_DOCTOR % appointment.id,
            {"doctor_id": self.doctor.id, "department_id": self.department.id},
        )
        self.assertEqual(unknown["error"]["code"], "unknown_field")

        _, absent = self._post(ASSIGN_DOCTOR % appointment.id, {"doctor_id": 0})
        self.assertEqual(absent["error"]["code"], "invalid_field")

        appointment.invalidate_recordset()
        self.assertFalse(appointment.doctor_id)

    def test_assign_doctor_after_start_triage_syncs_the_draft_evaluation(self):
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)

        self._post(ASSIGN_DOCTOR % appointment.id, {"doctor_id": self.second_doctor.id})

        evaluation = self._evaluations_for(appointment)
        self.assertEqual(evaluation.physician_id, self.second_doctor)
        self.assertEqual(evaluation.state, "draft")

    def test_assign_doctor_is_financially_inert(self):
        appointment, encounter = self._register()
        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        encounter.invalidate_recordset()
        before_outstanding = encounter.reception_outstanding_amount
        before_clearance = account.financial_clearance_state
        before_receipts = self.env["hospital.charge.receipt"].sudo().search_count([])

        _, payload = self._post(
            ASSIGN_DOCTOR % appointment.id, {"doctor_id": self.doctor.id}
        )

        account.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertEqual(encounter.reception_outstanding_amount, before_outstanding)
        self.assertEqual(account.financial_clearance_state, before_clearance)
        self.assertEqual(
            self.env["hospital.charge.receipt"].sudo().search_count([]), before_receipts
        )
        self.assertEqual(payload["data"]["row"]["queue_stage"], "intake")
        self.assertFalse(payload["data"]["clearance"]["ok"])

    def test_assign_doctor_is_denied_to_a_plain_nurse(self):
        appointment, _encounter = self._register()

        response, _payload = self._post(
            ASSIGN_DOCTOR % appointment.id,
            {"doctor_id": self.doctor.id},
            user=self.ward_nurse,
            password="fdt-api-pw-3",
        )

        self.assertEqual(response.status_code, 403)
        appointment.invalidate_recordset()
        self.assertFalse(appointment.doctor_id)

    # ==================================================================
    # 4. SpO2 read model
    # ==================================================================
    def test_spo2_is_present_in_the_front_desk_detail(self):
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        self._evaluations_for(appointment).with_user(self.front_desk).write(
            {"spo2": 97.0}
        )

        _, payload = self._get(VISIT % appointment.id)

        vitals = payload["data"]["evaluation"]["vitals"]
        self.assertEqual(set(vitals), EXPECTED_VITAL_KEYS)
        self.assertEqual(vitals["spo2"], 97.0)

    # ==================================================================
    # 4b. Completion minimum-data rule
    #
    # Enforced in action_done() so /front-desk, legacy /triage, the Odoo form
    # button and any RPC client all obey it. A browser-only check would not be
    # a rule.
    # ==================================================================
    def _draft_of(self, appointment):
        return self._evaluations_for(appointment).with_user(self.front_desk)

    def test_complete_is_blocked_without_a_chief_complaint(self):
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        evaluation = self._draft_of(appointment)
        evaluation.write({"triage_priority": "urgent"})

        with self.assertRaises(ValidationError):
            evaluation.action_done()

    def test_complete_is_blocked_by_a_whitespace_only_chief_complaint(self):
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        evaluation = self._draft_of(appointment)
        evaluation.write({"chief_complaint": "   \n  ", "triage_priority": "routine"})

        with self.assertRaises(ValidationError):
            evaluation.action_done()

    # ------------------------------------------------------------------
    # triage_priority: the ABSENT case is unreachable, so it is not tested.
    #
    # The original test here wrote triage_priority=False to reach the guard in
    # _assert_triage_minimum_data. That state cannot exist: the field is
    # required=True with default='routine' and a NOT NULL column, so the write
    # died with an IntegrityError at flush -- before action_done() was ever
    # called. It was asserting against SQL that the schema forbids.
    #
    # The product rule ("Complete Triage requires a valid priority") is real and
    # unchanged; it is simply guaranteed by the model and the database rather
    # than by the completion guard. The three tests below cover it on the paths
    # that actually exist. The guard itself is kept as defence in depth for
    # in-memory records, and is documented as such in the model.
    # ------------------------------------------------------------------
    def test_new_evaluation_defaults_to_routine_priority(self):
        """Start Triage never produces an evaluation without a priority."""
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)

        self.assertEqual(
            self._evaluations_for(appointment).triage_priority, "routine"
        )

    def test_invalid_triage_priority_is_rejected(self):
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        evaluation = self._draft_of(appointment)

        # Odoo validates a Selection on assignment, so this raises immediately
        # and leaves the transaction usable -- unlike the NOT NULL violation.
        with self.assertRaises(ValueError):
            evaluation.write({"triage_priority": "very_urgent"})

        self.assertEqual(
            self._evaluations_for(appointment).triage_priority, "routine"
        )

    def test_completion_always_carries_a_valid_priority(self):
        """A nurse who never touches the priority still completes with one."""
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        evaluation = self._draft_of(appointment)
        evaluation.write({"chief_complaint": "Chest pain"})

        evaluation.action_done()

        stored = self._evaluations_for(appointment)
        stored.invalidate_recordset()
        self.assertEqual(stored.state, "done")
        self.assertEqual(stored.triage_priority, "routine")

    def test_a_blocked_completion_leaves_no_trace(self):
        """Rejected BEFORE super(): no state, no completed_at, no stage move."""
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        evaluation = self._draft_of(appointment)

        with self.assertRaises(ValidationError):
            evaluation.action_done()

        stored = self._evaluations_for(appointment)
        stored.invalidate_recordset()
        self.assertEqual(stored.state, "draft")
        self.assertFalse(stored.completed_at)
        self.assertTrue(stored.started_at)

        _, payload = self._get(VISIT % appointment.id)
        self.assertEqual(payload["data"]["row"]["queue_stage"], "triage")

    def test_complete_succeeds_with_complaint_and_priority(self):
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)

        self._complete(appointment, complaint="Headache", priority="urgent")

        stored = self._evaluations_for(appointment)
        stored.invalidate_recordset()
        self.assertEqual(stored.state, "done")
        self.assertTrue(stored.completed_at)

    def test_complete_endpoint_surfaces_the_rule_as_a_validation_error(self):
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        evaluation = self._evaluations_for(appointment)

        response, payload = self._post(COMPLETE % evaluation.id)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "validation_error")
        self.assertIn("chief complaint", payload["error"]["message"].lower())

    def test_vitals_are_not_required_to_complete(self):
        """A measurement can be genuinely unavailable; that must not block triage."""
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)

        self._complete(appointment, complaint="Unwell, refuses observations")

        stored = self._evaluations_for(appointment)
        stored.invalidate_recordset()
        self.assertEqual(stored.state, "done")
        self.assertFalse(stored.temperature)
        self.assertFalse(stored.heart_rate)
        self.assertFalse(stored.spo2)

    def test_save_draft_validation_is_unchanged(self):
        """Partial work is exactly what a draft is for."""
        appointment, _encounter = self._register()
        self._post(START_TRIAGE % appointment.id)

        self._draft_of(appointment).write({"temperature": 37.2})

        stored = self._evaluations_for(appointment)
        stored.invalidate_recordset()
        self.assertEqual(stored.state, "draft")
        self.assertEqual(stored.temperature, 37.2)
        self.assertFalse(stored.chief_complaint)

        _, payload = self._get(VISIT % appointment.id)
        self.assertEqual(payload["data"]["row"]["queue_stage"], "triage")

    # ==================================================================
    # 5. Financial boundary, still
    # ==================================================================
    def test_front_desk_nurse_still_cannot_record_payment(self):
        appointment, encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        account = encounter.billing_account_id.sudo()
        before = self.env["hospital.charge.receipt"].sudo().search_count([])

        with self.assertRaises(AccessError):
            account.with_user(self.front_desk).record_operational_payment(
                300.0, "cash", intake_token=uuid.uuid4().hex
            )

        self.assertEqual(
            self.env["hospital.charge.receipt"].sudo().search_count([]), before
        )

    def test_front_desk_nurse_still_cannot_post_accounting(self):
        appointment, encounter = self._register()
        self._post(START_TRIAGE % appointment.id)
        self._complete(appointment)

        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        receipt = account.with_user(self.cashier).record_operational_payment(
            account.amount_due_for_clearance, "cash", intake_token=uuid.uuid4().hex
        ).sudo()

        with self.assertRaises(AccessError):
            receipt.with_user(self.front_desk).action_post_receipt_accounting()

        receipt.invalidate_recordset()
        self.assertFalse(receipt.accounting_posted)

    def test_completion_stage_logic_is_unchanged(self):
        appointment, encounter = self._register()
        self._post(START_TRIAGE % appointment.id)

        self._complete(appointment)
        _, payload = self._get(VISIT % appointment.id)
        self.assertEqual(payload["data"]["row"]["queue_stage"], "awaiting_cashier")

        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        account.with_user(self.cashier).record_operational_payment(
            account.amount_due_for_clearance, "cash", intake_token=uuid.uuid4().hex
        )

        _, payload = self._get(VISIT % appointment.id)
        self.assertEqual(payload["data"]["row"]["queue_stage"], "ready_doctor")

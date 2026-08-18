"""The Doctor Desk read surface and its one mutation.

WHAT THESE TESTS ARE FOR
------------------------
Two properties carry the whole integration, and each has a class below:

  1. SCOPE. A pure doctor sees their own assigned visits and nothing else, and
     no client parameter can widen that.
  2. THE STAGE IS AUTHORITATIVE. 'Ready' means
     hospital.appointment.front_desk_stage == 'ready_doctor' and nothing else.
     A visit that is triage-complete but still owes money at the desk reads
     'awaiting_cashier' and must never be presented as workable -- that is the
     precise failure the interim Doctor Desk adapter could produce, because it
     approximated readiness from triage state plus the consultation-scoped
     billing flag.

A third class pins the confidentiality boundary: no amount, no receipt, no
sponsor allocation, no agreement name, no membership number and no payer name
may appear anywhere in a Doctor payload.
"""
import json
import uuid
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, tagged

from odoo.addons.yoya_emr_api.services.doctor_serializers import (
    DOCTOR_CLEARANCE_REASONS,
    READY_STAGE,
)

G_DOCTOR = "hospital_management.group_hospital_doctor"
G_NURSE = "hospital_management.group_hospital_nurse"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_MANAGER = "hospital_management.group_hospital_manager"

# Where the CONTROLLER looks the serializer up, not where it is defined --
# patching the definition would not affect the name already bound in the
# controller module. Same target convention as
# test_cashier_payment_api.SERIALIZER_TARGET.
VISIT_SERIALIZER_TARGET = (
    "odoo.addons.yoya_emr_api.controllers.doctor.serialize_visit_detail"
)

SESSION = "/yoya-emr/api/v1/doctor/session"
WORKLIST = "/yoya-emr/api/v1/doctor/worklist"
VISIT = "/yoya-emr/api/v1/doctor/visits/%s"
START = "/yoya-emr/api/v1/doctor/visits/%s/start-consultation"

EXPECTED_ROW_KEYS = {
    "appointment_id", "appointment_code", "appointment_date", "state",
    "patient", "department", "doctor", "encounter", "queue_stage",
    "visit_type", "triage_status", "triage_priority", "chief_complaint",
    "urgent", "clearance", "can_start_consultation",
}

EXPECTED_CLEARANCE_KEYS = {"blocked", "state", "reason"}

# Substrings that must never appear in a Doctor payload, whatever the visit's
# financial situation. 'sponsor'/'agreement'/'membership' are commercial
# vocabulary; the amount checks are done numerically in the test itself.
FORBIDDEN_KEYS = (
    "amount", "balance", "outstanding", "required_amount", "paid_amount",
    "receipt", "sponsor", "agreement", "membership", "policy_number",
    "payer_name", "payer_id", "billing_clearance_message", "tariff", "price",
)


class DoctorDeskCase(HttpCase):
    """Shared fixture: two doctors, a nurse, a cashier and a working visit."""

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
            {"name": "Doc Desk Dept %s" % one, "code": "DDA%s" % one.upper()}
        )
        cls.other_department = cls.env["hospital.department"].sudo().create(
            {"name": "Doc Desk Other %s" % two, "code": "DDB%s" % two.upper()}
        )

        # The front desk registers and triages; it is not under test here, but
        # a visit cannot exist without it.
        cls.fd_password = "dd-fd-pw-1"
        cls.front_desk = cls._make_user("dd_fd", cls.fd_password, [G_FRONT_DESK_NURSE])
        cls.cashier_password = "dd-cashier-pw-1"
        cls.cashier = cls._make_user("dd_cashier", cls.cashier_password, [G_CASHIER])

        cls.doctor_password = "dd-doc-pw-1"
        cls.doctor_user = cls._make_user("dd_doc", cls.doctor_password, [G_DOCTOR])
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {
                "name": "Doc Desk Doctor",
                "user_id": cls.doctor_user.id,
                "department_id": cls.department.id,
            }
        )

        cls.other_password = "dd-other-pw-1"
        cls.other_user = cls._make_user("dd_other", cls.other_password, [G_DOCTOR])
        cls.other_doctor = cls.env["hospital.doctor"].sudo().create(
            {
                "name": "Doc Desk Other Doctor",
                "user_id": cls.other_user.id,
                "department_id": cls.other_department.id,
            }
        )

        cls.nurse_password = "dd-nurse-pw-1"
        cls.nurse = cls._make_user("dd_nurse", cls.nurse_password, [G_NURSE])
        cls.manager_password = "dd-manager-pw-1"
        cls.manager = cls._make_user("dd_manager", cls.manager_password, [G_MANAGER])

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
    # Fixture helpers. All run as the roles that really perform each act.
    # ------------------------------------------------------------------
    def _register(self, doctor=None, department=None, visit_type="routine"):
        result = self.env["hospital.reception.workflow"].with_user(
            self.front_desk
        ).create_visit(
            patient_values={"name": "DD Patient %s" % uuid.uuid4().hex[:6]},
            visit_type=visit_type,
            department=department or self.department,
            doctor=doctor or self.doctor,
        )
        return result["appointment"].sudo(), result["encounter"].sudo()

    def _triage(self, appointment, complete=False, priority="routine"):
        evaluation = self.env["hospital.patient.evaluation"].with_user(
            self.front_desk
        ).create(
            {
                "patient_id": appointment.patient_id.id,
                "appointment_id": appointment.id,
                "chief_complaint": "DD complaint",
                "triage_priority": priority,
            }
        )
        evaluation.action_start_evaluation()
        if complete:
            evaluation.action_done()
        return evaluation.sudo()

    def _pay(self, encounter):
        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        return account.with_user(self.cashier).record_operational_payment(
            account.amount_due_for_clearance, "cash", intake_token=uuid.uuid4().hex
        )

    def _ready_visit(self, doctor=None):
        """A visit that has genuinely reached ready_doctor."""
        appointment, encounter = self._register(doctor=doctor)
        self._triage(appointment, complete=True)
        self._pay(encounter)
        appointment.invalidate_recordset()
        return appointment, encounter

    # ------------------------------------------------------------------
    def _auth(self, user=None, password=None):
        self.authenticate(
            (user or self.doctor_user).login, password or self.doctor_password
        )

    def _get(self, url, user=None, password=None, **params):
        self._auth(user, password)
        query = "&".join(
            "%s=%s" % (k, v) for k, v in params.items() if v is not None
        )
        response = self.url_open("%s%s" % (url, "?" + query if query else ""))
        return response, json.loads(response.text)

    def _post(self, url, user=None, password=None):
        self._auth(user, password)
        response = self.url_open(
            url, data="{}", headers={"Content-Type": "application/json"}
        )
        return response, json.loads(response.text)

    def _rows(self, payload):
        return {row["appointment_id"]: row for row in payload["data"]["rows"]}


@tagged("post_install", "-at_install", "doctor_desk")
class TestDoctorDeskScope(DoctorDeskCase):
    """Who may open the desk, and whose visits they see."""

    def test_pure_doctor_sees_own_assigned_visit(self):
        mine, _ = self._register(doctor=self.doctor)

        response, payload = self._get(WORKLIST)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        rows = self._rows(payload)
        self.assertIn(mine.id, rows)
        self.assertEqual(set(rows[mine.id]), EXPECTED_ROW_KEYS)
        self.assertEqual(rows[mine.id]["doctor"]["id"], self.doctor.id)

    def test_doctor_cannot_see_another_doctors_visit(self):
        mine, _ = self._register(doctor=self.doctor)
        theirs, _ = self._register(
            doctor=self.other_doctor, department=self.other_department
        )

        _, payload = self._get(WORKLIST)
        rows = self._rows(payload)

        self.assertIn(mine.id, rows)
        self.assertNotIn(theirs.id, rows)

    def test_visit_detail_respects_scope(self):
        """Another doctor's visit is INDISTINGUISHABLE from one that does not exist.

        404, deliberately, and this is stronger than a 403 rather than weaker.
        find_appointment_in_scope probes with search_count() as the CALLER, so
        rule_appointment_doctor (doctor_id.user_id = me) has already hidden the
        record before the explicit scope domain is consulted. The desk
        therefore cannot be used to confirm that a colleague's visit exists, or
        to enumerate appointment ids by comparing 403s against 404s.

        The 'out_of_scope' branch is still reachable for role combinations
        whose record rule is broader than the API's own domain; it is simply
        not the answer a pure doctor gets, and asserting 403 here would pin a
        weaker property than the code actually provides.
        """
        theirs, _ = self._register(
            doctor=self.other_doctor, department=self.other_department
        )

        response, payload = self._get(VISIT % theirs.id)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "visit_not_found")
        # The same answer a genuinely absent id produces -- that identity is
        # the non-disclosure property.
        missing_response, missing_payload = self._get(VISIT % 99999999)
        self.assertEqual(missing_response.status_code, response.status_code)
        self.assertEqual(missing_payload["error"]["code"], payload["error"]["code"])

    def test_visit_detail_serves_own_visit(self):
        mine, _ = self._register(doctor=self.doctor)

        response, payload = self._get(VISIT % mine.id)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["data"]["visit"]["appointment_id"], mine.id)
        self.assertEqual(payload["data"]["patient"]["id"], mine.patient_id.id)

    def test_department_filter_cannot_widen_scope(self):
        """A client-supplied filter NARROWS an already-scoped set.

        Naming the other doctor's department must not reach their visit: the
        scope domain is ANDed outside every caller filter.
        """
        theirs, _ = self._register(
            doctor=self.other_doctor, department=self.other_department
        )

        _, payload = self._get(WORKLIST, department_id=self.other_department.id)

        self.assertNotIn(theirs.id, self._rows(payload))

    def test_unknown_query_parameter_cannot_inject_a_domain(self):
        """A doctor_id parameter does not exist and is ignored, not honoured."""
        theirs, _ = self._register(
            doctor=self.other_doctor, department=self.other_department
        )

        _, payload = self._get(WORKLIST, doctor_id=self.other_doctor.id)

        self.assertTrue(payload["success"])
        self.assertNotIn(theirs.id, self._rows(payload))

    def test_nurse_is_denied_the_desk(self):
        self._register(doctor=self.doctor)

        response, payload = self._get(
            WORKLIST, user=self.nurse, password=self.nurse_password
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "access_denied")

    def test_front_desk_nurse_and_cashier_are_denied_the_desk(self):
        for user, password in (
            (self.front_desk, self.fd_password),
            (self.cashier, self.cashier_password),
        ):
            response, payload = self._get(WORKLIST, user=user, password=password)
            self.assertEqual(response.status_code, 403, user.login)
            self.assertEqual(payload["error"]["code"], "access_denied")

    def test_manager_may_open_the_desk_with_existing_clinical_semantics(self):
        """Manager scope is clinical_scope's, unchanged: unrestricted.

        build_appointment_scope_domain returns [] for a manager, and that is
        the pre-existing behaviour of every clinical read in this addon. The
        Doctor Desk reuses it rather than inventing a second answer.
        """
        mine, _ = self._register(doctor=self.doctor)
        theirs, _ = self._register(
            doctor=self.other_doctor, department=self.other_department
        )

        response, payload = self._get(
            WORKLIST, user=self.manager, password=self.manager_password
        )

        self.assertEqual(response.status_code, 200)
        rows = self._rows(payload)
        self.assertIn(mine.id, rows)
        self.assertIn(theirs.id, rows)

    def test_session_reports_identity_and_capabilities(self):
        response, payload = self._get(SESSION)

        self.assertEqual(response.status_code, 200)
        data = payload["data"]
        self.assertEqual(data["user"]["id"], self.doctor_user.id)
        self.assertEqual(data["doctor"]["id"], self.doctor.id)
        self.assertTrue(data["is_doctor"])
        self.assertTrue(data["capabilities"]["doctor_desk"])

    def test_session_tells_a_nurse_plainly_that_the_desk_is_not_theirs(self):
        """Reachable without the role on purpose: the shell needs an answer."""
        response, payload = self._get(
            SESSION, user=self.nurse, password=self.nurse_password
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["data"]["is_doctor"])
        self.assertFalse(payload["data"]["capabilities"]["doctor_desk"])


@tagged("post_install", "-at_install", "doctor_desk")
class TestDoctorDeskQueueSemantics(DoctorDeskCase):
    """'Ready' is front_desk_stage == ready_doctor, and nothing else."""

    def test_ready_doctor_is_serialized_authoritatively(self):
        appointment, _ = self._ready_visit()

        _, payload = self._get(WORKLIST)
        row = self._rows(payload)[appointment.id]

        self.assertEqual(row["queue_stage"], READY_STAGE)
        self.assertEqual(row["queue_stage"], appointment.front_desk_stage)
        self.assertEqual(row["state"], "confirmed")
        self.assertFalse(row["clearance"]["blocked"])
        self.assertTrue(row["can_start_consultation"])

    def test_awaiting_cashier_is_never_represented_as_ready(self):
        """THE REGRESSION THIS SURFACE EXISTS TO PREVENT.

        Triage is complete and the consultation-scoped billing flag may well be
        satisfiable, but encounter-wide clearance is not: the patient still owes
        money at the desk. The authoritative stage says awaiting_cashier, and
        nothing in the payload may contradict it.
        """
        appointment, _ = self._register()
        self._triage(appointment, complete=True)
        appointment.invalidate_recordset()

        _, payload = self._get(WORKLIST)
        row = self._rows(payload)[appointment.id]

        self.assertEqual(row["queue_stage"], "awaiting_cashier")
        self.assertNotEqual(row["queue_stage"], READY_STAGE)
        # Triage really is done -- this is exactly the state an approximation
        # from triage_status would have called ready.
        self.assertEqual(row["triage_status"], "completed")
        self.assertTrue(row["clearance"]["blocked"])
        self.assertFalse(row["can_start_consultation"])

    def test_triage_incomplete_is_never_represented_as_ready(self):
        appointment, encounter = self._register()
        self._pay(encounter)
        self._triage(appointment, complete=False)
        appointment.invalidate_recordset()

        _, payload = self._get(WORKLIST)
        row = self._rows(payload)[appointment.id]

        self.assertEqual(row["queue_stage"], "triage")
        self.assertNotEqual(row["queue_stage"], READY_STAGE)
        self.assertFalse(row["can_start_consultation"])

    def test_stage_progresses_intake_triage_cashier_ready(self):
        appointment, encounter = self._register()

        _, payload = self._get(WORKLIST)
        self.assertEqual(self._rows(payload)[appointment.id]["queue_stage"], "intake")

        evaluation = self._triage(appointment)
        _, payload = self._get(WORKLIST)
        self.assertEqual(self._rows(payload)[appointment.id]["queue_stage"], "triage")

        evaluation.with_user(self.front_desk).action_done()
        _, payload = self._get(WORKLIST)
        self.assertEqual(
            self._rows(payload)[appointment.id]["queue_stage"], "awaiting_cashier"
        )

        self._pay(encounter)
        _, payload = self._get(WORKLIST)
        self.assertEqual(self._rows(payload)[appointment.id]["queue_stage"], READY_STAGE)

    def test_in_consultation_and_done_stay_in_the_worklist(self):
        """The Finished bucket is history the doctor still needs today."""
        appointment, _ = self._ready_visit()
        appointment.with_user(self.doctor_user).action_start_consultation()
        appointment.invalidate_recordset()

        _, payload = self._get(WORKLIST)
        row = self._rows(payload)[appointment.id]
        self.assertEqual(row["queue_stage"], "in_consultation")
        self.assertEqual(row["state"], "in_consultation")
        self.assertEqual(payload["data"]["counts"]["finished"], 1)
        # Already started: the desk must not offer to start it again.
        self.assertFalse(row["can_start_consultation"])

    def test_counts_agree_with_the_rows_they_describe(self):
        ready, _ = self._ready_visit()
        waiting, _ = self._register()

        _, payload = self._get(WORKLIST)
        counts = payload["data"]["counts"]
        rows = self._rows(payload)

        self.assertEqual(rows[ready.id]["queue_stage"], READY_STAGE)
        self.assertEqual(rows[waiting.id]["queue_stage"], "intake")
        self.assertEqual(counts["review"], 1)
        self.assertEqual(counts["wait"], 1)
        self.assertEqual(
            counts["wait"] + counts["review"] + counts["finished"], counts["all"]
        )

    def test_visit_detail_carries_the_same_authoritative_stage(self):
        appointment, _ = self._ready_visit()

        _, payload = self._get(VISIT % appointment.id)
        data = payload["data"]

        self.assertEqual(data["visit"]["queue_stage"], READY_STAGE)
        self.assertEqual(data["visit"]["visit_type"], "routine")
        self.assertEqual(data["triage"]["status"], "completed")
        self.assertEqual(data["triage"]["chief_complaint"], "DD complaint")
        self.assertTrue(data["can_start_consultation"])

    def test_visit_detail_supplies_payer_category_under_normal_doctor_rights(self):
        """payer_type is a bare category and needs no ACL widening."""
        appointment, _ = self._register()

        _, payload = self._get(VISIT % appointment.id)

        self.assertEqual(payload["data"]["payer_type"], "self_pay")

    def test_cancelled_and_draft_visits_are_not_in_the_doctors_day(self):
        appointment, _ = self._register()
        appointment.sudo().action_cancel()

        _, payload = self._get(WORKLIST)

        self.assertNotIn(appointment.id, self._rows(payload))


@tagged("post_install", "-at_install", "doctor_desk")
class TestDoctorDeskStartConsultation(DoctorDeskCase):
    """The mutation is the model's; the route only carries the call."""

    def test_start_consultation_succeeds_through_the_model_method(self):
        appointment, _ = self._ready_visit()

        response, payload = self._post(START % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        # Re-serialized from the records as they now stand.
        self.assertEqual(payload["data"]["visit"]["state"], "in_consultation")
        self.assertEqual(payload["data"]["visit"]["queue_stage"], "in_consultation")
        self.assertEqual(payload["data"]["bucket"], "finished")

    def test_model_triage_gate_still_refuses_and_the_route_forwards_it(self):
        """Odoo's refusal is not swallowed, and no state moves."""
        appointment, encounter = self._register()
        self._pay(encounter)
        appointment.invalidate_recordset()

        response, payload = self._post(START % appointment.id)

        self.assertEqual(response.status_code, 422)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "invalid_workflow_state")
        self.assertIn("triage", payload["error"]["message"].lower())
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "confirmed")

    def test_model_financial_gate_still_refuses_and_the_route_forwards_it(self):
        appointment, _ = self._register()
        self._triage(appointment, complete=True)
        appointment.invalidate_recordset()

        response, payload = self._post(START % appointment.id)

        self.assertEqual(response.status_code, 422)
        self.assertFalse(payload["success"])
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "confirmed")
        # Refused on money, not on an emergency bypass having been granted.
        self.assertFalse(appointment.encounter_id.emergency_bypass)

    def test_another_doctors_visit_cannot_be_started(self):
        """Refused by SCOPE, before the model gate is even reached.

        404 rather than 403 for the reason given in
        TestDoctorDeskScope.test_visit_detail_respects_scope: the record rule
        hides the visit at the first probe, so the route cannot confirm it
        exists. The load-bearing assertion is the LAST one -- nothing moved.
        """
        appointment, _ = self._ready_visit(doctor=self.other_doctor)

        response, payload = self._post(START % appointment.id)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "visit_not_found")
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "confirmed")

    def test_nurse_cannot_start_a_consultation_through_this_route(self):
        appointment, _ = self._ready_visit()

        response, payload = self._post(
            START % appointment.id, user=self.nurse, password=self.nurse_password
        )

        self.assertEqual(response.status_code, 403)
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "confirmed")

    def test_model_layer_authorization_is_independent_of_this_api(self):
        """The gate holds even when the HTTP layer is bypassed entirely.

        This is the check that matters: the route is a caller of the gate, not
        the gate. A nurse calling the model directly is still refused.
        """
        appointment, _ = self._ready_visit()

        with self.assertRaises(AccessError):
            appointment.with_user(self.nurse).action_start_consultation()

        with self.assertRaises(AccessError):
            appointment.with_user(self.other_user).action_start_consultation()

    def test_model_layer_triage_gate_is_independent_of_this_api(self):
        appointment, encounter = self._register()
        self._pay(encounter)
        appointment.invalidate_recordset()

        with self.assertRaises(UserError):
            appointment.with_user(self.doctor_user).action_start_consultation()

    def test_starting_twice_is_refused_without_moving_state(self):
        appointment, _ = self._ready_visit()
        self._post(START % appointment.id)
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")

        response, _payload = self._post(START % appointment.id)

        # action_start_consultation filters on state == 'confirmed', so a second
        # call is a no-op rather than an error; what must NOT happen is a
        # regression of the state or a second encounter transition.
        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertIn(response.status_code, (200, 422))

    def test_missing_visit_is_a_404(self):
        response, payload = self._post(START % 99999999)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "visit_not_found")

    # ------------------------------------------------------------------
    # Transaction boundary
    # ------------------------------------------------------------------
    def test_response_failure_after_start_rolls_the_transition_back(self):
        """Regression: a started consultation must never survive a failed response.

        THE DEFECT IS STRUCTURAL, NOT A BAD PERMISSION. doctor_endpoint catches
        exceptions and RETURNS a Response, and Odoo's dispatcher reads a normal
        return as a served request -- so it COMMITS. Without the savepoint, a
        failure raised after action_start_consultation() had already moved the
        appointment, the encounter and the consultation charge would commit all
        three and still hand the doctor an error. The patient would be
        in_consultation on a screen that said the start failed, and the retry
        would then be refused because the visit is no longer 'confirmed'.

        The failure is injected by patching the SERIALIZER the controller calls
        -- production security is left exactly as it is. AccessError is the
        injected type because it is the realistic cause (the response reaching
        for a relation the doctor cannot read) AND because it is the type that
        proves the error-ordering fix: it must NOT come back as access_denied.
        """
        appointment, encounter = self._ready_visit()
        charge = appointment._consultation_charge()

        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        state_before = appointment.state
        encounter_state_before = encounter.state
        charge_state_before = charge.charge_state if charge else None
        self.assertEqual(state_before, "confirmed")

        with patch(
            VISIT_SERIALIZER_TARGET,
            side_effect=AccessError(
                "simulated post-write failure reading Patient Evaluation"
            ),
        ):
            response, payload = self._post(START % appointment.id)

        # The client is told the consultation did NOT start...
        self.assertEqual(response.status_code, 500)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "consultation_response_failed")
        # ...and is NOT told it was unauthorized, because it was authorized.
        # This is the assertion that pins the except-ordering in doctor_endpoint.
        self.assertNotEqual(payload["error"]["code"], "access_denied")

        # No internals leak to the client.
        serialized = json.dumps(payload)
        self.assertNotIn("Traceback", serialized)
        self.assertNotIn("Patient Evaluation", serialized)
        self.assertNotIn("simulated post-write failure", serialized)

        # ...and the workflow genuinely did not move, at every layer the model
        # method touches: the appointment, the encounter and the charge.
        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertEqual(appointment.state, state_before)
        self.assertNotEqual(appointment.state, "in_consultation")
        self.assertEqual(encounter.state, encounter_state_before)
        if charge:
            charge.invalidate_recordset()
            self.assertEqual(charge.charge_state, charge_state_before)
        # The derived stage still says the patient is waiting to be called.
        self.assertEqual(appointment.front_desk_stage, READY_STAGE)

    def test_a_normal_start_still_works_after_the_rollback(self):
        """The retry the client was told to make must actually succeed.

        A rollback that left the visit unusable would be a different bug wearing
        the same error message, so the recovery path is asserted rather than
        assumed.
        """
        appointment, encounter = self._ready_visit()

        with patch(
            VISIT_SERIALIZER_TARGET,
            side_effect=AccessError("simulated post-write failure"),
        ):
            failed, _ = self._post(START % appointment.id)
        self.assertEqual(failed.status_code, 500)

        appointment.invalidate_recordset()
        self.assertEqual(appointment.state, "confirmed")

        # Same visit, no patch: the ordinary path is untouched by the fix.
        response, payload = self._post(START % appointment.id)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["visit"]["state"], "in_consultation")
        self.assertEqual(payload["data"]["visit"]["queue_stage"], "in_consultation")
        self.assertEqual(payload["data"]["bucket"], "finished")

        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertEqual(appointment.state, "in_consultation")
        self.assertEqual(encounter.state, "active")


@tagged("post_install", "-at_install", "doctor_desk")
class TestDoctorDeskConfidentiality(DoctorDeskCase):
    """No money, no receipts, no sponsor allocations, no named payer."""

    def _walk(self, node, found_keys, found_strings):
        if isinstance(node, dict):
            for key, value in node.items():
                found_keys.add(key.lower())
                self._walk(value, found_keys, found_strings)
        elif isinstance(node, list):
            for item in node:
                self._walk(item, found_keys, found_strings)
        elif isinstance(node, str):
            found_strings.append(node)

    def _scan(self, data):
        keys, strings = set(), []
        self._walk(data, keys, strings)
        return keys, strings

    def _assert_clean(self, data, label):
        keys, strings = self._scan(data)

        for forbidden in FORBIDDEN_KEYS:
            offenders = [key for key in keys if forbidden in key]
            self.assertFalse(
                offenders,
                "%s exposes forbidden key(s) %s" % (label, offenders),
            )

        # The consultation price is 300.00. Its appearance anywhere -- including
        # inside a sentence -- is the leak this guards against.
        for text in strings:
            self.assertNotIn("300", text, "%s leaks an amount: %r" % (label, text))
            self.assertNotIn(
                self.service.name.lower(),
                text.lower(),
                "%s leaks a billing service name: %r" % (label, text),
            )

    def test_blocked_worklist_row_carries_no_financial_detail(self):
        """The row a patient owing money produces is the highest-risk one."""
        appointment, _ = self._register()
        self._triage(appointment, complete=True)
        appointment.invalidate_recordset()

        _, payload = self._get(WORKLIST)
        row = self._rows(payload)[appointment.id]

        self.assertEqual(row["queue_stage"], "awaiting_cashier")
        self.assertTrue(row["clearance"]["blocked"])
        self._assert_clean(row, "worklist row")

    def test_blocked_visit_detail_carries_no_financial_detail(self):
        appointment, _ = self._register()
        self._triage(appointment, complete=True)
        appointment.invalidate_recordset()

        _, payload = self._get(VISIT % appointment.id)

        self._assert_clean(payload["data"], "visit detail")

    def test_clearance_reason_comes_from_the_fixed_allowlist(self):
        """NOT from hospital.billing.engine, which embeds money and a sponsor.

        The engine's sentence for this exact visit is "Prepayment of 300.00 is
        required before service." The doctor must receive the allowlisted
        sentence instead.
        """
        appointment, _ = self._register()
        self._triage(appointment, complete=True)
        appointment.invalidate_recordset()

        _, payload = self._get(VISIT % appointment.id)
        clearance = payload["data"]["clearance"]

        self.assertEqual(set(clearance), EXPECTED_CLEARANCE_KEYS)
        self.assertTrue(clearance["blocked"])
        self.assertEqual(clearance["state"], "pending")
        self.assertEqual(clearance["reason"], DOCTOR_CLEARANCE_REASONS["pending"])
        # The engine's own wording really does carry the figure, which is why
        # it is not forwarded.
        self.assertIn("300", appointment.billing_clearance_message or "")
        self.assertNotIn("300", clearance["reason"])

    def test_cleared_visit_reports_a_verdict_and_no_reason(self):
        appointment, _ = self._ready_visit()

        _, payload = self._get(VISIT % appointment.id)
        clearance = payload["data"]["clearance"]

        self.assertFalse(clearance["blocked"])
        self.assertIsNone(clearance["reason"])
        self._assert_clean(payload["data"], "cleared visit detail")

    def test_session_payload_carries_nothing_financial(self):
        _, payload = self._get(SESSION)

        self._assert_clean(payload["data"], "session")

"""Phase A: the consolidated front-desk worklist and selected-visit endpoints."""
import json
import uuid

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import HttpCase, tagged

G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_MANAGER = "hospital_management.group_hospital_manager"
G_PHARMACIST = "hospital_management.group_hospital_pharmacist"

WORKLIST = "/yoya-emr/api/v1/front-desk/worklist"
VISIT = "/yoya-emr/api/v1/front-desk/visits/%s"

EXPECTED_ROW_KEYS = {
    "appointment_id", "appointment_code", "encounter_id", "patient_id", "mrn",
    "patient_name", "age", "gender", "arrived_at", "department", "doctor",
    "visit_type", "appointment_state", "encounter_state", "evaluation_id",
    "triage_state", "triage_priority", "clearance", "financial_clearance_state",
    "operational_funding_state", "emergency", "queue_stage", "urgent",
    "permitted_actions",
}


@tagged("post_install", "-at_install", "front_desk_worklist")
class TestFrontDeskWorklist(HttpCase):
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
            {"name": "FD Worklist Dept %s" % one, "code": "WLA%s" % one.upper()}
        )
        cls.other_department = cls.env["hospital.department"].sudo().create(
            {"name": "FD Worklist Other %s" % two, "code": "WLB%s" % two.upper()}
        )
        cls.fd_password = "fd-worklist-pw-1"
        cls.front_desk = cls._make_user("wl_fd", cls.fd_password, [G_FRONT_DESK_NURSE])
        cls.cashier_password = "wl-cashier-pw-1"
        cls.cashier = cls._make_user("wl_cashier", cls.cashier_password, [G_CASHIER])
        cls.pharmacist_password = "wl-pharm-pw-1"
        cls.pharmacist = cls._make_user(
            "wl_pharmacist", cls.pharmacist_password, [G_PHARMACIST]
        )
        cls.manager = cls._make_user("wl_manager", "wl-manager-pw-1", [G_MANAGER])
        cls.doctor_user = cls._make_user("wl_doc", "wl-doc-pw-1", [G_DOCTOR])
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {
                "name": "FD Worklist Doctor",
                "user_id": cls.doctor_user.id,
                "department_id": cls.department.id,
            }
        )
        cls.other_doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "FD Worklist Other Doctor", "department_id": cls.other_department.id}
        )

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
    def _register(self, department=None, doctor=None, visit_type="routine", name=None):
        result = self.env["hospital.reception.workflow"].with_user(
            self.front_desk
        ).create_visit(
            patient_values={"name": name or "WL Patient %s" % uuid.uuid4().hex[:6]},
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
                "chief_complaint": "WL complaint",
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

    def _get(self, url, user=None, password=None, **params):
        self.authenticate(
            (user or self.front_desk).login, password or self.fd_password
        )
        query = "&".join("%s=%s" % (k, v) for k, v in params.items() if v is not None)
        response = self.url_open("%s%s" % (url, "?" + query if query else ""))
        return response, json.loads(response.text)

    def _rows_by_appointment(self, payload):
        return {row["appointment_id"]: row for row in payload["data"]["rows"]}

    # ==================================================================
    # D. Worklist
    # ==================================================================
    def test_row_shape_and_derived_stage(self):
        appointment, _encounter = self._register()

        response, payload = self._get(WORKLIST)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        row = self._rows_by_appointment(payload)[appointment.id]
        self.assertEqual(set(row), EXPECTED_ROW_KEYS)
        self.assertEqual(row["queue_stage"], "intake")
        self.assertEqual(row["mrn"], appointment.patient_id.identification_code)
        self.assertEqual(row["patient_name"], appointment.patient_id.name)
        self.assertEqual(row["appointment_code"], appointment.appointment_code)
        self.assertEqual(row["department"]["id"], self.department.id)
        self.assertEqual(row["doctor"]["id"], self.doctor.id)
        self.assertTrue(row["arrived_at"])
        self.assertIsNotNone(row["encounter_id"])
        self.assertFalse(row["clearance"]["ok"])
        self.assertGreater(row["clearance"]["outstanding"], 0.0)
        # No history is smuggled into a worklist row.
        for absent in ("diagnoses", "prescriptions", "lab_requests", "radiology"):
            self.assertNotIn(absent, row)

    def test_stage_progresses_intake_triage_cashier_ready(self):
        appointment, encounter = self._register()

        _, payload = self._get(WORKLIST)
        self.assertEqual(
            self._rows_by_appointment(payload)[appointment.id]["queue_stage"], "intake"
        )

        evaluation = self._triage(appointment)
        _, payload = self._get(WORKLIST)
        self.assertEqual(
            self._rows_by_appointment(payload)[appointment.id]["queue_stage"], "triage"
        )

        evaluation.with_user(self.front_desk).action_done()
        _, payload = self._get(WORKLIST)
        row = self._rows_by_appointment(payload)[appointment.id]
        self.assertEqual(row["queue_stage"], "awaiting_cashier")
        self.assertEqual(row["triage_state"], "done")
        self.assertTrue(row["permitted_actions"]["send_to_cashier"])

        self._pay(encounter)
        _, payload = self._get(WORKLIST)
        row = self._rows_by_appointment(payload)[appointment.id]
        self.assertEqual(row["queue_stage"], "ready_doctor")
        self.assertTrue(row["clearance"]["ok"])
        self.assertEqual(row["operational_funding_state"], "funded")

    def test_emergency_type_does_not_skip_cashier_without_bypass(self):
        appointment, encounter = self._register(visit_type="emergency")
        self._triage(appointment, complete=True)

        _, payload = self._get(WORKLIST)
        row = self._rows_by_appointment(payload)[appointment.id]
        self.assertEqual(row["queue_stage"], "awaiting_cashier")
        self.assertTrue(row["emergency"])
        self.assertFalse(row["clearance"]["ok"])
        with self.assertRaises(UserError):
            appointment.with_user(self.doctor_user).action_start_consultation()

    def test_authorized_emergency_bypass_is_genuinely_ready_for_doctor(self):
        appointment, encounter = self._register(visit_type="emergency")
        self._triage(appointment, complete=True)
        encounter.with_user(self.manager).write(
            {
                "emergency_bypass": True,
                "emergency_bypass_reason": "Authorized emergency care before payment.",
            }
        )

        _, payload = self._get(WORKLIST)
        row = self._rows_by_appointment(payload)[appointment.id]
        self.assertEqual(row["queue_stage"], "ready_doctor")
        self.assertTrue(row["clearance"]["ok"])
        appointment.with_user(self.doctor_user).action_start_consultation()
        self.assertEqual(appointment.sudo().state, "in_consultation")

    def test_counts_match_the_rows_they_describe(self):
        intake, _ = self._register()
        in_triage, _ = self._register()
        self._triage(in_triage)
        waiting, _ = self._register()
        self._triage(waiting, complete=True)
        ready, ready_encounter = self._register()
        self._triage(ready, complete=True)
        self._pay(ready_encounter)

        _, payload = self._get(WORKLIST, stage=",".join(
            ["new", "intake", "triage", "awaiting_cashier", "ready_doctor",
             "in_consultation", "completed", "cancelled"]
        ))
        data = payload["data"]
        rows = data["rows"]
        counts = data["counts"]

        # Recompute from the rows themselves: the counters must be a function of
        # the same population, not an independent query.
        for name, stages in (
            ("intake", ("new", "intake")),
            ("triage", ("triage",)),
            ("awaiting_cashier", ("awaiting_cashier",)),
            ("ready_doctor", ("ready_doctor",)),
            ("in_consultation", ("in_consultation",)),
        ):
            expected = sum(1 for row in rows if row["queue_stage"] in stages)
            self.assertEqual(counts[name], expected, "counter %s disagrees" % name)
        self.assertEqual(counts["today"], len(rows))
        self.assertEqual(
            counts["urgent"], sum(1 for row in rows if row["urgent"])
        )

        stages_seen = {row["appointment_id"]: row["queue_stage"] for row in rows}
        self.assertEqual(stages_seen[intake.id], "intake")
        self.assertEqual(stages_seen[in_triage.id], "triage")
        self.assertEqual(stages_seen[waiting.id], "awaiting_cashier")
        self.assertEqual(stages_seen[ready.id], "ready_doctor")

    def test_no_duplicate_rows_and_repeat_visits_share_one_mrn(self):
        """The real duplication risk is one patient with two visits in a day.

        hospital.patient.evaluation carries a unique constraint on
        appointment_id, so a visit can never have two evaluations and
        _latest_evaluation() can never multiply a row. What CAN happen is the
        same patient returning: that must produce two distinct rows that agree
        on patient identity, not one row or two MRNs.
        """
        first, _ = self._register()
        patient = first.patient_id

        # UPDATED: one active episode per patient. A returning patient is now a
        # SEQUENTIAL attendance -- the first visit must be finished before the
        # second is registered, which is what the hospital actually does and
        # what hospital.encounter now enforces. The claim under test is
        # unchanged: two visits, two rows, ONE medical record number.
        first.encounter_id.sudo().write({"state": "closed"})

        second_result = self.env["hospital.reception.workflow"].with_user(
            self.front_desk
        ).create_visit(
            patient=patient.with_user(self.front_desk),
            visit_type="follow_up",
            department=self.department,
            doctor=self.doctor,
        )
        second = second_result["appointment"].sudo()
        self._triage(first)

        _, payload = self._get(WORKLIST)
        rows = payload["data"]["rows"]

        appointment_ids = [row["appointment_id"] for row in rows]
        self.assertEqual(
            len(appointment_ids), len(set(appointment_ids)), "duplicate visit rows"
        )

        mine = [row for row in rows if row["patient_id"] == patient.id]
        self.assertEqual(len(mine), 2)
        self.assertEqual({row["appointment_id"] for row in mine}, {first.id, second.id})
        self.assertEqual(
            {row["mrn"] for row in mine},
            {patient.identification_code},
            "one patient must map to exactly one MRN",
        )

        # And globally: never two MRNs for one patient id.
        mapping = {}
        for row in rows:
            mapping.setdefault(row["patient_id"], set()).add(row["mrn"])
        for patient_id, mrns in mapping.items():
            self.assertEqual(len(mrns), 1, "patient %s has multiple MRNs" % patient_id)

    def test_stage_filter(self):
        intake, _ = self._register()
        triaged, _ = self._register()
        self._triage(triaged, complete=True)

        _, payload = self._get(WORKLIST, stage="awaiting_cashier")
        ids = set(self._rows_by_appointment(payload))
        self.assertIn(triaged.id, ids)
        self.assertNotIn(intake.id, ids)
        for row in payload["data"]["rows"]:
            self.assertEqual(row["queue_stage"], "awaiting_cashier")

    def test_department_and_doctor_filters(self):
        mine, _ = self._register()
        other, _ = self._register(
            department=self.other_department, doctor=self.other_doctor
        )

        _, payload = self._get(WORKLIST, department_id=self.department.id)
        ids = set(self._rows_by_appointment(payload))
        self.assertIn(mine.id, ids)
        self.assertNotIn(other.id, ids)

        _, payload = self._get(WORKLIST, doctor_id=self.other_doctor.id)
        ids = set(self._rows_by_appointment(payload))
        self.assertIn(other.id, ids)
        self.assertNotIn(mine.id, ids)

    def test_patient_search_filter_by_name_and_mrn(self):
        unique = uuid.uuid4().hex[:8]
        target, _ = self._register(name="Zzsearch %s" % unique)
        other, _ = self._register()

        _, payload = self._get(WORKLIST, q="Zzsearch")
        ids = set(self._rows_by_appointment(payload))
        self.assertIn(target.id, ids)
        self.assertNotIn(other.id, ids)

        _, payload = self._get(WORKLIST, q=target.patient_id.identification_code)
        ids = set(self._rows_by_appointment(payload))
        self.assertIn(target.id, ids)
        self.assertNotIn(other.id, ids)

    def test_date_filter_excludes_other_days(self):
        appointment, _ = self._register()
        _, payload = self._get(WORKLIST, date="2001-01-01")
        self.assertNotIn(appointment.id, set(self._rows_by_appointment(payload)))
        self.assertEqual(payload["data"]["filters"]["date"], "2001-01-01")

    def test_unknown_stage_is_rejected(self):
        response, payload = self._get(WORKLIST, stage="teleportation")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(payload["error"]["code"], "invalid_parameter")

    def test_urgent_is_a_flag_not_a_stage(self):
        appointment, _ = self._register()
        self._triage(appointment, complete=True, priority="urgent")

        _, payload = self._get(WORKLIST)
        row = self._rows_by_appointment(payload)[appointment.id]
        self.assertTrue(row["urgent"])
        self.assertEqual(row["triage_priority"], "urgent")
        # Urgency must not displace the workflow position.
        self.assertEqual(row["queue_stage"], "awaiting_cashier")

    def test_permitted_actions_reflect_authoritative_permissions(self):
        appointment, encounter = self._register()

        _, payload = self._get(WORKLIST)
        row = self._rows_by_appointment(payload)[appointment.id]
        self.assertTrue(row["permitted_actions"]["record_triage"])
        self.assertFalse(row["permitted_actions"]["record_payment"])
        self.assertFalse(row["permitted_actions"]["start_consultation"])
        self.assertFalse(payload["data"]["capabilities"]["record_payment"])
        self.assertTrue(payload["data"]["capabilities"]["triage"])
        self.assertTrue(payload["data"]["capabilities"]["intake"])
        self.assertFalse(payload["data"]["capabilities"]["start_consultation_role"])

        # Cleared and triaged: still not startable BY THE FRONT DESK.
        self._triage(appointment, complete=True)
        self._pay(encounter)
        _, payload = self._get(WORKLIST)
        row = self._rows_by_appointment(payload)[appointment.id]
        self.assertEqual(row["queue_stage"], "ready_doctor")
        self.assertFalse(row["permitted_actions"]["start_consultation"])

    def test_doctor_is_not_a_front_desk_role(self):
        """The doctor has their own queue; this screen is not theirs.

        FRONT_DESK_GROUPS deliberately omits Doctor. Doctors work
        /clinical/evaluation-queue, and action_start_consultation remains the
        authoritative transition either way (proved in
        yoya_reception_bridge.tests.test_front_desk_triage_workflow).
        """
        appointment, encounter = self._register()
        self._triage(appointment, complete=True)
        self._pay(encounter)

        response, payload = self._get(
            WORKLIST, user=self.doctor_user, password="wl-doc-pw-1"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "access_denied")

    # ==================================================================
    # E. Security
    # ==================================================================
    def test_cashier_cannot_open_the_front_desk_worklist(self):
        self._register()
        response, payload = self._get(
            WORKLIST, user=self.cashier, password=self.cashier_password
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "access_denied")

    def test_unrelated_role_is_rejected(self):
        self._register()
        response, payload = self._get(
            WORKLIST, user=self.pharmacist, password=self.pharmacist_password
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"]["code"], "access_denied")

    def test_unauthenticated_request_is_rejected(self):
        """No anonymous access. The route is auth="user", so Odoo redirects to
        the login page rather than answering -- following that redirect is what
        yields a 200, so the redirect itself is the assertion."""
        self._register()
        # HttpCase keeps the session from any earlier authenticate() in this
        # class, so the anonymous case has to log out explicitly.
        self.authenticate(None, None)

        response = self.url_open(WORKLIST, allow_redirects=False)

        self.assertIn(response.status_code, (301, 302, 303))
        self.assertIn("/web/login", response.headers.get("Location", ""))
        # And no worklist data is served anonymously even after the redirect.
        followed = self.url_open(WORKLIST)
        self.assertNotIn("appointment_code", followed.text)

    # ==================================================================
    # Selected visit
    # ==================================================================
    def test_visit_detail_shape_and_triage_draft(self):
        appointment, encounter = self._register()
        self._triage(appointment)

        response, payload = self._get(VISIT % appointment.id)

        self.assertEqual(response.status_code, 200)
        data = payload["data"]
        # payer_change joined this set in Phase 2B. It is detail-only: the
        # freeze check runs a search per encounter, so it is deliberately absent
        # from the worklist rows (EXPECTED_ROW_KEYS above is unchanged).
        self.assertEqual(set(data), {
            "row", "patient", "visit", "encounter", "evaluation", "clearance",
            "permitted_actions", "payer_change",
        })
        self.assertEqual(data["row"]["queue_stage"], "triage")
        self.assertEqual(data["patient"]["mrn"], appointment.patient_id.identification_code)
        self.assertEqual(data["visit"]["appointment_id"], appointment.id)
        self.assertEqual(data["encounter"]["id"], encounter.id)
        self.assertEqual(data["evaluation"]["state"], "draft")
        self.assertEqual(data["evaluation"]["chief_complaint"], "WL complaint")
        self.assertIn("temperature", data["evaluation"]["vitals"])
        self.assertFalse(data["clearance"]["ok"])
        # Histories are a later phase and must not be eagerly loaded.
        for absent in ("diagnoses", "laboratory", "radiology", "medications"):
            self.assertNotIn(absent, data)

    def test_visit_detail_row_agrees_with_the_worklist_row(self):
        appointment, _ = self._register()
        self._triage(appointment, complete=True)

        _, list_payload = self._get(WORKLIST)
        _, detail_payload = self._get(VISIT % appointment.id)

        self.assertEqual(
            self._rows_by_appointment(list_payload)[appointment.id],
            detail_payload["data"]["row"],
        )

    def test_visit_detail_unknown_id_is_404(self):
        response, payload = self._get(VISIT % 99999999)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(payload["error"]["code"], "visit_not_found")

    def test_visit_detail_rejects_cashier(self):
        appointment, _ = self._register()
        response, payload = self._get(
            VISIT % appointment.id,
            user=self.cashier,
            password=self.cashier_password,
        )
        self.assertEqual(response.status_code, 403)

    # ==================================================================
    # Drift guard
    # ==================================================================
    def test_api_reception_groups_mirror_the_workflow_groups(self):
        from odoo.addons.yoya_emr_api.services import reception_scope
        from odoo.addons.yoya_reception_bridge.models import reception_workflow

        self.assertEqual(
            set(reception_scope.RECEPTION_GROUPS),
            set(reception_workflow.REGISTRATION_GROUPS),
            "The API's RECEPTION_GROUPS has drifted from the authoritative "
            "REGISTRATION_GROUPS enforced in hospital.reception.workflow.",
        )

    def test_stage_vocabulary_comes_from_the_model(self):
        from odoo.addons.yoya_emr_api.services import front_desk_serializers
        from odoo.addons.yoya_reception_bridge.models import hospital_appointment

        self.assertEqual(
            front_desk_serializers.STAGE_KEYS,
            tuple(key for key, _ in hospital_appointment.FRONT_DESK_STAGES),
        )
        # Every canonical stage must have a legacy counterpart, or
        # clinical_queue_stage would raise a KeyError at runtime.
        for key in front_desk_serializers.STAGE_KEYS:
            self.assertIn(key, hospital_appointment.LEGACY_STAGE_BY_FRONT_DESK)

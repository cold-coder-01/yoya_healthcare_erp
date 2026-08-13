"""Phase A: triage happens BEFORE the cashier, and the doctor gate still holds.

The workflow this proves is the one the hospital actually runs:

    arrive -> front desk NURSE -> registration -> triage -> triage complete
           -> cashier -> financial clearance -> ready for doctor -> consultation

The previous implementation required payment before triage. These tests would
have failed against it, which is the point.
"""
import uuid

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_NURSE = "hospital_management.group_hospital_nurse"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_MANAGER = "hospital_management.group_hospital_manager"


@tagged("post_install", "-at_install", "front_desk_triage")
class TestFrontDeskTriageWorkflow(TransactionCase):
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
                "default_price": 300.0,
                "fixed_fee": True,
                "prepayment_required": True,
                "coverage_auth_required": False,
                "active": True,
                "is_default_consultation": True,
            }
        )
        suffix = uuid.uuid4().hex[:6]
        cls.department = cls.env["hospital.department"].sudo().create(
            {
                "name": "Front Desk Test Department %s" % suffix,
                "code": "FDT%s" % suffix.upper(),
            }
        )

        cls.front_desk = cls._make_user("fd_nurse", [G_FRONT_DESK_NURSE])
        # The SECOND front desk nurse: the shift-handover case. Deliberately
        # given no permitted departments, because that is the real
        # configuration -- the entrance is not department-scoped.
        cls.front_desk_b = cls._make_user("fd_nurse_b", [G_FRONT_DESK_NURSE])
        cls.ward_nurse = cls._make_user("fd_ward_nurse", [G_NURSE])
        cls.cashier = cls._make_user("fd_cashier", [G_CASHIER])
        cls.receptionist = cls._make_user("fd_receptionist", [G_RECEPTIONIST])
        cls.accountant = cls._make_user("fd_accountant", [G_ACCOUNTANT])
        cls.manager = cls._make_user("fd_manager", [G_MANAGER])

        cls.doctor_user = cls._make_user("fd_doctor", [G_DOCTOR])
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {
                "name": "Front Desk Test Doctor",
                "user_id": cls.doctor_user.id,
                "department_id": cls.department.id,
            }
        )
        # The department-scoped nurse record rule reaches an appointment through
        # this field; the front desk group's own rule is unrestricted, so this
        # only matters for the plain ward nurse used in the scoping test.
        cls.ward_nurse.sudo().write(
            {"yoya_permitted_department_ids": [(6, 0, cls.department.ids)]}
        )

    @classmethod
    def _make_user(cls, login, group_xmlids):
        return (
            cls.env["res.users"]
            .sudo()
            .create(
                {
                    "name": login,
                    "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
                    "password": "%s-pw-1234" % login,
                    "company_id": cls.env.company.id,
                    "company_ids": [(6, 0, cls.env.company.ids)],
                    "groups_id": [
                        (
                            6,
                            0,
                            [cls.env.ref("base.group_user").id]
                            + [cls.env.ref(x).id for x in group_xmlids],
                        )
                    ],
                }
            )
        )

    # ------------------------------------------------------------------
    # Helpers -- every one goes through an authoritative domain method
    # ------------------------------------------------------------------
    def _register_visit(self, user=None, visit_type="routine"):
        workflow = self.env["hospital.reception.workflow"].with_user(
            user or self.front_desk
        )
        result = workflow.create_visit(
            patient_values={"name": "FD Patient %s" % uuid.uuid4().hex[:6]},
            visit_type=visit_type,
            department=self.department,
            doctor=self.doctor,
        )
        return result["appointment"].sudo(), result["encounter"].sudo()

    def _stage(self, appointment):
        """Read the stage the way a fresh HTTP request would.

        encounter.reception_clearance_ok is a non-stored compute with no
        @api.depends -- deliberately, so it is always recomputed from the engine
        rather than cached wrongly. Within ONE environment it is still cached
        after first read, so a test that pays and then re-reads in the same env
        must drop the cache. Every real request gets a new env, which is what
        invalidate_all() reproduces here.
        """
        self.env.invalidate_all()
        return appointment.sudo().front_desk_stage

    def _save_triage(self, appointment, user=None):
        """Create + claim the draft evaluation, as the nursing UI does."""
        evaluation = (
            self.env["hospital.patient.evaluation"]
            .with_user(user or self.front_desk)
            .create(
                {
                    "patient_id": appointment.patient_id.id,
                    "appointment_id": appointment.id,
                    "chief_complaint": "Headache for two days",
                    "temperature": 37.4,
                    "heart_rate": 82.0,
                    "triage_priority": "routine",
                }
            )
        )
        evaluation.action_start_evaluation()
        return evaluation

    def _complete_triage(self, evaluation, user=None):
        evaluation.with_user(user or self.front_desk).action_done()
        return evaluation

    def _pay_full(self, encounter, amount=None):
        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        due = amount if amount is not None else account.amount_due_for_clearance
        return account.with_user(self.cashier).record_operational_payment(
            due, "cash", intake_token=uuid.uuid4().hex
        )

    # ==================================================================
    # A. Front desk / nursing workflow
    # ==================================================================
    def test_front_desk_nurse_can_register_a_visit(self):
        appointment, encounter = self._register_visit()

        self.assertTrue(appointment.reception_workflow_managed)
        self.assertEqual(appointment.state, "confirmed")
        self.assertEqual(appointment.registered_by_id, self.front_desk)
        self.assertTrue(encounter)
        self.assertEqual(self._stage(appointment), "intake")

    def test_unpaid_visit_can_be_sent_to_triage(self):
        """The old implementation raised UserError here. That was the bug."""
        appointment, encounter = self._register_visit()
        encounter.invalidate_recordset()
        self.assertFalse(encounter.reception_clearance_ok)
        self.assertGreater(encounter.reception_outstanding_amount, 0.0)

        result = self.env["hospital.reception.workflow"].with_user(
            self.front_desk
        ).send_to_triage(appointment.with_user(self.front_desk))

        self.assertEqual(result["front_desk_stage"], "intake")
        # Clearance is still reported -- it just no longer decides.
        self.assertFalse(result["clearance"]["cleared"])
        self.assertGreater(result["clearance"]["outstanding"], 0.0)

    def test_unpaid_patient_can_save_triage(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)

        self.assertEqual(evaluation.sudo().state, "draft")
        self.assertTrue(evaluation.sudo().started_at)
        self.assertEqual(self._stage(appointment), "triage")
        encounter.invalidate_recordset()
        self.assertFalse(encounter.reception_clearance_ok)

    def test_unpaid_patient_can_complete_triage_and_waits_for_cashier(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)

        self.assertEqual(evaluation.sudo().state, "done")
        self.assertTrue(evaluation.sudo().completed_at)
        self.assertEqual(self._stage(appointment), "awaiting_cashier")

    def test_completed_triage_plus_payment_becomes_ready_for_doctor(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        self.assertEqual(self._stage(appointment), "awaiting_cashier")

        self._pay_full(encounter)

        self.assertEqual(self._stage(appointment), "ready_doctor")

    def test_payment_before_triage_does_not_skip_triage(self):
        """Paying early is allowed; it must not fabricate doctor-readiness."""
        appointment, encounter = self._register_visit()
        self._pay_full(encounter)

        self.assertEqual(self._stage(appointment), "intake")

        evaluation = self._save_triage(appointment)
        self.assertEqual(self._stage(appointment), "triage")
        self._complete_triage(evaluation)
        self.assertEqual(self._stage(appointment), "ready_doctor")

    def test_emergency_visit_type_alone_does_not_claim_doctor_readiness(self):
        appointment, encounter = self._register_visit(visit_type="emergency")
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)

        encounter.invalidate_recordset()
        self.assertFalse(encounter.reception_clearance_ok)
        self.assertFalse(encounter.emergency_bypass)
        self.assertEqual(self._stage(appointment), "awaiting_cashier")
        with self.assertRaises(UserError):
            appointment.with_user(self.doctor_user).action_start_consultation()

    def test_authorized_emergency_bypass_can_be_genuinely_ready_for_doctor(self):
        appointment, encounter = self._register_visit(visit_type="emergency")
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        encounter.with_user(self.manager).write(
            {
                "emergency_bypass": True,
                "emergency_bypass_reason": "Manager-authorized emergency care before payment.",
            }
        )

        encounter.invalidate_recordset()
        self.assertTrue(encounter.emergency_bypass)
        self.assertTrue(encounter.reception_clearance_ok)
        self.assertEqual(self._stage(appointment), "ready_doctor")
        appointment.with_user(self.doctor_user).action_start_consultation()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.sudo().state, "in_consultation")

    def test_legacy_queue_stage_agrees_with_the_canonical_stage(self):
        """clinical_queue_stage is a mapping, not a second derivation."""
        appointment, encounter = self._register_visit()
        expected = {
            "intake": "awaiting_triage",
            "triage": "in_triage",
            "awaiting_cashier": "awaiting_payment",
            "ready_doctor": "awaiting_doctor",
        }

        def check():
            appointment.invalidate_recordset()
            record = appointment.sudo()
            self.assertEqual(
                record.clinical_queue_stage, expected[record.front_desk_stage]
            )

        check()
        evaluation = self._save_triage(appointment)
        check()
        self._complete_triage(evaluation)
        check()
        self._pay_full(encounter)
        check()

    # ==================================================================
    # A2. Shift handover -- rule_patient_evaluation_front_desk_nurse
    #
    # Before that rule existed the desk fell under the department-scoped
    # Hospital Nurse rule, whose "unassigned in my department" branch cannot
    # match once assigned_nurse_id is stamped. Every test here failed.
    # ==================================================================
    def test_second_front_desk_nurse_can_read_first_nurses_evaluation(self):
        appointment, _encounter = self._register_visit()
        evaluation = self._save_triage(appointment, user=self.front_desk)
        self.assertEqual(evaluation.sudo().assigned_nurse_id, self.front_desk)

        self.env.invalidate_all()
        as_b = evaluation.with_user(self.front_desk_b)
        self.assertEqual(as_b.chief_complaint, "Headache for two days")
        self.assertEqual(
            self.env["hospital.patient.evaluation"]
            .with_user(self.front_desk_b)
            .search_count([("id", "=", evaluation.id)]),
            1,
        )

    def test_second_front_desk_nurse_sees_the_same_front_desk_stage(self):
        """The silent failure mode: a filtered x2many, not an AccessError.

        front_desk_stage derives the stage from evaluation_ids, and Odoo
        FILTERS unreadable x2many members rather than raising. Without the rule
        nurse B read 'intake' for a patient nurse A had already triaged, with
        matching counters, and nothing anywhere reported an error.
        """
        appointment, _encounter = self._register_visit()
        evaluation = self._save_triage(appointment, user=self.front_desk)

        self.env.invalidate_all()
        self.assertEqual(
            appointment.with_user(self.front_desk_b).front_desk_stage, "triage"
        )
        self.assertEqual(
            appointment.with_user(self.front_desk_b).clinical_queue_stage, "in_triage"
        )

        self._complete_triage(evaluation, user=self.front_desk)
        self.env.invalidate_all()
        self.assertEqual(
            appointment.with_user(self.front_desk_b).front_desk_stage,
            "awaiting_cashier",
        )

    def test_second_front_desk_nurse_can_continue_a_draft_triage(self):
        appointment, _encounter = self._register_visit()
        evaluation = self._save_triage(appointment, user=self.front_desk)

        self.env.invalidate_all()
        evaluation.with_user(self.front_desk_b).write(
            {"temperature": 38.1, "triage_notes": "Continued by the incoming shift."}
        )

        evaluation.invalidate_recordset()
        self.assertEqual(evaluation.sudo().temperature, 38.1)
        # Continuing does NOT steal ownership: action_start_evaluation only
        # claims an unassigned evaluation.
        self.assertEqual(evaluation.sudo().assigned_nurse_id, self.front_desk)

        self._complete_triage(evaluation, user=self.front_desk_b)
        self.assertEqual(evaluation.sudo().state, "done")
        self.assertEqual(self._stage(appointment), "awaiting_cashier")

    def test_front_desk_nurse_cannot_unlink_an_evaluation(self):
        appointment, _encounter = self._register_visit()
        evaluation = self._save_triage(appointment)

        with self.assertRaises(AccessError):
            evaluation.with_user(self.front_desk).unlink()
        self.assertTrue(evaluation.sudo().exists())

    def test_front_desk_rule_does_not_reach_appointmentless_evaluations(self):
        """The rule is scoped to appointment-linked arrivals, on purpose.

        A ward evaluation with no appointment is a purely clinical artefact and
        belongs to the future Nurses workspace, not to the entrance.
        """
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "FD Ward Only %s" % uuid.uuid4().hex[:6]}
        )
        ward_evaluation = self.env["hospital.patient.evaluation"].sudo().create(
            {
                "patient_id": patient.id,
                "chief_complaint": "Ward round, no appointment",
                "assigned_nurse_id": self.ward_nurse.id,
            }
        )

        self.assertEqual(
            self.env["hospital.patient.evaluation"]
            .with_user(self.front_desk)
            .search_count([("id", "=", ward_evaluation.id)]),
            0,
        )

    def test_plain_nurse_scope_is_unchanged(self):
        """The plain Hospital Nurse rule must not have moved.

        ward_nurse HAS this department in yoya_permitted_department_ids, so if
        the new front-desk rule had been attached to Hospital Nurse instead,
        this evaluation would become visible. It must not: the nurse rule's
        second branch requires assigned_nurse_id to be empty, and nurse A owns
        this record.
        """
        appointment, _encounter = self._register_visit()
        evaluation = self._save_triage(appointment, user=self.front_desk)

        self.env.invalidate_all()
        Evaluation = self.env["hospital.patient.evaluation"].with_user(self.ward_nurse)
        self.assertEqual(Evaluation.search_count([("id", "=", evaluation.id)]), 0)
        with self.assertRaises(AccessError):
            evaluation.with_user(self.ward_nurse).write({"temperature": 39.0})

    def test_plain_nurse_still_reaches_its_own_and_unassigned_department_work(self):
        """The other half of "unchanged": the nurse rule still GRANTS."""
        appointment, _encounter = self._register_visit()
        unassigned = self.env["hospital.patient.evaluation"].sudo().create(
            {
                "patient_id": appointment.patient_id.id,
                "appointment_id": appointment.id,
                "assigned_nurse_id": False,
            }
        )

        self.env.invalidate_all()
        Evaluation = self.env["hospital.patient.evaluation"].with_user(self.ward_nurse)
        self.assertEqual(Evaluation.search_count([("id", "=", unassigned.id)]), 1)

    # ==================================================================
    # A3. Doctor assignment
    # ==================================================================
    def _assign(self, appointment, doctor, user=None):
        return self.env["hospital.reception.workflow"].with_user(
            user or self.front_desk
        ).assign_doctor(
            appointment.with_user(user or self.front_desk), doctor
        )

    def test_assign_doctor_synchronizes_appointment_and_encounter(self):
        """B2.1 opens the encounter before a doctor exists.

        hospital.encounter only copies the doctor in an @api.onchange, which
        never fires on an ORM write, so writing appointment.doctor_id alone
        leaves primary_doctor_id empty forever.
        """
        workflow = self.env["hospital.reception.workflow"].with_user(self.front_desk)
        result = workflow.create_visit(
            patient_values={"name": "FD NoDoc %s" % uuid.uuid4().hex[:6]},
            department=self.department,
        )
        appointment = result["appointment"].sudo()
        encounter = result["encounter"].sudo()
        self.assertFalse(appointment.doctor_id)
        self.assertFalse(encounter.primary_doctor_id)

        self._assign(appointment, self.doctor)

        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertEqual(appointment.doctor_id, self.doctor)
        self.assertEqual(encounter.primary_doctor_id, self.doctor)

    def test_assign_doctor_rejects_a_doctor_from_another_department(self):
        appointment, encounter = self._register_visit()
        other_department = self.env["hospital.department"].sudo().create(
            {
                "name": "FD Other Dept %s" % uuid.uuid4().hex[:6],
                "code": "FDO%s" % uuid.uuid4().hex[:5].upper(),
            }
        )
        other_doctor = self.env["hospital.doctor"].sudo().create(
            {"name": "FD Other Doctor", "department_id": other_department.id}
        )

        with self.assertRaises(ValidationError):
            self._assign(appointment, other_doctor)

        appointment.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertEqual(appointment.doctor_id, self.doctor)
        self.assertNotEqual(encounter.primary_doctor_id, other_doctor)

    def test_assign_doctor_syncs_a_draft_evaluation_but_never_a_completed_one(self):
        """THE synchronization rule, both halves of it."""
        appointment, _encounter = self._register_visit()
        evaluation = self._save_triage(appointment)

        second_doctor = self.env["hospital.doctor"].sudo().create(
            {
                "name": "FD Second Doctor",
                "department_id": self.department.id,
            }
        )
        self._assign(appointment, second_doctor)
        evaluation.invalidate_recordset()
        self.assertEqual(evaluation.sudo().physician_id, second_doctor)

        # Completed: the triage document records who was responsible AT
        # COMPLETION and is frozen by LOCKED_CLINICAL_FIELDS. Reassigning the
        # visit afterwards must still succeed, and must leave it alone.
        self._complete_triage(evaluation)
        self._assign(appointment, self.doctor)

        appointment.invalidate_recordset()
        evaluation.invalidate_recordset()
        self.assertEqual(appointment.doctor_id, self.doctor)
        self.assertEqual(evaluation.sudo().physician_id, second_doctor)

    def test_assign_doctor_does_not_change_the_department(self):
        appointment, _encounter = self._register_visit()
        doctor_without_department = self.env["hospital.doctor"].sudo().create(
            {"name": "FD Floating Doctor %s" % uuid.uuid4().hex[:6]}
        )

        self._assign(appointment, doctor_without_department)

        appointment.invalidate_recordset()
        self.assertEqual(appointment.department_id, self.department)

    def test_assign_doctor_is_financially_inert(self):
        appointment, encounter = self._register_visit()
        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        encounter.invalidate_recordset()

        before_outstanding = encounter.reception_outstanding_amount
        before_clearance = account.financial_clearance_state
        before_charges = self.env["hospital.charge.line"].sudo().search_count(
            [("encounter_id", "=", encounter.id)]
        )
        before_receipts = self.env["hospital.charge.receipt"].sudo().search_count([])

        second_doctor = self.env["hospital.doctor"].sudo().create(
            {"name": "FD Inert Doctor", "department_id": self.department.id}
        )
        self._assign(appointment, second_doctor)

        account.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertEqual(encounter.reception_outstanding_amount, before_outstanding)
        self.assertEqual(account.financial_clearance_state, before_clearance)
        self.assertEqual(
            self.env["hospital.charge.line"].sudo().search_count(
                [("encounter_id", "=", encounter.id)]
            ),
            before_charges,
        )
        self.assertEqual(
            self.env["hospital.charge.receipt"].sudo().search_count([]),
            before_receipts,
        )
        self.assertEqual(self._stage(appointment), "intake")

    def test_assign_doctor_requires_an_intake_role(self):
        appointment, _encounter = self._register_visit()

        with self.assertRaises(AccessError):
            self._assign(appointment, self.doctor, user=self.ward_nurse)
        with self.assertRaises(AccessError):
            self._assign(appointment, self.doctor, user=self.cashier)

    # ==================================================================
    # B. Doctor boundary
    # ==================================================================
    def test_front_desk_nurse_cannot_start_consultation(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        self._pay_full(encounter)

        with self.assertRaises(AccessError):
            appointment.with_user(self.front_desk).action_start_consultation()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.sudo().state, "confirmed")

    def test_ward_nurse_cannot_start_consultation(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        self._pay_full(encounter)

        with self.assertRaises(AccessError):
            appointment.with_user(self.ward_nurse).action_start_consultation()

    def test_cashier_cannot_start_consultation(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        self._pay_full(encounter)

        with self.assertRaises(AccessError):
            appointment.with_user(self.cashier).action_start_consultation()

    def test_assigned_doctor_needs_completed_triage(self):
        appointment, encounter = self._register_visit()
        self._pay_full(encounter)
        self.assertEqual(self._stage(appointment), "intake")

        with self.assertRaisesRegex(UserError, "triage"):
            appointment.with_user(self.doctor_user).action_start_consultation()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.sudo().state, "confirmed")

    def test_assigned_doctor_needs_financial_clearance(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        self.assertEqual(self._stage(appointment), "awaiting_cashier")

        with self.assertRaises(UserError):
            appointment.with_user(self.doctor_user).action_start_consultation()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.sudo().state, "confirmed")

    def test_assigned_doctor_starts_when_triaged_and_cleared(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        self._pay_full(encounter)
        self.assertEqual(self._stage(appointment), "ready_doctor")

        appointment.with_user(self.doctor_user).action_start_consultation()

        appointment.invalidate_recordset()
        self.assertEqual(appointment.sudo().state, "in_consultation")
        self.assertEqual(self._stage(appointment), "in_consultation")

    # ==================================================================
    # C. Financial boundary
    # ==================================================================
    def test_triage_does_not_persist_a_false_clearance(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)

        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        encounter.invalidate_recordset()
        self.assertFalse(encounter.reception_clearance_ok)
        self.assertNotEqual(account.financial_clearance_state, "cleared")
        self.assertGreater(account.amount_due_for_clearance, 0.0)

    def test_ready_for_doctor_does_not_require_accounting_posting(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        receipt = self._pay_full(encounter).sudo()

        # Operational intake only: nothing posted, nothing fiscalized.
        self.assertEqual(receipt.state, "confirmed")
        self.assertFalse(receipt.accounting_posted)
        self.assertFalse(receipt.fiscalized)
        if "accounting_move_id" in receipt._fields:
            self.assertFalse(receipt.accounting_move_id)
        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        self.assertEqual(account.accounting_receipt_state, "unposted")

        self.assertEqual(self._stage(appointment), "ready_doctor")
        appointment.with_user(self.doctor_user).action_start_consultation()
        appointment.invalidate_recordset()
        self.assertEqual(appointment.sudo().state, "in_consultation")

    def test_partial_payment_keeps_the_patient_at_the_cashier(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        self._pay_full(encounter, amount=100.0)

        self.assertEqual(self._stage(appointment), "awaiting_cashier")
        with self.assertRaises(UserError):
            appointment.with_user(self.doctor_user).action_start_consultation()

    # ==================================================================
    # E. Security
    # ==================================================================
    def test_front_desk_nurse_cannot_record_payment(self):
        appointment, encounter = self._register_visit()
        account = encounter.billing_account_id.sudo()
        before = self.env["hospital.charge.receipt"].sudo().search_count([])

        with self.assertRaises(AccessError):
            account.with_user(self.front_desk).record_operational_payment(
                300.0, "cash", intake_token=uuid.uuid4().hex
            )

        self.assertEqual(
            self.env["hospital.charge.receipt"].sudo().search_count([]), before
        )

    def test_front_desk_nurse_cannot_post_accounting(self):
        appointment, encounter = self._register_visit()
        evaluation = self._save_triage(appointment)
        self._complete_triage(evaluation)
        receipt = self._pay_full(encounter).sudo()

        # assert_invoice_authorized in hospital_billing_accounting raises
        # AccessError. Odoo's assertRaises override rejects a tuple of types.
        with self.assertRaises(AccessError):
            receipt.with_user(self.front_desk).action_post_receipt_accounting()

        receipt.invalidate_recordset()
        self.assertFalse(receipt.accounting_posted)

    def test_front_desk_nurse_holds_no_forbidden_group(self):
        user = self.front_desk
        self.assertTrue(user.has_group(G_FRONT_DESK_NURSE))
        # Implies Nurse on purpose: that is where triage rights come from.
        self.assertTrue(user.has_group(G_NURSE))
        for forbidden in (
            G_CASHIER,
            G_ACCOUNTANT,
            G_RECEPTIONIST,
            "hospital_management.group_hospital_manager",
            "hospital_management.group_hospital_system_administrator",
            "hospital_management.group_hospital_doctor",
            "yoya_reception_bridge.group_hospital_emergency_authorizer",
        ):
            self.assertFalse(
                user.has_group(forbidden),
                "Front Desk Nurse must not hold %s" % forbidden,
            )

    def test_cashier_cannot_edit_triage(self):
        appointment, encounter = self._register_visit()

        with self.assertRaises(AccessError):
            self.env["hospital.patient.evaluation"].with_user(self.cashier).create(
                {
                    "patient_id": appointment.patient_id.id,
                    "appointment_id": appointment.id,
                    "chief_complaint": "Cashier should not be able to write this",
                }
            )

        evaluation = self._save_triage(appointment)
        with self.assertRaises(AccessError):
            evaluation.with_user(self.cashier).write({"chief_complaint": "tampered"})

    def test_front_desk_nurse_cannot_create_a_visit_outside_the_workflow(self):
        """The intake create bits must not become a bypass of create_visit()."""
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "FD Direct Create %s" % uuid.uuid4().hex[:6]}
        )

        with self.assertRaises(AccessError):
            self.env["hospital.appointment"].with_user(self.front_desk).create(
                {
                    "patient_id": patient.id,
                    "appointment_date": fields.Datetime.now(),
                }
            )
        with self.assertRaises(AccessError):
            self.env["hospital.patient"].with_user(self.front_desk).create(
                {"name": "FD Direct Patient %s" % uuid.uuid4().hex[:6]}
            )
        with self.assertRaises(AccessError):
            self.env["hospital.encounter"].with_user(self.front_desk).create(
                {
                    "patient_id": patient.id,
                    "encounter_type": "outpatient",
                    "state": "active",
                    "company_id": self.company.id,
                }
            )

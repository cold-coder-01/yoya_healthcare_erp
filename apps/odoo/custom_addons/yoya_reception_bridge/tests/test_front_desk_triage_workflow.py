"""Phase A: triage happens BEFORE the cashier, and the doctor gate still holds.

The workflow this proves is the one the hospital actually runs:

    arrive -> front desk NURSE -> registration -> triage -> triage complete
           -> cashier -> financial clearance -> ready for doctor -> consultation

The previous implementation required payment before triage. These tests would
have failed against it, which is the point.
"""
import uuid

from odoo import fields
from odoo.exceptions import AccessError, UserError
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

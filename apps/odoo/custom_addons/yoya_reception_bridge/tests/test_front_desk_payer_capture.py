"""Phase 2B: front-desk payer capture, its guards and its freeze.

The workflow proved here is the one the hospital runs:

    arrive -> register (optionally under a payer identity) -> triage
           -> [payer still correctable] -> cashier -> [payer frozen] -> doctor

The freeze point is deliberate and is asserted from both sides: a nurse may
still fix a wrongly-selected payer after triage completes, and may not once a
receipt exists or the consultation has started.
"""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.hospital_billing.models.encounter_payer import (
    PAYER_IDENTITY_AUTHORITY,
)
from odoo.addons.yoya_reception_bridge.models.reception_workflow import (
    REGISTRATION_GROUPS,
    VISIT_PAYER_GROUPS,
)

G_FRONT_DESK = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_NURSE = "hospital_management.group_hospital_nurse"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_MANAGER = "hospital_management.group_hospital_manager"


@tagged("post_install", "-at_install", "front_desk_payer_capture")
class TestFrontDeskPayerCapture(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hospital.patient.payer"])
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
                "name": "Payer Capture Dept %s" % suffix,
                "code": "PCD%s" % suffix.upper(),
            }
        )

        cls.front_desk = cls._make_user("pc_front_desk", [G_FRONT_DESK])
        cls.ward_nurse = cls._make_user("pc_ward_nurse", [G_NURSE])
        cls.cashier = cls._make_user("pc_cashier", [G_CASHIER])
        cls.officer = cls._make_user("pc_officer", [G_OFFICER])
        cls.manager = cls._make_user("pc_manager", [G_MANAGER])
        cls.doctor_user = cls._make_user("pc_doctor", [G_DOCTOR])
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {
                "name": "Payer Capture Doctor",
                "user_id": cls.doctor_user.id,
                "department_id": cls.department.id,
            }
        )
        cls.agreement = cls._make_agreement()

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, login, group_xmlids):
        return cls.env["res.users"].sudo().create(
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

    @classmethod
    def _make_agreement(cls, company=None):
        company = company or cls.env.company
        partner = cls.env["res.partner"].sudo().create(
            {"name": "PC Partner %s" % uuid.uuid4().hex[:6]}
        )
        payer = cls.env["hospital.payer"].sudo().create(
            {
                "name": "PC Payer %s" % uuid.uuid4().hex[:6],
                "payer_type": "insurance",
                "partner_id": partner.id,
                "company_id": company.id,
            }
        )
        agreement = cls.env["hospital.payer.agreement"].sudo().create(
            {
                "payer_id": payer.id,
                "agreement_number": "PC-%s" % uuid.uuid4().hex[:8].upper(),
                "company_id": company.id,
                "effective_from": cls.today - timedelta(days=30),
                "limit_scope": "unlimited",
            }
        )
        agreement.sudo().action_activate()
        return agreement

    def _eligibility(self, patient, agreement=None, activate=True):
        record = self.env["hospital.patient.payer"].sudo().create(
            {
                "patient_id": patient.id,
                "agreement_id": (agreement or self.agreement).id,
                "effective_from": self.today - timedelta(days=1),
            }
        )
        if activate:
            record.action_activate()
        return record

    def _workflow(self, user=None):
        return self.env["hospital.reception.workflow"].with_user(
            user or self.front_desk
        )

    def _register(self, user=None, patient_payer=None):
        result = self._workflow(user).create_visit(
            patient_values={"name": "PC Patient %s" % uuid.uuid4().hex[:6]},
            department=self.department,
            doctor=self.doctor,
            patient_payer=patient_payer,
        )
        return result["appointment"].sudo(), result["encounter"].sudo()

    def _complete_triage(self, appointment):
        evaluation = (
            self.env["hospital.patient.evaluation"]
            .with_user(self.front_desk)
            .create(
                {
                    "patient_id": appointment.patient_id.id,
                    "appointment_id": appointment.id,
                    "chief_complaint": "Payer capture triage",
                    "temperature": 37.0,
                    "triage_priority": "routine",
                }
            )
        )
        evaluation.action_start_evaluation()
        evaluation.action_done()
        return evaluation

    def _pay_full(self, encounter):
        account = encounter.billing_account_id.sudo()
        account.invalidate_recordset()
        return account.with_user(self.cashier).record_operational_payment(
            account.amount_due_for_clearance, "cash", intake_token=uuid.uuid4().hex
        )

    # ==================================================================
    # Controlled selection
    # ==================================================================
    def test_front_desk_can_set_payer_through_the_workflow(self):
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)

        result = self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )

        self.assertEqual(result["patient_payer"], eligibility)
        self.assertEqual(encounter.patient_payer_id, eligibility)
        # THE phase boundary.
        self.assertEqual(result["payer_type"], "self_pay")
        self.assertEqual(encounter.payer_type, "self_pay")

    def test_front_desk_can_clear_the_payer(self):
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )

        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=None
        )
        self.assertFalse(encounter.patient_payer_id)

    def test_setting_the_payer_does_not_clear_the_cash_gate(self):
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)

        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self.env.invalidate_all()

        self.assertFalse(encounter.reception_clearance_ok)
        self.assertGreater(encounter.reception_outstanding_amount, 0.0)
        self.assertNotEqual(
            encounter.billing_account_id.financial_clearance_state,
            "credit_authorized",
        )

    def test_setting_the_payer_does_not_move_the_queue_stage(self):
        appointment, _encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self.env.invalidate_all()
        before = appointment.front_desk_stage

        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self.env.invalidate_all()

        self.assertEqual(appointment.front_desk_stage, before)

    # ==================================================================
    # Raw-write bypass
    # ==================================================================
    def test_raw_patient_payer_id_write_is_rejected(self):
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        with self.assertRaises(AccessError):
            encounter.with_user(self.front_desk).write(
                {"patient_payer_id": eligibility.id}
            )

    def test_raw_payer_type_write_is_rejected(self):
        """The pre-existing cash-gate bypass, closed."""
        _appointment, encounter = self._register()
        with self.assertRaises(AccessError):
            encounter.with_user(self.front_desk).write({"payer_type": "insurance"})

    def test_raw_payer_id_write_is_rejected(self):
        _appointment, encounter = self._register()
        partner = self.env["res.partner"].sudo().create({"name": "PC Sponsor"})
        with self.assertRaises(AccessError):
            encounter.with_user(self.front_desk).write({"payer_id": partner.id})

    def test_payer_authority_may_still_write_the_legacy_fields(self):
        """Higher authority is preserved, not collaterally revoked."""
        _appointment, encounter = self._register()
        partner = self.env["res.partner"].sudo().create({"name": "PC Sponsor OK"})
        encounter.with_user(self.manager).write(
            {"payer_type": "insurance", "payer_id": partner.id}
        )
        self.assertEqual(encounter.payer_type, "insurance")

    # ==================================================================
    # Role boundary on the controlled path
    # ==================================================================
    def test_unauthorized_roles_cannot_set_the_payer(self):
        appointment, _encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        for user in (self.cashier, self.ward_nurse, self.doctor_user):
            with self.subTest(user=user.name):
                with self.assertRaises(AccessError):
                    self._workflow(user).set_visit_payer(
                        appointment.with_user(user), patient_payer=eligibility
                    )

    def test_wrong_patient_eligibility_is_refused(self):
        appointment, _encounter = self._register()
        other = self.env["hospital.patient"].sudo().create({"name": "PC Other"})
        foreign = self._eligibility(other)
        with self.assertRaises(ValidationError):
            self._workflow().set_visit_payer(
                appointment.with_user(self.front_desk), patient_payer=foreign
            )

    def test_non_selectable_eligibility_is_refused(self):
        appointment, _encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id, activate=False)
        with self.assertRaises(ValidationError):
            self._workflow().set_visit_payer(
                appointment.with_user(self.front_desk), patient_payer=eligibility
            )

    # ==================================================================
    # Freeze
    # ==================================================================
    def test_payer_is_still_correctable_after_triage_completes(self):
        """Triage completion is NOT a financial event, so it must not freeze."""
        appointment, encounter = self._register()
        first = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=first
        )
        self._complete_triage(appointment)
        self.env.invalidate_all()
        self.assertEqual(appointment.front_desk_stage, "awaiting_cashier")

        corrected = self._eligibility(
            appointment.patient_id, agreement=self._make_agreement()
        )
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=corrected
        )
        self.assertEqual(encounter.patient_payer_id, corrected)

    def test_front_desk_cannot_change_the_payer_once_a_receipt_exists(self):
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self._complete_triage(appointment)
        self._pay_full(encounter)
        self.env.invalidate_all()

        replacement = self._eligibility(
            appointment.patient_id, agreement=self._make_agreement()
        )
        with self.assertRaises(UserError):
            self._workflow().set_visit_payer(
                appointment.with_user(self.front_desk), patient_payer=replacement
            )
        self.assertEqual(encounter.patient_payer_id, eligibility)

    def test_reselecting_the_same_payer_after_payment_is_a_no_op(self):
        """Idempotence must not be punished by the freeze."""
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self._complete_triage(appointment)
        self._pay_full(encounter)
        self.env.invalidate_all()

        result = self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self.assertEqual(result["patient_payer"], eligibility)

    def test_front_desk_cannot_change_the_payer_once_consultation_started(self):
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self._complete_triage(appointment)
        self._pay_full(encounter)
        self.env.invalidate_all()
        appointment.with_user(self.doctor_user).action_start_consultation()
        self.env.invalidate_all()
        self.assertEqual(encounter.state, "active")

        replacement = self._eligibility(
            appointment.patient_id, agreement=self._make_agreement()
        )
        with self.assertRaises(UserError):
            self._workflow().set_visit_payer(
                appointment.with_user(self.front_desk), patient_payer=replacement
            )

    def test_manager_may_correct_after_the_freeze(self):
        """Manager is in BOTH tuples, so this alone proves little.

        It is exactly why the Insurance/Credit Officer gap below went unnoticed:
        a manager satisfies REGISTRATION_GROUPS as well as
        PAYER_IDENTITY_AUTHORITY, so this test passed against a guard that
        rejected every standalone payer-authority role.
        """
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self._complete_triage(appointment)
        self._pay_full(encounter)
        self.env.invalidate_all()

        corrected = self._eligibility(
            appointment.patient_id, agreement=self._make_agreement()
        )
        self._workflow(self.manager).set_visit_payer(
            appointment.with_user(self.manager), patient_payer=corrected
        )
        self.assertEqual(encounter.patient_payer_id, corrected)

    # ==================================================================
    # Payer authority WITHOUT registration rights
    # ==================================================================
    def test_officer_fixture_holds_payer_authority_and_no_registration_rights(self):
        """B. Prove the next test is exercising payer authority, nothing else.

        If the officer ever picked up a registration group -- directly or through
        an implied_ids change on hospital_billing's side -- the correction test
        below would start passing for the wrong reason. This pins that down.
        """
        for group in REGISTRATION_GROUPS:
            self.assertFalse(
                self.officer.has_group(group),
                "the officer fixture unexpectedly holds %s" % group,
            )
        self.assertTrue(
            any(self.officer.has_group(group) for group in PAYER_IDENTITY_AUTHORITY)
        )
        # And the guard really is the union of the two, not one or the other.
        self.assertEqual(
            set(VISIT_PAYER_GROUPS),
            set(REGISTRATION_GROUPS) | set(PAYER_IDENTITY_AUTHORITY),
        )

    def test_officer_has_no_ambient_access_to_the_visit_records(self):
        """WHY set_visit_payer elevates. Pinned so it is not "tidied away".

        group_hospital_insurance_officer implies no other role and carries ACL
        rows for hospital.payer, hospital.payer.agreement and
        hospital.patient.payer only. Widening the guard without elevating inside
        the method would move the AccessError from the guard to the ORM two
        lines later, and the correction path would still not work.

        This is also the boundary the elevation must NOT cross: the officer
        gains no standing access to clinical records, only the ability to
        complete set_visit_payer.
        """
        appointment, encounter = self._register()
        with self.assertRaises(AccessError):
            appointment.with_user(self.officer).read(["state"])
        with self.assertRaises(AccessError):
            encounter.with_user(self.officer).read(["state"])

    def test_officer_without_registration_rights_may_correct_after_freeze(self):
        """A. THE repair. Previously an AccessError from the group guard.

        The officer holds no registration group, so before the fix
        _assert_group(REGISTRATION_GROUPS, ...) rejected them before the freeze
        logic -- which names this very role as the one permitted to correct --
        was ever consulted.
        """
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self._complete_triage(appointment)
        self._pay_full(encounter)
        self.env.invalidate_all()

        # The front desk is genuinely frozen at this point.
        replacement = self._eligibility(
            appointment.patient_id, agreement=self._make_agreement()
        )
        with self.assertRaises(UserError):
            self._workflow().set_visit_payer(
                appointment.with_user(self.front_desk), patient_payer=replacement
            )

        # The officer is not.
        result = self._workflow(self.officer).set_visit_payer(
            appointment.with_user(self.officer), patient_payer=replacement
        )
        self.env.invalidate_all()
        self.assertEqual(encounter.patient_payer_id, replacement)
        self.assertEqual(result["patient_payer"], replacement)
        # The correction is still identity-only.
        self.assertEqual(encounter.payer_type, "self_pay")

    def test_officer_may_clear_the_payer_after_freeze(self):
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self._complete_triage(appointment)
        self._pay_full(encounter)
        self.env.invalidate_all()

        self._workflow(self.officer).set_visit_payer(
            appointment.with_user(self.officer), patient_payer=None
        )
        self.env.invalidate_all()
        self.assertFalse(encounter.patient_payer_id)

    def test_officer_still_cannot_write_the_field_directly(self):
        """Authority to call the workflow is not authority to write the column."""
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        with self.assertRaises(AccessError):
            encounter.with_user(self.officer).write(
                {"patient_payer_id": eligibility.id}
            )

    def test_unauthorized_roles_are_rejected_after_the_freeze_too(self):
        """C. Widening the guard must not have let anyone else through.

        AccessError, not UserError: they are stopped by authorization, not
        merely by the freeze -- so the assertion still holds on a visit where
        the freeze would have stopped them anyway.
        """
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        self._complete_triage(appointment)
        self._pay_full(encounter)
        self.env.invalidate_all()

        replacement = self._eligibility(
            appointment.patient_id, agreement=self._make_agreement()
        )
        for user in (self.cashier, self.ward_nurse, self.doctor_user):
            with self.subTest(user=user.name):
                with self.assertRaises(AccessError):
                    self._workflow(user).set_visit_payer(
                        appointment.with_user(user), patient_payer=replacement
                    )
        self.env.invalidate_all()
        self.assertEqual(encounter.patient_payer_id, eligibility)

    def test_closed_encounter_stays_locked_for_everyone(self):
        appointment, encounter = self._register()
        eligibility = self._eligibility(appointment.patient_id)
        self._workflow().set_visit_payer(
            appointment.with_user(self.front_desk), patient_payer=eligibility
        )
        encounter.sudo().write({"state": "cancelled"})
        self.env.invalidate_all()

        with self.assertRaises(UserError):
            self._workflow(self.manager).set_visit_payer(
                appointment.with_user(self.manager), patient_payer=None
            )

    # ==================================================================
    # create_visit atomicity
    # ==================================================================
    def test_create_visit_captures_a_valid_eligibility(self):
        patient = self.env["hospital.patient"].sudo().create({"name": "PC Atomic OK"})
        eligibility = self._eligibility(patient)

        result = self._workflow().create_visit(
            patient=patient.with_user(self.front_desk),
            department=self.department,
            doctor=self.doctor,
            patient_payer=eligibility,
        )

        self.assertEqual(result["encounter"].sudo().patient_payer_id, eligibility)
        self.assertEqual(result["encounter"].sudo().payer_type, "self_pay")

    def test_invalid_eligibility_rolls_back_the_whole_visit(self):
        """No orphan appointment, no orphan encounter, no orphan card charge."""
        patient = self.env["hospital.patient"].sudo().create({"name": "PC Atomic Bad"})
        other = self.env["hospital.patient"].sudo().create({"name": "PC Atomic Other"})
        foreign = self._eligibility(other)

        appointments_before = self.env["hospital.appointment"].sudo().search_count(
            [("patient_id", "=", patient.id)]
        )

        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self._workflow().create_visit(
                    patient=patient.with_user(self.front_desk),
                    department=self.department,
                    doctor=self.doctor,
                    patient_payer=foreign,
                )

        self.env.invalidate_all()
        self.assertEqual(
            self.env["hospital.appointment"].sudo().search_count(
                [("patient_id", "=", patient.id)]
            ),
            appointments_before,
        )
        self.assertFalse(
            self.env["hospital.encounter"].sudo().search_count(
                [("patient_id", "=", patient.id)]
            )
        )
        self.assertFalse(
            self.env["hospital.patient.card.issue"].sudo().search_count(
                [("patient_id", "=", patient.id)]
            )
        )

    # ==================================================================
    # Lookup
    # ==================================================================
    def test_selectable_eligibilities_returns_only_valid_ones(self):
        patient = self.env["hospital.patient"].sudo().create({"name": "PC Lookup"})
        valid = self._eligibility(patient)
        draft = self._eligibility(
            patient, agreement=self._make_agreement(), activate=False
        )
        suspended = self._eligibility(patient, agreement=self._make_agreement())
        suspended.action_suspend()
        other_patient = self.env["hospital.patient"].sudo().create(
            {"name": "PC Lookup Other"}
        )
        foreign = self._eligibility(other_patient)

        found = self._workflow().selectable_eligibilities(
            patient.with_user(self.front_desk)
        )

        self.assertIn(valid, found)
        self.assertNotIn(draft, found)
        self.assertNotIn(suspended, found)
        self.assertNotIn(foreign, found)

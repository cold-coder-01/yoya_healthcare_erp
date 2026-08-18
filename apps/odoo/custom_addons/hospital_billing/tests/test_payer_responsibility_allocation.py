"""Phase 3C/3D: sponsor responsibility, and the cash gate that follows it.

THE CLAIM THESE TESTS DEFEND
----------------------------
Sponsor responsibility is recorded and authorized money that the patient is NOT
asked for -- and it is never represented as patient cash. A fully sponsored
visit produces no receipt, leaves amount_received at zero, and still lets the
doctor start.

The mode assertions are the safety rail: under 'off' every figure and every
clearance answer must be the legacy one, so switching this code into a live
hospital changes nothing until an operator says so.
"""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.hospital_billing.models.charge_responsibility import (
    RESPONSIBILITY_AUTHORITY,
)

G_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_MANAGER = "hospital_management.group_hospital_manager"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"


@tagged("post_install", "-at_install", "payer_responsibility_allocation")
class TestPayerResponsibilityAllocation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.today = fields.Date.context_today(cls.env["hospital.patient.payer"])
        cls.engine = cls.env["hospital.billing.engine"]

        cls.officer = cls._make_user("resp_officer", [G_OFFICER])
        cls.manager = cls._make_user("resp_manager", [G_MANAGER])
        cls.cashier = cls._make_user("resp_cashier", [G_CASHIER])
        cls.accountant = cls._make_user("resp_accountant", [G_ACCOUNTANT])
        cls.front_desk = cls._make_user("resp_frontdesk", [G_FRONT_DESK_NURSE])

        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Responsibility Patient"}
        )
        cls.other_patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Responsibility Other Patient"}
        )
        cls.service = cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "Responsibility Consultation",
                "code": "RESP-%s" % uuid.uuid4().hex[:6].upper(),
                "service_type": "consultation",
                "default_price": 1500.0,
                # prepaid: amount_due_for_clearance is zero for delivery-basis
                # charges, so only a prepaid charge exercises the cash gate.
                "prepayment_required": True,
            }
        )
        cls.payer = cls._make_payer("Responsibility Payer")
        cls.agreement = cls._make_agreement(cls.payer)
        cls.eligibility = cls._make_eligibility(cls.patient, cls.agreement)

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, label, groups):
        return cls.env["res.users"].sudo().create(
            {
                "name": label,
                "login": "%s_%s" % (label, uuid.uuid4().hex[:8]),
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
                "groups_id": [
                    (
                        6,
                        0,
                        [cls.env.ref("base.group_user").id]
                        + [cls.env.ref(x).id for x in groups],
                    )
                ],
            }
        )

    @classmethod
    def _make_payer(cls, label, company=None):
        company = company or cls.company
        partner = cls.env["res.partner"].sudo().create(
            {"name": label, "company_id": company.id}
        )
        return cls.env["hospital.payer"].sudo().create(
            {
                "name": label,
                "payer_type": "insurance",
                "partner_id": partner.id,
                "company_id": company.id,
            }
        )

    @classmethod
    def _make_agreement(cls, payer, activate=True, **overrides):
        vals = {
            "payer_id": payer.id,
            "agreement_number": "RESP-%s" % uuid.uuid4().hex[:8].upper(),
            "company_id": payer.company_id.id,
            "effective_from": cls.today - timedelta(days=30),
            "limit_scope": "unlimited",
        }
        vals.update(overrides)
        agreement = cls.env["hospital.payer.agreement"].sudo().create(vals)
        if activate:
            agreement.sudo().action_activate()
        return agreement

    @classmethod
    def _make_eligibility(cls, patient, agreement, activate=True):
        eligibility = cls.env["hospital.patient.payer"].sudo().create(
            {
                "patient_id": patient.id,
                "agreement_id": agreement.id,
                "effective_from": max(cls.today, agreement.effective_from),
            }
        )
        if activate:
            eligibility.sudo().action_activate()
        return eligibility

    def _set_mode(self, mode):
        self.company.sudo().write({"payer_responsibility_mode": mode})
        # Stored computes depend on the flag; flush so the recompute lands
        # before anything reads a figure derived from it.
        self.env.flush_all()

    def _visit(self, eligibility=None, price=1500.0):
        """An encounter with one prepaid 1500 charge, ready to split."""
        # ONE ACTIVE EPISODE PER PATIENT. These tests reuse a single patient
        # across visits, so the previous episode is closed first -- which is
        # what happens between real attendances.
        self.env["hospital.encounter"].sudo().search(
            [
                ("patient_id", "=", self.patient.id),
                ("state", "not in", ["completed", "closed", "cancelled"]),
            ]
        ).write({"state": "closed"})
        encounter = self.env["hospital.encounter"].sudo().create(
            {
                "patient_id": self.patient.id,
                "encounter_type": "outpatient",
                "company_id": self.company.id,
                "opened_at": fields.Datetime.now(),
            }
        )
        if eligibility is not None:
            from odoo.addons.hospital_billing.models.encounter_payer import (
                payer_identity_capability,
            )

            with payer_identity_capability():
                encounter.sudo().write({"patient_payer_id": eligibility.id})
        charge = self.engine.sudo().create_or_update_charge(
            encounter,
            source_model="test.responsibility",
            source_res_id=encounter.id,
            source_event="consultation",
            description="Responsibility Consultation",
            service=self.service,
            qty_requested=1.0,
            unit_price=price,
        )
        self.engine.sudo().activate_charge(charge)
        return encounter, charge

    def _allocate(self, charge, amount, user=None, authorize=False, **kw):
        return self.engine.with_user(user or self.officer).allocate_payer(
            charge.sudo().billing_account_id,
            charge=charge,
            amount=amount,
            reason=kw.pop("reason", "Sponsor carries this share"),
            authorize=authorize,
            **kw,
        )

    def _pay(self, charge, amount):
        account = charge.sudo().billing_account_id
        return account.with_user(self.cashier).record_operational_payment(
            amount=amount, payment_method="cash",
            intake_token=uuid.uuid4().hex,
        )

    # ==================================================================
    # MODEL
    # ==================================================================
    def test_01_self_pay_patient_carries_everything(self):
        _, charge = self._visit()
        self.assertEqual(charge.sudo().amount_estimated, 1500.0)
        self.assertEqual(charge.sudo().amount_sponsor_authorized, 0.0)
        self.assertEqual(charge.sudo().amount_patient_responsibility, 1500.0)
        self.assertEqual(charge.sudo().responsibility_state, "self_pay")

    def test_02_draft_allocation_does_not_reduce_patient_share(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0)
        self.assertEqual(record.state, "draft")
        self.assertEqual(charge.sudo().amount_sponsor_responsibility, 1000.0)
        self.assertEqual(charge.sudo().amount_sponsor_authorized, 0.0)
        self.assertEqual(
            charge.sudo().amount_patient_responsibility, 1500.0,
            "A draft is a proposal; it must buy the patient nothing.",
        )
        self.assertEqual(charge.sudo().responsibility_state, "proposed")

    def test_03_authorization_makes_the_split_real(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0)
        record.with_user(self.officer).action_authorize(
            authorization_reference="GL-001"
        )
        self.assertEqual(record.state, "authorized")
        self.assertEqual(record.authorization_reference, "GL-001")
        self.assertEqual(record.authorized_by_id, self.officer)
        self.assertTrue(record.authorization_date)
        self.assertEqual(charge.sudo().amount_sponsor_authorized, 1000.0)
        self.assertEqual(charge.sudo().amount_patient_responsibility, 500.0)

    def test_04_over_allocation_refused(self):
        _, charge = self._visit(self.eligibility)
        with self.assertRaises(ValidationError):
            self._allocate(charge, 1500.01)
        with self.assertRaises(ValidationError):
            self._allocate(charge, 5000.0)

    def test_05_exactly_the_charge_amount_is_allowed(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1500.0, authorize=True)
        self.assertEqual(record.state, "authorized")
        self.assertEqual(charge.sudo().amount_patient_responsibility, 0.0)

    @mute_logger("odoo.sql_db")
    def test_06_negative_amount_refused(self):
        _, charge = self._visit(self.eligibility)
        with self.assertRaises(Exception):
            self._allocate(charge, -1.0)

    def test_07_eligibility_of_another_patient_refused(self):
        foreign = self._make_eligibility(
            self.other_patient, self._make_agreement(self._make_payer("Foreign Payer"))
        )
        _, charge = self._visit(self.eligibility)
        with self.assertRaises(ValidationError):
            self.env["hospital.charge.responsibility"].with_user(
                self.officer
            ).create(
                {
                    "charge_id": charge.id,
                    "patient_payer_id": foreign.id,
                    "amount": 100.0,
                }
            )

    def test_08_eligibility_not_selected_on_the_visit_refused(self):
        """Belongs to the right patient, but is not what the visit uses."""
        second = self._make_eligibility(
            self.patient, self._make_agreement(self._make_payer("Second Payer"))
        )
        _, charge = self._visit(self.eligibility)
        with self.assertRaises(ValidationError):
            self.env["hospital.charge.responsibility"].with_user(
                self.officer
            ).create(
                {
                    "charge_id": charge.id,
                    "patient_payer_id": second.id,
                    "amount": 100.0,
                }
            )

    def test_09_no_eligibility_on_the_visit_refused(self):
        _, charge = self._visit()  # self-pay, no patient_payer_id
        with self.assertRaises(UserError):
            self._allocate(charge, 1000.0)

    def test_10_cancellation_returns_the_full_share_to_the_patient(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0, authorize=True)
        self.assertEqual(charge.sudo().amount_patient_responsibility, 500.0)
        record.with_user(self.officer).action_cancel(reason="Sponsor declined")
        self.assertEqual(record.state, "cancelled")
        self.assertEqual(charge.sudo().amount_sponsor_authorized, 0.0)
        self.assertEqual(charge.sudo().amount_patient_responsibility, 1500.0)
        self.assertEqual(charge.sudo().responsibility_state, "self_pay")

    def test_11_cancel_requires_a_reason(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0, authorize=True)
        with self.assertRaises(UserError):
            record.with_user(self.officer).action_cancel()

    def test_12_authorize_requires_a_reason(self):
        _, charge = self._visit(self.eligibility)
        record = self.env["hospital.charge.responsibility"].with_user(
            self.officer
        ).create(
            {
                "charge_id": charge.id,
                "patient_payer_id": self.eligibility.id,
                "amount": 1000.0,
            }
        )
        with self.assertRaises(UserError):
            record.with_user(self.officer).action_authorize()

    def test_13_only_one_live_allocation_per_charge(self):
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0)
        with self.assertRaises(ValidationError):
            self._allocate(charge, 200.0)

    def test_14_a_cancelled_row_frees_the_charge(self):
        _, charge = self._visit(self.eligibility)
        first = self._allocate(charge, 1000.0)
        first.with_user(self.officer).action_cancel(reason="Wrong amount")
        second = self._allocate(charge, 800.0, authorize=True)
        self.assertEqual(second.state, "authorized")
        self.assertEqual(charge.sudo().amount_patient_responsibility, 700.0)

    def test_15_idempotent_retry_returns_the_same_row(self):
        _, charge = self._visit(self.eligibility)
        token = uuid.uuid4().hex
        first = self._allocate(charge, 1000.0, request_token=token)
        second = self._allocate(charge, 1000.0, request_token=token)
        self.assertEqual(first, second)
        self.assertEqual(
            charge.sudo().amount_sponsor_responsibility, 1000.0,
            "A retry must not add a second share.",
        )

    def test_16_same_token_different_amount_refused(self):
        _, charge = self._visit(self.eligibility)
        token = uuid.uuid4().hex
        self._allocate(charge, 1000.0, request_token=token)
        with self.assertRaises(ValidationError):
            self._allocate(charge, 900.0, request_token=token)

    def test_17_authorization_is_idempotent(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0, authorize=True)
        stamp = record.authorization_date
        record.with_user(self.officer).action_authorize()
        self.assertEqual(record.authorization_date, stamp)
        self.assertEqual(charge.sudo().amount_sponsor_authorized, 1000.0)

    def test_18_authorized_amount_is_frozen(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0, authorize=True)
        with self.assertRaises(UserError):
            record.with_user(self.officer).write({"amount": 200.0})

    def test_19_cancelled_row_cannot_be_authorized(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0)
        record.with_user(self.officer).action_cancel(reason="No")
        with self.assertRaises(UserError):
            record.with_user(self.officer).action_authorize()

    # ==================================================================
    # SECURITY
    # ==================================================================
    def test_20_front_desk_cannot_touch_responsibility(self):
        _, charge = self._visit(self.eligibility)
        with self.assertRaises(AccessError):
            self.env["hospital.charge.responsibility"].with_user(
                self.front_desk
            ).create(
                {
                    "charge_id": charge.id,
                    "patient_payer_id": self.eligibility.id,
                    "amount": 1000.0,
                }
            )

    def test_21_cashier_cannot_create_or_authorize(self):
        _, charge = self._visit(self.eligibility)
        with self.assertRaises(AccessError):
            self.env["hospital.charge.responsibility"].with_user(
                self.cashier
            ).create(
                {
                    "charge_id": charge.id,
                    "patient_payer_id": self.eligibility.id,
                    "amount": 1000.0,
                }
            )
        record = self._allocate(charge, 1000.0)
        with self.assertRaises(AccessError):
            record.with_user(self.cashier).action_authorize()

    def test_22_accountant_may_read_but_not_authorize(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0)
        self.assertTrue(record.with_user(self.accountant).read(["amount"]))
        with self.assertRaises(AccessError):
            record.with_user(self.accountant).action_authorize()

    def test_23_manager_has_override_authority(self):
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0, user=self.manager)
        record.with_user(self.manager).action_authorize()
        self.assertEqual(record.state, "authorized")

    def test_24_authority_tuple_excludes_the_accountant(self):
        self.assertNotIn(
            "hospital_management.group_hospital_accountant",
            RESPONSIBILITY_AUTHORITY,
            "Booking the receivable and creating it must stay separate duties.",
        )

    def test_25_commercial_terms_are_not_exposed_on_responsibility(self):
        """The model must not have grown a contract-terms field."""
        leaky = {
            "limit_amount", "member_limit_amount", "limit_scope",
            "payment_terms_days", "tariff_mode", "coverage_percent",
        }
        present = leaky & set(self.env["hospital.charge.responsibility"]._fields)
        self.assertFalse(present, "Commercial terms leaked onto responsibility: %s" % present)

    # ==================================================================
    # MODE
    # ==================================================================
    def test_30_off_preserves_legacy_clearance_and_figures(self):
        self._set_mode("off")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self.assertEqual(
            charge.sudo().amount_due_for_clearance, 1500.0,
            "Under 'off' the cashier must still be asked for the whole charge.",
        )
        result = self.engine.sudo().check_financial_clearance(
            charge.sudo().encounter_id
        )
        self.assertFalse(result["cleared"])
        self.assertEqual(result["state"], "pending")
        self.assertEqual(result["amount_due"], 1500.0)

    def test_31_shadow_exposes_the_split_but_does_not_enforce_it(self):
        self._set_mode("shadow")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self.assertEqual(
            charge.sudo().amount_patient_responsibility, 500.0,
            "Shadow must still COMPUTE the split.",
        )
        self.assertEqual(
            charge.sudo().amount_due_for_clearance, 1500.0,
            "Shadow must not change what is collected.",
        )
        result = self.engine.sudo().check_financial_clearance(
            charge.sudo().encounter_id
        )
        self.assertEqual(result["amount_due"], 1500.0)
        self.assertEqual(result["state"], "pending")

    def test_32_enforce_uses_the_patient_share(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self.assertEqual(charge.sudo().amount_due_for_clearance, 500.0)

    def test_33_enforce_with_no_sponsor_is_identical_to_off(self):
        self._set_mode("enforce")
        _, charge = self._visit()
        self.assertEqual(charge.sudo().amount_due_for_clearance, 1500.0)
        result = self.engine.sudo().check_financial_clearance(
            charge.sudo().encounter_id
        )
        self.assertEqual(result["state"], "pending")
        self.assertEqual(result["amount_due"], 1500.0)

    # ==================================================================
    # CLEARANCE
    # ==================================================================
    def test_40_self_pay_clearance_unchanged(self):
        self._set_mode("enforce")
        _, charge = self._visit()
        self._pay(charge, 1500.0)
        result = self.engine.sudo().check_financial_clearance(
            charge.sudo().encounter_id
        )
        self.assertTrue(result["cleared"])
        self.assertEqual(result["state"], "cleared")

    def test_41_partial_sponsor_not_authorized_blocks(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0)  # draft only
        result = self.engine.sudo().check_financial_clearance(
            charge.sudo().encounter_id
        )
        self.assertFalse(result["cleared"])
        self.assertEqual(result["state"], "pending")
        self.assertIn("NOT authorized", result["reason"])

    def test_42_partial_sponsor_authorized_patient_unpaid_blocks(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        result = self.engine.sudo().check_financial_clearance(
            charge.sudo().encounter_id
        )
        self.assertFalse(result["cleared"])
        self.assertEqual(result["state"], "pending")
        self.assertAlmostEqual(result["amount_due"], 500.0, places=2)

    def test_43_partial_sponsor_authorized_patient_paid_clears(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self._pay(charge, 500.0)
        result = self.engine.sudo().check_financial_clearance(
            charge.sudo().encounter_id
        )
        self.assertTrue(result["cleared"])
        self.assertEqual(result["state"], "cleared")
        self.assertEqual(charge.sudo().amount_received, 500.0)

    def test_44_full_sponsor_authorized_needs_no_cash(self):
        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._allocate(charge, 1500.0, authorize=True)
        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertTrue(result["cleared"])
        self.assertEqual(result["state"], "sponsor_cleared")
        self.assertEqual(result["amount_due"], 0.0)
        self.assertEqual(charge.sudo().amount_patient_responsibility, 0.0)

    def test_45_full_sponsor_creates_no_receipt_and_no_cash(self):
        """THE anti-fake-cash assertion."""
        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._allocate(charge, 1500.0, authorize=True)
        self.engine.sudo().check_financial_clearance(encounter, persist=True)
        self.assertEqual(charge.sudo().amount_received, 0.0)
        receipts = self.env["hospital.charge.receipt"].sudo().search(
            [("encounter_id", "=", encounter.id)]
        )
        self.assertFalse(
            receipts, "Sponsor authorization must never manufacture a receipt."
        )
        self.assertEqual(
            encounter.sudo().billing_account_id.financial_clearance_state,
            "sponsor_cleared",
        )

    def test_46_full_sponsor_unauthorized_is_blocked(self):
        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._allocate(charge, 1500.0)  # draft
        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertFalse(result["cleared"])

    def test_47_emergency_bypass_stays_independent(self):
        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0)  # draft: would otherwise block
        # As the MANAGER, not sudo(): res.users.has_group does not auto-pass for
        # the superuser, so a sudo() write here would raise the same AccessError
        # the bypass guard raises for anyone else.
        encounter.with_user(self.manager).write(
            {
                "emergency_bypass": True,
                "emergency_bypass_reason": "Critical presentation",
            }
        )
        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertTrue(result["cleared"])
        self.assertEqual(
            result["state"], "emergency_bypass",
            "Bypass must short-circuit ahead of the responsibility engine.",
        )

    def test_48_sponsor_cleared_is_in_the_state_vocabularies(self):
        """Both the account and the reception mirror must know the state."""
        account_states = dict(
            self.env["hospital.billing.account"]._fields[
                "financial_clearance_state"
            ].selection
        )
        self.assertIn("sponsor_cleared", account_states)
        reception_states = dict(
            self.env["hospital.encounter"]._fields[
                "reception_clearance_state"
            ].selection
        )
        self.assertIn("sponsor_cleared", reception_states)

    # ==================================================================
    # RECEIPTS
    # ==================================================================
    def test_50_amount_received_stays_patient_cash(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self.assertEqual(charge.sudo().amount_received, 0.0)
        self._pay(charge, 500.0)
        self.assertEqual(
            charge.sudo().amount_received, 500.0,
            "Only real patient cash may appear here.",
        )

    def test_51_enforce_caps_collection_at_the_patient_share(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self.assertEqual(
            charge.sudo().get_patient_payable_ceiling(), 500.0,
            "The cashier must be offered 500, not 1500.",
        )
        with self.assertRaises(ValidationError):
            self.env["hospital.charge.receipt.allocation"].sudo().create(
                {
                    "receipt_id": self.env["hospital.charge.receipt"].sudo().create(
                        {"payment_method": "cash"}
                    ).id,
                    "charge_line_id": charge.id,
                    "amount": 1500.0,
                }
            )

    def test_52_off_mode_receipt_behaviour_is_unchanged(self):
        self._set_mode("off")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self.assertEqual(
            charge.sudo().get_patient_payable_ceiling(), 1500.0,
            "Under 'off' the legacy whole-charge ceiling must stand.",
        )
        self._pay(charge, 1500.0)
        self.assertEqual(charge.sudo().amount_received, 1500.0)

    def test_53_shadow_mode_receipt_behaviour_is_unchanged(self):
        self._set_mode("shadow")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self.assertEqual(charge.sudo().get_patient_payable_ceiling(), 1500.0)
        self._pay(charge, 1500.0)
        self.assertEqual(charge.sudo().amount_received, 1500.0)

    # ==================================================================
    # FREEZE
    # ==================================================================
    def test_60_payment_freezes_responsibility(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._pay(charge, 500.0)
        with self.assertRaises(UserError):
            self._allocate(charge, 1000.0)

    def test_61_payment_freezes_cancellation_too(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0, authorize=True)
        self._pay(charge, 500.0)
        with self.assertRaises(UserError):
            record.with_user(self.officer).action_cancel(reason="Too late")

    def test_62_authorize_before_payment_is_the_supported_order(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self._pay(charge, 500.0)
        self.assertEqual(charge.sudo().amount_received, 500.0)
        self.assertEqual(charge.sudo().amount_due_for_clearance, 0.0)

    # ==================================================================
    # AGGREGATES
    # ==================================================================
    def test_70_account_aggregates(self):
        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        account = encounter.sudo().billing_account_id
        self.assertEqual(account.amount_estimated, 1500.0)
        self.assertEqual(account.amount_sponsor_authorized, 1000.0)
        self.assertEqual(account.amount_sponsor_responsibility, 1000.0)
        self.assertEqual(account.amount_patient_responsibility, 500.0)
        self.assertEqual(account.amount_patient_outstanding, 500.0)
        self.assertEqual(account.amount_sponsor_outstanding, 1000.0)
        self.assertEqual(account.responsibility_state, "authorized")
        self._pay(charge, 500.0)
        self.assertEqual(account.amount_received, 500.0)
        self.assertEqual(account.amount_patient_outstanding, 0.0)

    def test_71_cancelled_charge_leaves_the_aggregates(self):
        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        account = encounter.sudo().billing_account_id
        self.assertEqual(account.amount_sponsor_authorized, 1000.0)
        self.engine.sudo().cancel_charge(charge, reason="Not needed")
        self.assertEqual(
            account.amount_sponsor_authorized, 0.0,
            "A cancelled charge must not inflate what a payer appears to owe.",
        )

    # ==================================================================
    # API CONTRACT
    # ==================================================================
    def test_80_financial_block_shape_and_values(self):
        from odoo.addons.yoya_emr_api.services.cashier_serializers import (
            serialize_financial_block,
        )

        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        account = encounter.sudo().billing_account_id

        block = serialize_financial_block(encounter.sudo(), account)
        self.assertEqual(block["responsibility_mode"], "enforce")
        self.assertFalse(block["responsibility_advisory"])
        self.assertEqual(block["amount_estimated"], 1500.0)
        self.assertEqual(block["sponsor_authorized"], 1000.0)
        self.assertEqual(block["patient_responsibility"], 500.0)
        self.assertEqual(block["patient_paid"], 0.0)
        self.assertEqual(block["patient_outstanding"], 500.0)

        self._pay(charge, 500.0)
        block = serialize_financial_block(encounter.sudo(), account)
        self.assertEqual(block["patient_paid"], 500.0)
        self.assertEqual(block["patient_outstanding"], 0.0)

    def test_81_shadow_marks_the_split_advisory(self):
        from odoo.addons.yoya_emr_api.services.cashier_serializers import (
            serialize_financial_block,
        )

        self._set_mode("shadow")
        encounter, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        block = serialize_financial_block(
            encounter.sudo(), encounter.sudo().billing_account_id
        )
        self.assertEqual(block["responsibility_mode"], "shadow")
        self.assertTrue(
            block["responsibility_advisory"],
            "A client must be told the split is not driving the cash gate.",
        )
        self.assertEqual(block["patient_responsibility"], 500.0)

    def test_82_financial_block_leaks_no_commercial_terms(self):
        from odoo.addons.yoya_emr_api.services.cashier_serializers import (
            serialize_cashier_charge_line,
            serialize_financial_block,
        )

        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        payload = {
            **serialize_financial_block(
                encounter.sudo(), encounter.sudo().billing_account_id
            ),
            **serialize_cashier_charge_line(charge.sudo()),
        }
        for banned in (
            "limit_amount", "member_limit_amount", "limit_scope",
            "payment_terms_days", "tariff_mode", "notes", "agreement_id",
        ):
            self.assertNotIn(banned, payload)

    # ==================================================================
    # HARDENING 1: THE LEGACY WAIVER MUST NOT BYPASS THE NEW DOMAIN
    #
    # encounter.payer_type != 'self_pay' waives the whole bill. If that arm is
    # allowed to answer first for a visit that is in the NEW responsibility
    # domain, then under 'enforce' a sponsor share that nobody authorized still
    # produces a zero-cash clearance -- the responsibility engine never runs.
    # ==================================================================
    def _legacy_sponsored(self, encounter):
        """Set the LEGACY payer classification, as an authorized role.

        Written only here, and only to build the backward-compatibility
        fixture. Nothing in the responsibility engine writes these fields.

        As the MANAGER: the Insurance Officer is in PAYER_IDENTITY_AUTHORITY,
        which is the model-level guard on these two fields, but holds no write
        ACL on hospital.encounter -- the ACL is the outer gate and it is checked
        first.
        """
        partner = self.env["res.partner"].sudo().create(
            {"name": "Legacy Payer Partner %s" % uuid.uuid4().hex[:6]}
        )
        encounter.with_user(self.manager).write(
            {"payer_type": "insurance", "payer_id": partner.id}
        )
        return partner

    def test_90_enforce_legacy_waiver_cannot_bypass_unauthorized_sponsor(self):
        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._legacy_sponsored(encounter)
        self._allocate(charge, 1000.0)  # draft: NOT authorized

        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertFalse(
            result["cleared"],
            "A visit in the new responsibility domain must not be cleared by "
            "the legacy whole-bill waiver while its sponsor share is "
            "unauthorized.",
        )
        self.assertNotEqual(result["state"], "credit_authorized")
        self.assertEqual(result["state"], "pending")

    def test_91_enforce_new_domain_full_sponsor_is_sponsor_cleared(self):
        self._set_mode("enforce")
        encounter, charge = self._visit(self.eligibility)
        self._legacy_sponsored(encounter)
        self._allocate(charge, 1500.0, authorize=True)

        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertTrue(result["cleared"])
        self.assertEqual(
            result["state"], "sponsor_cleared",
            "The new domain must answer, not the legacy waiver.",
        )

    def test_92_enforce_legacy_only_encounter_keeps_legacy_behaviour(self):
        """No eligibility, no allocation: not in the new domain at all."""
        self._set_mode("enforce")
        encounter, _charge = self._visit()  # no patient_payer_id
        self._legacy_sponsored(encounter)

        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertTrue(
            result["cleared"],
            "A legacy-only sponsored visit must keep working exactly as before.",
        )
        self.assertEqual(result["state"], "credit_authorized")
        self.assertEqual(result["amount_due"], 0.0)

    def test_93_off_mode_legacy_waiver_is_untouched(self):
        self._set_mode("off")
        encounter, charge = self._visit(self.eligibility)
        self._legacy_sponsored(encounter)
        self._allocate(charge, 1000.0)  # draft

        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertTrue(result["cleared"])
        self.assertEqual(
            result["state"], "credit_authorized",
            "Under 'off' the legacy waiver must answer exactly as it always did.",
        )

    def test_94_shadow_mode_legacy_waiver_is_untouched(self):
        self._set_mode("shadow")
        encounter, charge = self._visit(self.eligibility)
        self._legacy_sponsored(encounter)
        self._allocate(charge, 1000.0)  # draft

        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertTrue(result["cleared"])
        self.assertEqual(result["state"], "credit_authorized")
        self.assertEqual(
            charge.sudo().amount_patient_responsibility, 1500.0,
            "Shadow still computes the split; it just does not act on it.",
        )

    def test_95_participation_is_eligibility_or_a_live_share(self):
        """Either new-domain signal is enough, on its own."""
        self._set_mode("enforce")

        # Signal A: an eligibility on the visit, no allocation yet.
        encounter_a, _ = self._visit(self.eligibility)
        self._legacy_sponsored(encounter_a)
        result_a = self.engine.sudo().check_financial_clearance(encounter_a)
        self.assertNotEqual(
            result_a["state"], "credit_authorized",
            "Recording a payer identity puts the visit in the new domain.",
        )
        self.assertFalse(result_a["cleared"])

        # Signal B: no eligibility -> not participating -> legacy stands.
        encounter_b, _ = self._visit()
        self._legacy_sponsored(encounter_b)
        result_b = self.engine.sudo().check_financial_clearance(encounter_b)
        self.assertEqual(result_b["state"], "credit_authorized")

    # ==================================================================
    # HARDENING 2: THE CHARGE MAY NOT SHRINK BELOW WHAT IS AUTHORIZED
    #
    # amount_patient_responsibility is max(0, estimated - authorized), so a
    # reprice that drops the charge under the authorized sponsor share does not
    # error -- it silently floors the patient at zero and leaves a sponsor
    # carrying more than the charge is worth.
    # ==================================================================
    def test_96_charge_cannot_shrink_below_authorized_sponsor_share(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        self.assertEqual(charge.sudo().amount_patient_responsibility, 500.0)

        with self.assertRaises(ValidationError):
            charge.sudo().write({"unit_price": 700.0})

        charge.sudo().invalidate_recordset()
        self.assertEqual(charge.sudo().amount_estimated, 1500.0)
        self.assertEqual(charge.sudo().amount_sponsor_authorized, 1000.0)
        self.assertEqual(charge.sudo().amount_patient_responsibility, 500.0)

    def test_97_charge_may_shrink_to_exactly_the_authorized_share(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        charge.sudo().write({"unit_price": 1000.0})
        self.assertEqual(charge.sudo().amount_estimated, 1000.0)
        self.assertEqual(charge.sudo().amount_patient_responsibility, 0.0)

    def test_98_charge_may_grow_and_the_residual_follows(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        charge.sudo().write({"unit_price": 1800.0})
        self.assertEqual(charge.sudo().amount_estimated, 1800.0)
        self.assertEqual(
            charge.sudo().amount_sponsor_authorized, 1000.0,
            "Growing the charge must not silently enlarge the sponsor's share.",
        )
        self.assertEqual(charge.sudo().amount_patient_responsibility, 800.0)
        self.assertEqual(charge.sudo().amount_due_for_clearance, 800.0)

    def test_99_cancelled_share_stops_blocking_a_reprice(self):
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        record = self._allocate(charge, 1000.0, authorize=True)
        with self.assertRaises(ValidationError):
            charge.sudo().write({"unit_price": 700.0})

        record.with_user(self.officer).action_cancel(reason="Sponsor withdrew")
        charge.sudo().write({"unit_price": 700.0})
        self.assertEqual(charge.sudo().amount_estimated, 700.0)
        self.assertEqual(charge.sudo().amount_patient_responsibility, 700.0)

    def test_100_a_draft_share_does_not_block_a_reprice(self):
        """Only AUTHORIZED money constrains the charge. A draft is a proposal."""
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0)  # draft
        charge.sudo().write({"unit_price": 700.0})
        self.assertEqual(charge.sudo().amount_estimated, 700.0)

    def test_101_quantity_reduction_is_guarded_too(self):
        """The guard is on the VALUE, not on one field that produces it."""
        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility, price=1000.0)
        self._allocate(charge, 900.0, authorize=True)
        with self.assertRaises(ValidationError):
            charge.sudo().write({"qty_requested": 0.5})

    def test_83_per_charge_detail_carries_the_split(self):
        from odoo.addons.yoya_emr_api.services.cashier_serializers import (
            serialize_cashier_charge_line,
        )

        self._set_mode("enforce")
        _, charge = self._visit(self.eligibility)
        self._allocate(charge, 1000.0, authorize=True)
        row = serialize_cashier_charge_line(charge.sudo())
        self.assertEqual(row["amount"], 1500.0)
        self.assertEqual(row["sponsor_authorized"], 1000.0)
        self.assertEqual(row["patient_responsibility"], 500.0)
        self.assertEqual(row["outstanding"], 500.0)
        self.assertEqual(row["responsibility_state"], "authorized")

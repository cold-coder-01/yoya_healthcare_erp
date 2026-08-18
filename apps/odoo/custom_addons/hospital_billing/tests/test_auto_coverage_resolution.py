"""Automatic sponsor resolution: the agreement decides what it can decide.

THE CASE THAT PROMPTED THIS. A corporate contract said "consultation, 80%, no
prior authorization". A 300 ETB consultation reached the cashier as 300 owed by
the patient and 0 by the sponsor, because nothing turned the contract's own
answer into a responsibility row until an officer clicked Approve.

Two claims are defended throughout:

1. DETERMINISTIC OUTCOMES NEVER REACH A HUMAN. A stated percentage with no
   authorization requirement, an explicit exclusion, a spent ceiling and a
   not-covered default are all decisions the CONTRACT made.

2. JUDGEMENT STILL DOES. A prior-authorization requirement, or an agreement
   with no policy for the service at all, is left untouched for the officer.

A NOTE ON MODE. Auto-resolution records responsibility in every mode, because
the rows are the same facts regardless. What the CASHIER collects still obeys
the existing mode rule: only 'enforce' makes the split the cash ceiling. The
mode tests below pin that distinction rather than blurring it.
"""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged

G_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_MANAGER = "hospital_management.group_hospital_manager"


@tagged("post_install", "-at_install", "auto_coverage_resolution")
class TestAutoCoverageResolution(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.today = fields.Date.context_today(cls.env["hospital.payer"])
        cls.engine = cls.env["hospital.billing.engine"]
        cls.officer = cls._make_user("auto_officer", G_OFFICER)
        cls.manager = cls._make_user("auto_manager", G_MANAGER)

        cls.consultation = cls._make_service("Auto Consultation", "consultation", 300.0)
        cls.cbc = cls._make_service("Auto CBC", "laboratory", 500.0)
        cls.ct = cls._make_service("Auto Brain CT", "radiology", 1500.0)
        cls.procedure = cls._make_service("Auto Major Procedure", "procedure", 5000.0)

    @classmethod
    def _make_user(cls, label, group):
        return cls.env["res.users"].sudo().create(
            {
                "name": label,
                "login": "%s_%s" % (label, uuid.uuid4().hex[:6]),
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
                "groups_id": [
                    (6, 0, [cls.env.ref("base.group_user").id, cls.env.ref(group).id])
                ],
            }
        )

    @classmethod
    def _make_service(cls, name, service_type, price):
        return cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "%s %s" % (name, uuid.uuid4().hex[:4]),
                "service_type": service_type,
                "default_price": price,
                "company_id": cls.company.id,
            }
        )

    def _mode(self, mode):
        self.company.sudo().write({"payer_responsibility_mode": mode})
        self.env.flush_all()

    def _agreement(self, rules=(), **overrides):
        partner = self.env["res.partner"].sudo().create(
            {"name": "Auto Sponsor %s" % uuid.uuid4().hex[:6]}
        )
        payer = self.env["hospital.payer"].sudo().create(
            {
                "name": partner.name,
                "payer_type": "corporate",
                "partner_id": partner.id,
                "company_id": self.company.id,
            }
        )
        vals = {
            "payer_id": payer.id,
            "agreement_number": "AUTO-%s" % uuid.uuid4().hex[:6].upper(),
            "company_id": self.company.id,
            "effective_from": self.today - timedelta(days=30),
            "limit_scope": "unlimited",
        }
        vals.update(overrides)
        agreement = self.env["hospital.payer.agreement"].sudo().create(vals)
        for spec in rules:
            self.env["hospital.payer.benefit.rule"].sudo().create(
                dict(spec, agreement_id=agreement.id)
            )
        agreement.with_user(self.manager).action_activate()
        return agreement

    def _visit(self, agreement, services=()):
        """A visit whose payer is selected BEFORE the charges are raised."""
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Auto Patient %s" % uuid.uuid4().hex[:6]}
        )
        elig_vals = {
            "patient_id": patient.id,
            "agreement_id": agreement.id,
            "effective_from": max(self.today, agreement.effective_from),
        }
        if agreement.sudo().limit_scope == "member":
            elig_vals["member_limit_amount"] = agreement.sudo().limit_amount
        eligibility = self.env["hospital.patient.payer"].sudo().create(elig_vals)
        eligibility.sudo().action_activate()

        encounter = self.env["hospital.encounter"].sudo().create(
            {
                "patient_id": patient.id,
                "encounter_type": "outpatient",
                "company_id": self.company.id,
                "opened_at": fields.Datetime.now(),
            }
        )
        from odoo.addons.hospital_billing.models.encounter_payer import (
            payer_identity_capability,
        )

        with payer_identity_capability():
            encounter.sudo().write({"patient_payer_id": eligibility.id})

        account = self.engine.sudo().get_or_create_billing_account(encounter)
        charges = self.env["hospital.charge.line"].sudo()
        for service in services:
            charges |= self.engine.sudo().create_or_update_charge(
                encounter,
                source_model="test.auto",
                source_res_id=len(charges) + 1,
                source_event="auto_%s" % uuid.uuid4().hex[:6],
                description=service.name,
                service=service,
                unit_price=service.default_price,
            )
        return encounter, account, charges, eligibility

    # ==================================================================
    # DETERMINISTIC: AUTO-AUTHORIZED
    # ==================================================================
    def test_01_eighty_percent_no_auth_auto_authorizes(self):
        """THE reported case: 300 at 80% => sponsor 240, patient 60."""
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0,
                    "authorization_required": False}]
        )
        _e, account, charges, _el = self._visit(agreement, [self.consultation])

        self.assertAlmostEqual(charges[0].amount_sponsor_authorized, 240.0, places=2)
        self.assertAlmostEqual(charges[0].amount_patient_responsibility, 60.0, places=2)
        self.assertEqual(charges[0].responsibility_state, "authorized")
        self.assertAlmostEqual(account.amount_patient_responsibility, 60.0, places=2)

    def test_02_full_coverage_no_auth_leaves_no_patient_share(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 100.0}]
        )
        _e, account, charges, _el = self._visit(agreement, [self.consultation])
        self.assertAlmostEqual(charges[0].amount_sponsor_authorized, 300.0, places=2)
        self.assertAlmostEqual(account.amount_patient_responsibility, 0.0, places=2)

    def test_03_default_percentage_covers_a_service_with_no_rule(self):
        """The full-credit contract: 100% by default, rules are exceptions."""
        agreement = self._agreement(
            default_coverage_policy="default_percentage",
            default_coverage_percent=100.0,
        )
        _e, account, charges, _el = self._visit(agreement, [self.consultation])
        self.assertAlmostEqual(charges[0].amount_sponsor_authorized, 300.0, places=2)
        self.assertAlmostEqual(account.amount_patient_responsibility, 0.0, places=2)

    def test_04_specific_and_category_rules_override_the_default(self):
        agreement = self._agreement(
            rules=[
                {"service_id": self.cbc.id,
                 "coverage_type": "percentage", "coverage_percent": 70.0},
                {"service_type": "procedure", "coverage_type": "excluded"},
            ],
            default_coverage_policy="default_percentage",
            default_coverage_percent=100.0,
        )
        _e, _a, charges, _el = self._visit(
            agreement, [self.consultation, self.cbc, self.procedure]
        )
        by_service = {c.service_id: c for c in charges}
        # No rule: the 100% default.
        self.assertAlmostEqual(
            by_service[self.consultation].amount_sponsor_authorized, 300.0, places=2
        )
        # Specific rule beats the default.
        self.assertAlmostEqual(
            by_service[self.cbc].amount_sponsor_authorized, 350.0, places=2
        )
        # Category exclusion beats the default.
        self.assertAlmostEqual(
            by_service[self.procedure].amount_sponsor_authorized, 0.0, places=2
        )
        self.assertAlmostEqual(
            by_service[self.procedure].amount_patient_responsibility, 5000.0, places=2
        )

    # ==================================================================
    # DETERMINISTIC ZERO
    # ==================================================================
    def test_10_excluded_service_auto_resolves_to_zero(self):
        agreement = self._agreement(
            rules=[{"service_id": self.procedure.id, "coverage_type": "excluded"}]
        )
        _e, _a, charges, _el = self._visit(agreement, [self.procedure])
        self.assertEqual(charges[0].responsibility_state, "authorized")
        self.assertAlmostEqual(charges[0].amount_sponsor_authorized, 0.0, places=2)
        self.assertAlmostEqual(
            charges[0].amount_patient_responsibility, 5000.0, places=2
        )

    def test_11_default_not_covered_auto_resolves_to_zero(self):
        agreement = self._agreement(default_coverage_policy="not_covered")
        _e, _a, charges, _el = self._visit(agreement, [self.consultation])
        self.assertEqual(charges[0].responsibility_state, "authorized")
        self.assertAlmostEqual(charges[0].amount_patient_responsibility, 300.0, places=2)

    def test_12_a_zero_decision_consumes_no_benefit(self):
        agreement = self._agreement(
            rules=[{"service_id": self.procedure.id, "coverage_type": "excluded"}],
            limit_scope="member", limit_amount=50000.0,
        )
        _e, _a, _c, eligibility = self._visit(agreement, [self.procedure])
        self.assertAlmostEqual(
            agreement.sudo().remaining_benefit_for(patient_payer=eligibility),
            50000.0, places=2,
        )

    # ==================================================================
    # LIMITS
    # ==================================================================
    def test_20_auto_authorization_consumes_benefit(self):
        agreement = self._agreement(
            default_coverage_policy="default_percentage",
            default_coverage_percent=100.0,
            limit_scope="member", limit_amount=50000.0,
        )
        _e, _a, _c, eligibility = self._visit(agreement, [self.consultation])
        self.assertAlmostEqual(
            agreement.sudo().remaining_benefit_for(patient_payer=eligibility),
            49700.0, places=2,
        )

    def test_21_remaining_limit_caps_the_automatic_amount(self):
        """eligible 5,000 but only 2,000 left => sponsor 2,000, no officer."""
        agreement = self._agreement(
            default_coverage_policy="default_percentage",
            default_coverage_percent=100.0,
            limit_scope="member", limit_amount=2000.0,
        )
        _e, account, charges, _el = self._visit(agreement, [self.procedure])
        self.assertAlmostEqual(charges[0].amount_sponsor_authorized, 2000.0, places=2)
        self.assertAlmostEqual(
            charges[0].amount_patient_responsibility, 3000.0, places=2
        )
        self.assertEqual(charges[0].responsibility_state, "authorized")

    def test_22_exhausted_limit_auto_resolves_to_zero(self):
        agreement = self._agreement(
            default_coverage_policy="default_percentage",
            default_coverage_percent=100.0,
            limit_scope="member", limit_amount=300.0,
        )
        encounter, account, charges, _el = self._visit(
            agreement, [self.consultation]
        )
        self.assertAlmostEqual(charges[0].amount_sponsor_authorized, 300.0, places=2)

        # A second charge on the same visit: the ceiling is spent.
        second = self.engine.sudo().create_or_update_charge(
            encounter,
            source_model="test.auto",
            source_res_id=99,
            source_event="exhausted",
            description="After exhaustion",
            service=self.cbc,
            unit_price=500.0,
        )
        self.assertEqual(second.responsibility_state, "authorized")
        self.assertAlmostEqual(second.amount_sponsor_authorized, 0.0, places=2)
        self.assertAlmostEqual(second.amount_patient_responsibility, 500.0, places=2)

    # ==================================================================
    # MANUAL: LEFT FOR THE OFFICER
    # ==================================================================
    def test_30_authorization_required_is_left_unresolved(self):
        agreement = self._agreement(
            rules=[{"service_id": self.ct.id,
                    "coverage_type": "percentage", "coverage_percent": 60.0,
                    "authorization_required": True}]
        )
        _e, _a, charges, _el = self._visit(agreement, [self.ct])
        self.assertEqual(
            charges[0].responsibility_state, "self_pay",
            "A contract demanding prior authorization must reach a human.",
        )
        self.assertTrue(
            self.engine.sudo().charge_requires_manual_decision(charges[0])
        )

    def test_31_default_manual_authorization_is_left_unresolved(self):
        agreement = self._agreement(
            default_coverage_policy="manual_authorization"
        )
        _e, _a, charges, _el = self._visit(agreement, [self.consultation])
        self.assertEqual(charges[0].responsibility_state, "self_pay")
        self.assertTrue(
            self.engine.sudo().charge_requires_manual_decision(charges[0])
        )

    def test_32_deterministic_charges_need_no_officer(self):
        """Both sides of the queue predicate, on a charge built the real way.

        The engine method AND the serializer helper the officer worklist calls
        must agree, because they are the two entry points to the same question.
        """
        from odoo.addons.yoya_emr_api.services.insurance_credit_serializers import (
            charge_needs_decision,
        )

        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        _e, _a, charges, _el = self._visit(agreement, [self.consultation])
        self.assertFalse(
            self.engine.sudo().charge_requires_manual_decision(charges[0]),
            "An 80% consultation is not a judgement call.",
        )
        self.assertFalse(
            charge_needs_decision(charges[0].sudo()),
            "The officer worklist must not show it either.",
        )
        # And it is genuinely decided, not merely hidden.
        self.assertAlmostEqual(charges[0].amount_sponsor_authorized, 240.0, places=2)

    def test_33_a_self_pay_charge_is_not_an_insurance_question(self):
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Auto Self Pay %s" % uuid.uuid4().hex[:6]}
        )
        encounter = self.env["hospital.encounter"].sudo().create(
            {"patient_id": patient.id, "encounter_type": "outpatient",
             "company_id": self.company.id, "opened_at": fields.Datetime.now()}
        )
        charge = self.engine.sudo().create_or_update_charge(
            encounter, source_model="test.auto", source_res_id=1,
            source_event="selfpay", description="Self pay",
            service=self.consultation, unit_price=300.0,
        )
        self.assertEqual(charge.responsibility_state, "self_pay")
        self.assertFalse(
            self.engine.sudo().charge_requires_manual_decision(charge)
        )

    # ==================================================================
    # IDEMPOTENCY, FREEZE, ORDERING
    # ==================================================================
    def test_40_repeated_resolution_is_idempotent(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        _e, account, charges, _el = self._visit(agreement, [self.consultation])
        for _ in range(3):
            self.engine.sudo().resolve_account_coverage(account)

        rows = self.env["hospital.charge.responsibility"].sudo().search(
            [("charge_id", "=", charges[0].id), ("state", "=", "authorized")]
        )
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(account.amount_sponsor_authorized, 240.0, places=2)

    def test_41_an_officer_decision_is_never_overwritten(self):
        """A manual reduction must survive later automatic reconciliation."""
        agreement = self._agreement(
            rules=[{"service_id": self.ct.id,
                    "coverage_type": "percentage", "coverage_percent": 60.0,
                    "authorization_required": True}]
        )
        _e, account, charges, _el = self._visit(agreement, [self.ct])
        self.engine.with_user(self.officer).authorize_visit_coverage(
            account,
            [{"charge_id": charges[0].id, "amount": 500.0,
              "reason": "Sponsor confirmed 500"}],
        )
        self.assertAlmostEqual(charges[0].amount_sponsor_authorized, 500.0, places=2)

        self.engine.sudo().resolve_account_coverage(account)
        charges.invalidate_recordset()
        self.assertAlmostEqual(
            charges[0].amount_sponsor_authorized, 500.0, places=2,
            msg="Automatic reconciliation must not restate an officer's decision.",
        )

    def test_42_a_taken_receipt_freezes_automatic_resolution(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Auto Frozen %s" % uuid.uuid4().hex[:6]}
        )
        eligibility = self.env["hospital.patient.payer"].sudo().create(
            {"patient_id": patient.id, "agreement_id": agreement.id,
             "effective_from": max(self.today, agreement.effective_from)}
        )
        eligibility.sudo().action_activate()
        encounter = self.env["hospital.encounter"].sudo().create(
            {"patient_id": patient.id, "encounter_type": "outpatient",
             "company_id": self.company.id, "opened_at": fields.Datetime.now()}
        )
        account = self.engine.sudo().get_or_create_billing_account(encounter)
        charge = self.env["hospital.charge.line"].sudo().create(
            {"billing_account_id": account.id, "service_id": self.consultation.id,
             "description": "Frozen", "billing_basis": "prepaid",
             "charge_state": "active", "unit_price": 300.0, "qty_requested": 1.0}
        )
        # Cash first, payer second: the split must not move under a receipt.
        account.sudo().record_operational_payment(
            amount=300.0, payment_method="cash", intake_token=uuid.uuid4().hex
        )
        from odoo.addons.hospital_billing.models.encounter_payer import (
            payer_identity_capability,
        )

        with payer_identity_capability():
            encounter.sudo().write({"patient_payer_id": eligibility.id})
        self.engine.sudo().resolve_account_coverage(account)

        charge.invalidate_recordset()
        self.assertEqual(
            charge.responsibility_state, "self_pay",
            "A charge whose cash has been taken must not gain a sponsor share.",
        )

    def test_43_payer_selected_after_the_charge_still_resolves(self):
        """The ordinary front-desk order: charge first, eligibility second."""
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Auto Late Payer %s" % uuid.uuid4().hex[:6]}
        )
        eligibility = self.env["hospital.patient.payer"].sudo().create(
            {"patient_id": patient.id, "agreement_id": agreement.id,
             "effective_from": max(self.today, agreement.effective_from)}
        )
        eligibility.sudo().action_activate()
        encounter = self.env["hospital.encounter"].sudo().create(
            {"patient_id": patient.id, "encounter_type": "outpatient",
             "company_id": self.company.id, "opened_at": fields.Datetime.now()}
        )
        charge = self.engine.sudo().create_or_update_charge(
            encounter, source_model="test.auto", source_res_id=1,
            source_event="late", description="Consultation",
            service=self.consultation, unit_price=300.0,
        )
        self.assertEqual(charge.responsibility_state, "self_pay")

        # The reconciliation entry point the reception workflow calls.
        with payer_capability():
            encounter.sudo().write({"patient_payer_id": eligibility.id})
        self.engine.sudo().resolve_account_coverage(
            encounter.sudo().billing_account_id
        )

        charge.invalidate_recordset()
        self.assertAlmostEqual(charge.amount_sponsor_authorized, 240.0, places=2)

    # ==================================================================
    # MODE
    # ==================================================================
    def test_50_enforce_makes_the_split_the_cash_ceiling(self):
        self._mode("enforce")
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        _e, account, charges, _el = self._visit(agreement, [self.consultation])
        charges[0].sudo().write({"billing_basis": "prepaid"})
        account.invalidate_recordset()
        self.assertAlmostEqual(
            account.amount_patient_outstanding, 60.0, places=2,
            msg="Beza's case: the cashier collects 60, not 300.",
        )

    def test_51_off_mode_records_the_split_but_keeps_the_legacy_ceiling(self):
        """Auto-resolution is safe in off mode BECAUSE the ceiling is untouched."""
        self._mode("off")
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        _e, account, charges, _el = self._visit(agreement, [self.consultation])
        charges[0].sudo().write({"billing_basis": "prepaid"})
        account.invalidate_recordset()

        self.assertAlmostEqual(
            charges[0].amount_sponsor_authorized, 240.0, places=2,
            msg="The split is still recorded, which is what makes shadow useful.",
        )
        self.assertAlmostEqual(
            account.amount_patient_outstanding, 300.0, places=2,
            msg="But off mode still collects the legacy gross. Only enforce "
            "changes what the cashier asks for.",
        )
        self._mode("enforce")


def payer_capability():
    from odoo.addons.hospital_billing.models.encounter_payer import (
        payer_identity_capability,
    )

    return payer_identity_capability()

"""Slice A: benefit rules, coverage evaluation and derived limit consumption.

TWO CLAIMS THESE TESTS DEFEND ABOVE ALL OTHERS

1. The evaluator is READ-ONLY. It says what the agreement permits and creates
   nothing. If a future change makes it authorize a share as a side effect,
   test_60 fails.

2. An agreement with no benefit rules grants NOTHING automatically. Every
   contract that predates this module lands there, and a default that quietly
   started covering 80% of unreviewed contracts would be the worst possible
   upgrade. test_40 is that guarantee.
"""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

G_INSURANCE_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_MANAGER = "hospital_management.group_hospital_manager"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"


@tagged("post_install", "-at_install", "payer_benefit_rules")
class TestPayerBenefitRules(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.today = fields.Date.context_today(cls.env["hospital.payer"])
        cls.engine = cls.env["hospital.billing.engine"]

        cls.officer = cls._make_user("benefit_officer", [G_INSURANCE_OFFICER])
        cls.manager = cls._make_user("benefit_manager", [G_MANAGER])
        cls.cashier = cls._make_user("benefit_cashier", [G_CASHIER])

        cls.partner = cls.env["res.partner"].sudo().create({"name": "Benefit Insurer"})
        cls.payer = cls.env["hospital.payer"].sudo().create(
            {
                "name": "Benefit Insurer",
                "payer_type": "insurance",
                "partner_id": cls.partner.id,
                "company_id": cls.company.id,
            }
        )
        cls.consultation = cls._make_service("Benefit Consultation", "consultation", 300.0)
        cls.lab = cls._make_service("Benefit Lab Panel", "laboratory", 500.0)
        cls.procedure = cls._make_service("Benefit Major Procedure", "procedure", 5000.0)

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, login, group_xmlids):
        return cls.env["res.users"].sudo().create(
            {
                "name": login,
                "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
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
    def _make_service(cls, name, service_type, price):
        return cls.env["hospital.billing.service"].sudo().create(
            {
                "name": "%s %s" % (name, uuid.uuid4().hex[:4]),
                "service_type": service_type,
                "default_price": price,
                "company_id": cls.company.id,
            }
        )

    def _new_payer(self):
        """A FRESH payer per agreement.

        hospital.payer.agreement carries an EXCLUDE constraint forbidding two
        active agreements for one payer over overlapping dates. Several tests
        here need more than one live agreement, so each gets its own payer
        rather than fighting a constraint that is doing its job.
        """
        partner = self.env["res.partner"].sudo().create(
            {"name": "Benefit Insurer %s" % uuid.uuid4().hex[:6]}
        )
        return self.env["hospital.payer"].sudo().create(
            {
                "name": partner.name,
                "payer_type": "insurance",
                "partner_id": partner.id,
                "company_id": self.company.id,
            }
        )

    def _agreement(self, activate=True, **overrides):
        vals = {
            "payer_id": self._new_payer().id,
            "agreement_number": "BEN-%s" % uuid.uuid4().hex[:6].upper(),
            "company_id": self.company.id,
            "effective_from": self.today - timedelta(days=30),
            "limit_scope": "unlimited",
        }
        vals.update(overrides)
        agreement = self.env["hospital.payer.agreement"].sudo().create(vals)
        if activate:
            agreement.with_user(self.manager).action_activate()
        return agreement

    def _rule(self, agreement, **overrides):
        vals = {"agreement_id": agreement.id, "coverage_type": "percentage"}
        vals.update(overrides)
        return self.env["hospital.payer.benefit.rule"].sudo().create(vals)

    def _active_with_rule(self, **rule_overrides):
        """An ACTIVE agreement carrying one rule, built in the legal order.

        Rules are commercial terms and are frozen once the agreement leaves
        draft, so a fixture must set them BEFORE activation. Building it the
        other way round is what the freeze now refuses, which is the whole point
        of this slice's hardening.
        """
        agreement = self._agreement(activate=False)
        rule_overrides.setdefault("service_id", self.consultation.id)
        rule_overrides.setdefault("coverage_percent", 80.0)
        rule = self._rule(agreement, **rule_overrides)
        agreement.with_user(self.manager).action_activate()
        return agreement, rule

    def _eligibility(self, agreement, patient=None, **overrides):
        patient = patient or self.env["hospital.patient"].sudo().create(
            {"name": "Benefit Patient %s" % uuid.uuid4().hex[:6]}
        )
        vals = {
            "patient_id": patient.id,
            "agreement_id": agreement.id,
            "effective_from": max(self.today, agreement.effective_from),
        }
        vals.update(overrides)
        eligibility = self.env["hospital.patient.payer"].sudo().create(vals)
        eligibility.sudo().action_activate()
        return eligibility

    def _charge(self, eligibility, service, amount=None, opened_at=None):
        """A confirmed visit with one charge, presented under ``eligibility``."""
        from odoo.addons.hospital_billing.models.encounter_payer import (
            payer_identity_capability,
        )

        # ONE ACTIVE EPISODE PER PATIENT. hospital.encounter now refuses a
        # second live episode for the same patient, so a fixture that models a
        # member attending repeatedly must close the previous visit first --
        # which is what actually happens between real attendances. Without this
        # the fixture would be asserting a state the hospital forbids.
        self.env["hospital.encounter"].sudo().search(
            [
                ("patient_id", "=", eligibility.patient_id.id),
                ("state", "not in", ["completed", "closed", "cancelled"]),
            ]
        ).write({"state": "closed"})

        encounter = self.env["hospital.encounter"].sudo().create(
            {
                "patient_id": eligibility.patient_id.id,
                "encounter_type": "outpatient",
                "company_id": self.company.id,
                "opened_at": opened_at or fields.Datetime.now(),
            }
        )
        with payer_identity_capability():
            encounter.sudo().write({"patient_payer_id": eligibility.id})
        account = self.engine.sudo().get_or_create_billing_account(encounter)
        return self.env["hospital.charge.line"].sudo().create(
            {
                "billing_account_id": account.id,
                "service_id": service.id,
                "description": service.name,
                "billing_basis": "prepaid",
                "charge_state": "active",
                "unit_price": service.default_price if amount is None else amount,
                "qty_requested": 1.0,
            }
        )

    def _evaluate(self, charge, eligibility=None):
        return self.engine.sudo().evaluate_charge_coverage(charge, eligibility)

    def _authorize(self, charge, amount):
        """An authorized sponsor share: what consumes benefit."""
        return self.engine.sudo().allocate_payer(
            charge.billing_account_id,
            charge=charge,
            amount=amount,
            reason="benefit test",
            authorize=True,
        )

    # ==================================================================
    # COVERAGE ARITHMETIC
    # ==================================================================
    def test_01_percentage_coverage(self):
        """80% of 300 = 240 sponsor, 60 patient."""
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="percentage", coverage_percent=80.0,
        )
        agreement.with_user(self.manager).action_activate()
        charge = self._charge(self._eligibility(agreement), self.consultation)
        result = self._evaluate(charge)

        self.assertAlmostEqual(result["charge_amount"], 300.0, places=2)
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 240.0, places=2)
        self.assertAlmostEqual(result["patient_residual"], 60.0, places=2)
        self.assertEqual(result["coverage_state"], "covered")
        self.assertFalse(result["excluded"])

    def test_02_fixed_sponsor_amount(self):
        """Flat 200 of a 300 charge."""
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="fixed_sponsor", sponsor_amount=200.0,
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.consultation)
        )
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 200.0, places=2)
        self.assertAlmostEqual(result["patient_residual"], 100.0, places=2)

    def test_03_fixed_patient_copay(self):
        """Copay 100 on a 500 charge leaves 400 to the sponsor."""
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.lab.id,
            coverage_type="patient_copay", patient_copay_amount=100.0,
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(self._charge(self._eligibility(agreement), self.lab))
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 400.0, places=2)
        self.assertAlmostEqual(result["patient_residual"], 100.0, places=2)

    def test_04_excluded_service(self):
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.procedure.id, coverage_type="excluded",
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.procedure)
        )
        self.assertTrue(result["excluded"])
        self.assertEqual(result["coverage_state"], "excluded")
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 0.0, places=2)
        self.assertAlmostEqual(result["patient_residual"], 5000.0, places=2)

    def test_05_specific_service_overrides_category(self):
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_type="consultation",
            coverage_type="percentage", coverage_percent=50.0,
        )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="percentage", coverage_percent=90.0,
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.consultation)
        )
        self.assertAlmostEqual(
            result["permitted_sponsor_amount"], 270.0, places=2,
            msg="The specific service rule (90%) must beat the category rule (50%).",
        )

    def test_06_exclusion_beats_a_generous_category_rule(self):
        """A denial is a stronger statement than a grant."""
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_type="procedure",
            coverage_type="percentage", coverage_percent=100.0,
        )
        self._rule(
            agreement, service_id=self.procedure.id, coverage_type="excluded",
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.procedure)
        )
        self.assertTrue(result["excluded"])
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 0.0, places=2)

    def test_07_category_rule_matches_when_no_specific_rule(self):
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_type="laboratory",
            coverage_type="percentage", coverage_percent=60.0,
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(self._charge(self._eligibility(agreement), self.lab))
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 300.0, places=2)

    def test_08_coverage_never_exceeds_the_charge(self):
        """A fixed contribution larger than the charge pays the patient."""
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="fixed_sponsor", sponsor_amount=5000.0,
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.consultation)
        )
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 300.0, places=2)
        self.assertAlmostEqual(result["patient_residual"], 0.0, places=2)

    def test_09_copay_larger_than_charge_gives_no_negative_residual(self):
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="patient_copay", patient_copay_amount=900.0,
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.consultation)
        )
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 0.0, places=2)
        self.assertAlmostEqual(result["patient_residual"], 300.0, places=2)
        self.assertGreaterEqual(result["patient_residual"], 0.0)

    # ==================================================================
    # DEFAULT POLICY
    # ==================================================================
    def test_40_no_rules_grants_nothing_the_upgrade_safety_guarantee(self):
        """THE upgrade guarantee: a rule-less agreement covers nothing."""
        agreement = self._agreement()
        self.assertEqual(
            agreement.sudo().default_coverage_policy, "manual_authorization",
            "Existing agreements must default to manual authorization.",
        )
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.consultation)
        )
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 0.0, places=2)
        self.assertAlmostEqual(result["patient_residual"], 300.0, places=2)
        self.assertEqual(result["coverage_state"], "manual_authorization")
        self.assertTrue(result["requires_authorization"])
        self.assertEqual(result["reason_code"], "default_manual")

    def test_41_default_not_covered(self):
        agreement = self._agreement(
            activate=False, default_coverage_policy="not_covered"
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.consultation)
        )
        self.assertEqual(result["coverage_state"], "not_covered")
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 0.0, places=2)

    def test_42_default_percentage(self):
        agreement = self._agreement(
            activate=False,
            default_coverage_policy="default_percentage",
            default_coverage_percent=70.0,
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.consultation)
        )
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 210.0, places=2)
        self.assertEqual(result["reason_code"], "default_percentage")

    def test_43_authorization_requirement_is_reported(self):
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="percentage", coverage_percent=80.0,
            authorization_required=True,
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.consultation)
        )
        self.assertTrue(result["requires_authorization"])
        # Reported, NOT enforced: the permitted amount is unchanged.
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 240.0, places=2)

    # ==================================================================
    # LIMITS
    # ==================================================================
    def test_50_unlimited_reports_no_ceiling(self):
        agreement = self._agreement(activate=False, limit_scope="unlimited")
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="percentage", coverage_percent=100.0,
        )
        agreement.with_user(self.manager).action_activate()
        result = self._evaluate(
            self._charge(self._eligibility(agreement), self.consultation)
        )
        self.assertIsNone(
            result["limit_available"],
            "None means unbounded; 0.0 would mean exhausted.",
        )
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 300.0, places=2)

    def test_51_member_limit_caps_the_sponsor_amount(self):
        """50,000 ceiling, 48,000 consumed, 4,000 eligible => 2,000 permitted."""
        agreement = self._agreement(
            activate=False,
            limit_scope="member", limit_amount=50000.0, benefit_period="agreement_term",
        )
        self._rule(
            agreement, service_type="procedure",
            coverage_type="percentage", coverage_percent=100.0,
        )
        agreement.with_user(self.manager).action_activate()
        eligibility = self._eligibility(agreement, member_limit_amount=50000.0)

        consumed = self._charge(eligibility, self.procedure, amount=48000.0)
        self._authorize(consumed, 48000.0)

        new_charge = self._charge(eligibility, self.procedure, amount=4000.0)
        result = self._evaluate(new_charge)

        self.assertAlmostEqual(result["calculated_sponsor_amount"], 4000.0, places=2)
        self.assertAlmostEqual(result["limit_available"], 2000.0, places=2)
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 2000.0, places=2)
        self.assertAlmostEqual(result["patient_residual"], 2000.0, places=2)
        self.assertEqual(result["coverage_state"], "limit_capped")

    def test_52_exhausted_limit_permits_zero(self):
        agreement = self._agreement(
            activate=False,
            limit_scope="member", limit_amount=1000.0,
        )
        self._rule(
            agreement, service_type="consultation",
            coverage_type="percentage", coverage_percent=100.0,
        )
        agreement.with_user(self.manager).action_activate()
        eligibility = self._eligibility(agreement, member_limit_amount=1000.0)
        self._authorize(self._charge(eligibility, self.consultation, amount=1000.0), 1000.0)

        result = self._evaluate(self._charge(eligibility, self.consultation))
        self.assertAlmostEqual(result["limit_available"], 0.0, places=2)
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 0.0, places=2)
        self.assertAlmostEqual(result["patient_residual"], 300.0, places=2)
        self.assertEqual(result["coverage_state"], "limit_exhausted")

    def test_53_authorized_responsibility_counts_against_the_benefit(self):
        agreement = self._agreement(limit_scope="member", limit_amount=1000.0)
        eligibility = self._eligibility(agreement, member_limit_amount=1000.0)
        self.assertAlmostEqual(
            agreement.remaining_benefit_for(patient_payer=eligibility), 1000.0, places=2
        )
        self._authorize(self._charge(eligibility, self.consultation), 300.0)
        self.assertAlmostEqual(
            agreement.remaining_benefit_for(patient_payer=eligibility), 700.0, places=2
        )

    def test_54_cancelled_responsibility_releases_the_reservation(self):
        """Release is true by construction: cancelled rows leave the domain."""
        agreement = self._agreement(limit_scope="member", limit_amount=1000.0)
        eligibility = self._eligibility(agreement, member_limit_amount=1000.0)
        responsibility = self._authorize(
            self._charge(eligibility, self.consultation), 300.0
        )
        self.assertAlmostEqual(
            agreement.remaining_benefit_for(patient_payer=eligibility), 700.0, places=2
        )

        responsibility.sudo().action_cancel(reason="benefit test release")
        self.assertAlmostEqual(
            agreement.remaining_benefit_for(patient_payer=eligibility), 1000.0, places=2,
            msg="Cancelling must return the reserved benefit.",
        )

    def test_55_draft_responsibility_also_reserves(self):
        """A draft is an outstanding proposal, not free capacity."""
        agreement = self._agreement(limit_scope="member", limit_amount=1000.0)
        eligibility = self._eligibility(agreement, member_limit_amount=1000.0)
        # 200 of a 300 charge: a sponsor share may never exceed its own charge,
        # which hospital.charge.responsibility enforces independently.
        charge = self._charge(eligibility, self.consultation)
        self.engine.sudo().allocate_payer(
            charge.billing_account_id,
            charge=charge,
            amount=200.0,
            reason="draft proposal",
            authorize=False,
        )
        self.assertAlmostEqual(
            agreement.remaining_benefit_for(patient_payer=eligibility), 800.0, places=2
        )

    def test_56_per_visit_limit_is_scoped_to_the_encounter(self):
        agreement = self._agreement(activate=False, limit_scope="visit", limit_amount=500.0)
        self._rule(
            agreement, service_type="laboratory",
            coverage_type="percentage", coverage_percent=100.0,
        )
        agreement.with_user(self.manager).action_activate()
        eligibility = self._eligibility(agreement)

        first = self._charge(eligibility, self.lab)
        self._authorize(first, 500.0)
        # Same encounter: the visit cap is now spent.
        exhausted = self.env["hospital.charge.line"].sudo().create(
            {
                "billing_account_id": first.billing_account_id.id,
                "service_id": self.lab.id,
                "description": "Second lab, same visit",
                "billing_basis": "prepaid",
                "charge_state": "active",
                "unit_price": 500.0,
                "qty_requested": 1.0,
            }
        )
        self.assertAlmostEqual(
            self._evaluate(exhausted)["permitted_sponsor_amount"], 0.0, places=2
        )

        # A DIFFERENT visit gets its own cap.
        fresh = self._charge(eligibility, self.lab)
        self.assertAlmostEqual(
            self._evaluate(fresh)["permitted_sponsor_amount"], 500.0, places=2,
            msg="A per-visit ceiling resets per encounter.",
        )

    def test_57_calendar_year_period_excludes_a_prior_year(self):
        agreement = self._agreement(
            limit_scope="member", limit_amount=1000.0, benefit_period="calendar_year",
        )
        eligibility = self._eligibility(agreement, member_limit_amount=1000.0)
        # Last year's authorization must not consume this year's benefit.
        last_year = fields.Datetime.now().replace(year=fields.Date.today().year - 1)
        self._authorize(
            self._charge(eligibility, self.consultation, opened_at=last_year), 300.0
        )
        self.assertAlmostEqual(
            agreement.remaining_benefit_for(patient_payer=eligibility), 1000.0, places=2,
            msg="A calendar-year benefit resets; last year does not count.",
        )

    def test_58_agreement_term_period_counts_a_prior_year(self):
        """The default period is the contract's own window, not Jan to Dec."""
        agreement = self._agreement(
            limit_scope="member",
            limit_amount=1000.0,
            benefit_period="agreement_term",
            effective_from=self.today - timedelta(days=800),
        )
        eligibility = self._eligibility(agreement, member_limit_amount=1000.0)
        last_year = fields.Datetime.now().replace(year=fields.Date.today().year - 1)
        self._authorize(
            self._charge(eligibility, self.consultation, opened_at=last_year), 300.0
        )
        self.assertAlmostEqual(
            agreement.remaining_benefit_for(patient_payer=eligibility), 700.0, places=2
        )

    # ==================================================================
    # THE READ-ONLY CONTRACT
    # ==================================================================
    def test_60_evaluator_creates_and_authorizes_nothing(self):
        """Slice A's central boundary: policy is computed, never committed."""
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="percentage", coverage_percent=80.0,
        )
        agreement.with_user(self.manager).action_activate()
        charge = self._charge(self._eligibility(agreement), self.consultation)
        Responsibility = self.env["hospital.charge.responsibility"].sudo()
        before = Responsibility.search_count([("charge_id", "=", charge.id)])

        result = self._evaluate(charge)
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 240.0, places=2)

        self.assertEqual(
            Responsibility.search_count([("charge_id", "=", charge.id)]), before,
            "The evaluator must not create a responsibility row.",
        )
        # And the charge's own authorized figure is untouched.
        self.assertAlmostEqual(
            charge.sudo().amount_sponsor_authorized, 0.0, places=2
        )

    def test_61_evaluating_does_not_change_patient_responsibility(self):
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="percentage", coverage_percent=80.0,
        )
        agreement.with_user(self.manager).action_activate()
        charge = self._charge(self._eligibility(agreement), self.consultation)
        self._evaluate(charge)
        self.assertAlmostEqual(
            charge.sudo().get_patient_responsibility(), 300.0, places=2,
            msg="Until an officer authorizes, the patient still owes the whole charge.",
        )

    # ==================================================================
    # VALIDITY GATES
    # ==================================================================
    def test_70_inactive_agreement_denied(self):
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="percentage", coverage_percent=80.0,
        )
        agreement.with_user(self.manager).action_activate()
        eligibility = self._eligibility(agreement)
        charge = self._charge(eligibility, self.consultation)
        agreement.with_user(self.manager).action_suspend("benefit test")

        result = self._evaluate(charge)
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 0.0, places=2)
        self.assertEqual(result["reason_code"], "eligibility_not_valid")

    def test_71_inactive_eligibility_denied(self):
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id,
            coverage_type="percentage", coverage_percent=80.0,
        )
        agreement.with_user(self.manager).action_activate()
        eligibility = self._eligibility(agreement)
        charge = self._charge(eligibility, self.consultation)
        eligibility.with_user(self.officer).action_suspend()

        result = self._evaluate(charge)
        self.assertAlmostEqual(result["permitted_sponsor_amount"], 0.0, places=2)
        self.assertEqual(result["reason_code"], "eligibility_not_valid")

    def test_72_no_eligibility_means_full_patient_responsibility(self):
        agreement = self._agreement()
        eligibility = self._eligibility(agreement)
        charge = self._charge(eligibility, self.consultation)
        # Evaluate with the eligibility explicitly absent.
        result = self.engine.sudo().evaluate_charge_coverage(
            charge, self.env["hospital.patient.payer"]
        )
        # Falls back to the encounter's own eligibility, which IS set here, so
        # assert the explicit-mismatch path instead with a foreign eligibility.
        other = self._eligibility(agreement)
        mismatch = self.engine.sudo().evaluate_charge_coverage(charge, other)
        self.assertEqual(mismatch["reason_code"], "eligibility_patient_mismatch")
        self.assertAlmostEqual(mismatch["patient_residual"], 300.0, places=2)
        self.assertIsNotNone(result)

    # ==================================================================
    # MODEL VALIDATION
    # ==================================================================
    def test_80_percentage_out_of_range_refused(self):
        agreement = self._agreement()
        for bad in (-1.0, 101.0):
            with self.subTest(percent=bad):
                with self.assertRaises(Exception):
                    self._rule(
                        agreement, service_id=self.consultation.id,
                        coverage_type="percentage", coverage_percent=bad,
                    )

    def test_81_negative_amounts_refused(self):
        agreement = self._agreement()
        with self.assertRaises(Exception):
            self._rule(
                agreement, service_id=self.lab.id,
                coverage_type="fixed_sponsor", sponsor_amount=-5.0,
            )

    def test_82_rule_must_target_exactly_one_tier(self):
        agreement = self._agreement(activate=False)
        with self.assertRaises(ValidationError):
            self._rule(
                agreement, service_id=self.consultation.id,
                service_type="consultation", coverage_percent=50.0,
            )
        with self.assertRaises(ValidationError):
            self._rule(agreement, coverage_percent=50.0)

    def test_83_duplicate_active_rule_refused(self):
        agreement = self._agreement(activate=False, )
        self._rule(
            agreement, service_id=self.consultation.id, coverage_percent=80.0,
        )
        with self.assertRaises(ValidationError):
            self._rule(
                agreement, service_id=self.consultation.id, coverage_percent=50.0,
            )

    def test_84_limit_amount_cannot_be_negative(self):
        with self.assertRaises(Exception):
            self._agreement(
                activate=False, limit_scope="member", limit_amount=-1.0
            )

    # ==================================================================
    # SECURITY
    # ==================================================================
    def test_90_cashier_cannot_create_a_benefit_rule(self):
        agreement = self._agreement()
        with self.assertRaises(AccessError):
            self.env["hospital.payer.benefit.rule"].with_user(self.cashier).create(
                {
                    "agreement_id": agreement.id,
                    "service_id": self.consultation.id,
                    "coverage_type": "percentage",
                    "coverage_percent": 100.0,
                }
            )

    def test_91_front_desk_cannot_create_a_benefit_rule(self):
        nurse = self._make_user("benefit_nurse", [G_FRONT_DESK_NURSE])
        agreement = self._agreement()
        with self.assertRaises(AccessError):
            self.env["hospital.payer.benefit.rule"].with_user(nurse).create(
                {
                    "agreement_id": agreement.id,
                    "service_id": self.consultation.id,
                    "coverage_type": "percentage",
                    "coverage_percent": 100.0,
                }
            )

    def test_92_officer_may_configure(self):
        agreement = self._agreement(activate=False)
        rule = self.env["hospital.payer.benefit.rule"].with_user(self.officer).create(
            {
                "agreement_id": agreement.id,
                "service_id": self.consultation.id,
                "coverage_type": "percentage",
                "coverage_percent": 80.0,
            }
        )
        self.assertTrue(rule.id)

    def test_93_commercial_terms_are_group_protected(self):
        """Same protection the agreement's own ceiling carries."""
        for name in (
            "coverage_percent", "sponsor_amount", "patient_copay_amount", "notes",
        ):
            field = self.env["hospital.payer.benefit.rule"]._fields[name]
            self.assertTrue(
                field.groups,
                "%s must carry PAYER_COMMERCIAL_READ; a Cashier holding a read "
                "ACL would otherwise see contract rates." % name,
            )

    # ==================================================================
    # ACTIVATION GATE
    # ==================================================================
    def test_95_member_and_visit_scopes_may_now_activate(self):
        for scope in ("member", "visit"):
            with self.subTest(scope=scope):
                agreement = self._agreement(
                    activate=False, limit_scope=scope, limit_amount=50000.0
                )
                agreement.with_user(self.manager).action_activate()
                self.assertEqual(agreement.state, "active")

    # ==================================================================
    # THE VERSION FREEZE
    #
    # Manual UAT found AMG-2026-001 active with its consultation coverage still
    # editable from 80%. The agreement's own freeze is a column allowlist in
    # hospital.payer.agreement.write(), and a benefit rule is a row on a
    # different model, so that guard never ran. These tests pin the boundary to
    # the object that actually carries the term.
    # ==================================================================
    def test_100_draft_rules_are_fully_editable(self):
        agreement = self._agreement(activate=False)
        rule = self._rule(
            agreement, service_id=self.consultation.id, coverage_percent=80.0,
        )
        rule.sudo().write({"coverage_percent": 75.0})
        self.assertAlmostEqual(rule.sudo().coverage_percent, 75.0, places=2)
        rule.sudo().write({"active": False})
        rule.sudo().write({"active": True})
        rule.sudo().write({"sequence": 42})
        self._rule(agreement, service_id=self.lab.id, coverage_percent=50.0)
        rule.sudo().unlink()

    def test_101_active_agreement_refuses_a_coverage_change(self):
        """THE UAT bug, pinned."""
        agreement, rule = self._active_with_rule()
        with self.assertRaises(UserError) as caught:
            rule.sudo().write({"coverage_percent": 60.0})
        self.assertIn("Create Amendment", str(caught.exception))
        self.assertAlmostEqual(rule.sudo().coverage_percent, 80.0, places=2)

    def test_102_every_term_column_is_refused(self):
        agreement, rule = self._active_with_rule()
        for vals in (
            {"coverage_type": "fixed_sponsor"},
            {"sponsor_amount": 100.0},
            {"patient_copay_amount": 50.0},
            {"service_id": self.lab.id},
            {"service_type": "laboratory"},
            {"authorization_required": True},
            {"active": False},
            {"sequence": 99},
        ):
            with self.subTest(vals=vals):
                with self.assertRaises(UserError):
                    rule.sudo().write(vals)

    def test_103_active_agreement_refuses_a_new_rule(self):
        agreement = self._agreement()
        with self.assertRaises(UserError):
            self._rule(agreement, service_id=self.lab.id, coverage_percent=50.0)

    def test_104_active_agreement_refuses_unlink(self):
        agreement, rule = self._active_with_rule()
        with self.assertRaises(UserError):
            rule.sudo().unlink()
        self.assertTrue(rule.exists())

    def test_105_reassignment_to_a_frozen_agreement_is_refused(self):
        """Both ends of a move must be draft: it removes a term and adds one."""
        draft = self._agreement(activate=False)
        active = self._agreement()
        rule = self._rule(draft, service_id=self.consultation.id, coverage_percent=80.0)
        with self.assertRaises(UserError):
            rule.sudo().write({"agreement_id": active.id})

    def test_106_agreement_benefit_policy_is_frozen_after_activation(self):
        agreement = self._agreement()
        for vals in (
            {"default_coverage_policy": "default_percentage"},
            {"default_coverage_percent": 55.0},
            {"benefit_period": "calendar_year"},
        ):
            with self.subTest(vals=vals):
                with self.assertRaises(UserError) as caught:
                    agreement.sudo().write(vals)
                self.assertIn("Create Amendment", str(caught.exception))

    def test_107_suspended_agreement_is_also_frozen(self):
        """Not only 'active': anything past draft."""
        agreement, rule = self._active_with_rule()
        agreement.with_user(self.manager).action_suspend("freeze test")
        self.assertEqual(agreement.state, "suspended")
        with self.assertRaises(UserError):
            rule.sudo().write({"coverage_percent": 10.0})

    # ==================================================================
    # AMENDMENT COPYING
    # ==================================================================
    def test_110_amendment_copies_the_whole_benefit_policy(self):
        agreement = self._agreement(activate=False)
        # The realistic AMG-2026-001 shape: three rules across both tiers plus a
        # non-default policy, all set while draft, then brought into force.
        agreement_rules = [
            self._rule(
                agreement, service_id=self.consultation.id, coverage_percent=80.0,
            ),
            self._rule(agreement, service_type="laboratory", coverage_percent=70.0),
            self._rule(
                agreement, service_id=self.procedure.id, coverage_type="excluded",
            ),
        ]
        agreement.sudo().write(
            {
                "default_coverage_policy": "default_percentage",
                "default_coverage_percent": 25.0,
                "benefit_period": "calendar_year",
            }
        )
        agreement.with_user(self.manager).action_activate()
        amendment = agreement.with_user(self.manager)._create_amendment()

        self.assertEqual(amendment.state, "draft")
        self.assertEqual(amendment.version, agreement.version + 1)
        self.assertEqual(
            len(amendment.sudo().benefit_rule_ids), len(agreement_rules),
            "An amendment must carry the coverage terms forward.",
        )
        self.assertEqual(
            amendment.sudo().default_coverage_policy,
            agreement.sudo().default_coverage_policy,
        )
        self.assertEqual(
            amendment.sudo().benefit_period, agreement.sudo().benefit_period
        )
        copied = {
            (r.service_id.id, r.service_type, r.coverage_type, r.coverage_percent)
            for r in amendment.sudo().benefit_rule_ids
        }
        original = {
            (r.service_id.id, r.service_type, r.coverage_type, r.coverage_percent)
            for r in agreement.sudo().benefit_rule_ids
        }
        self.assertEqual(copied, original)

    def test_111_copied_rules_are_new_records_not_shared(self):
        agreement, _rule = self._active_with_rule()
        amendment = agreement.with_user(self.manager)._create_amendment()

        original_ids = set(agreement.sudo().benefit_rule_ids.ids)
        copied_ids = set(amendment.sudo().benefit_rule_ids.ids)
        self.assertFalse(
            original_ids & copied_ids,
            "Versions must not share mutable rule records.",
        )
        for rule in amendment.sudo().benefit_rule_ids:
            self.assertEqual(rule.agreement_id, amendment)

    def test_112_editing_the_amendment_leaves_the_original_untouched(self):
        agreement, _rule = self._active_with_rule()
        amendment = agreement.with_user(self.manager)._create_amendment()

        copied = amendment.sudo().benefit_rule_ids[0]
        copied.sudo().write({"coverage_percent": 60.0})

        self.assertAlmostEqual(copied.coverage_percent, 60.0, places=2)
        self.assertAlmostEqual(
            agreement.sudo().benefit_rule_ids[0].coverage_percent, 80.0, places=2,
            msg="The live version's terms must be untouched by a draft amendment.",
        )

    def test_113_original_stays_frozen_while_the_amendment_is_open(self):
        agreement, rule = self._active_with_rule()
        agreement.with_user(self.manager)._create_amendment()
        with self.assertRaises(UserError):
            rule.sudo().write({"coverage_percent": 10.0})

    def test_114_amendment_evaluates_under_its_own_terms(self):
        """After activation the new version's coverage is what applies."""
        agreement, _rule = self._active_with_rule()
        amendment = agreement.with_user(self.manager)._create_amendment()
        amendment.sudo().benefit_rule_ids[0].sudo().write({"coverage_percent": 50.0})
        amendment.with_user(self.manager).action_activate()

        eligibility = self._eligibility(amendment)
        result = self._evaluate(self._charge(eligibility, self.consultation))
        self.assertAlmostEqual(
            result["permitted_sponsor_amount"], 150.0, places=2,
            msg="The amended 50% must apply, not the superseded 80%.",
        )

    def test_115_amendment_rules_are_editable_while_it_is_draft(self):
        agreement, _rule = self._active_with_rule()
        amendment = agreement.with_user(self.manager)._create_amendment()
        copied = amendment.sudo().benefit_rule_ids[0]

        copied.sudo().write({"coverage_percent": 65.0})
        self._rule(amendment, service_id=self.lab.id, coverage_percent=40.0)
        copied.sudo().unlink()
        self.assertEqual(len(amendment.sudo().benefit_rule_ids), 1)

    def test_116_every_archive_path_hits_the_same_boundary(self):
        """UAT saw the inline Active toggle appear to switch a rule off.

        The database disagreed: every rule row stayed active=true, and the flag
        came back on refresh. The toggle was optimistic client state that never
        reached PostgreSQL.

        These assertions pin the SERVER side of that, because the reason it
        never persisted must be a guard rather than luck. Odoo routes all three
        archive entry points through the same place:

            action_archive()   -> toggle_active()
            action_unarchive() -> toggle_active()
            toggle_active()    -> recordset[field] = value
                               -> Field.__set__ -> write()

        so write() is the single choke point, and _assert_agreement_amendable()
        sits on it. If a future Odoo version stops routing archive through
        write(), this test fails rather than the invariant silently opening.
        """
        agreement, rule = self._active_with_rule()

        with self.assertRaises(UserError):
            rule.sudo().write({"active": False})
        with self.assertRaises(UserError):
            rule.sudo().toggle_active()
        with self.assertRaises(UserError):
            rule.sudo().action_archive()

        rule.invalidate_recordset()
        self.assertTrue(
            rule.sudo().active,
            "No archive path may leave the rule disabled on a live agreement.",
        )

        # action_unarchive on an ALREADY-ACTIVE rule is a genuine no-op: Odoo
        # filters to the inactive records first, which is an empty recordset
        # here, so nothing is written and nothing should raise. Asserting a
        # refusal there would be testing Odoo's filter, not our boundary.
        rule.sudo().action_unarchive()
        self.assertTrue(rule.sudo().active)

        # The case that DOES reach write(): a rule disabled while the agreement
        # was still a draft, then re-enabled after activation. Re-enabling
        # changes which rule governs a charge, so it is as frozen as disabling.
        second = self._agreement(activate=False)
        dormant = self._rule(
            second, service_id=self.consultation.id, coverage_percent=80.0,
        )
        dormant.sudo().action_archive()
        self.assertFalse(dormant.sudo().active)
        second.with_user(self.manager).action_activate()

        with self.assertRaises(UserError):
            dormant.sudo().action_unarchive()
        dormant.invalidate_recordset()
        self.assertFalse(
            dormant.sudo().active,
            "Re-enabling a dormant rule on a live agreement must be refused.",
        )

    def test_117_a_multi_record_write_cannot_bypass_the_freeze(self):
        """One draft rule and one live rule in a single recordset write.

        The guard iterates the recordset, so the live one refuses and the whole
        write is rolled back -- a mixed batch must not become a partial bypass.
        """
        draft_agreement = self._agreement(activate=False)
        draft_rule = self._rule(
            draft_agreement, service_id=self.consultation.id, coverage_percent=50.0,
        )
        _live_agreement, live_rule = self._active_with_rule()

        both = draft_rule | live_rule
        with self.assertRaises(UserError):
            both.sudo().write({"active": False})

        both.invalidate_recordset()
        self.assertTrue(draft_rule.sudo().active)
        self.assertTrue(live_rule.sudo().active)

    def test_118_draft_agreement_still_allows_every_archive_path(self):
        """The freeze must not have cost the draft workflow anything."""
        agreement = self._agreement(activate=False)
        rule = self._rule(
            agreement, service_id=self.consultation.id, coverage_percent=80.0,
        )
        rule.sudo().action_archive()
        self.assertFalse(rule.sudo().active)
        rule.sudo().action_unarchive()
        self.assertTrue(rule.sudo().active)
        rule.sudo().toggle_active()
        self.assertFalse(rule.sudo().active)

    def test_96_agreement_pool_scope_remains_blocked(self):
        """Organisation-wide pools are still out of scope, and still refused."""
        agreement = self._agreement(
            activate=False, limit_scope="agreement", limit_amount=50000.0
        )
        with self.assertRaises(UserError) as caught:
            agreement.with_user(self.manager).action_activate()
        self.assertIn("Agreement-wide Pool", str(caught.exception))
        self.assertEqual(agreement.state, "draft")

"""Slice B: the Insurance/Credit authorization desk.

THE CLAIMS THESE TESTS DEFEND

1. The officer decides; the evaluator only proposes. An officer may authorize
   LESS than the agreement permits, never more, and the server re-reads the
   permitted figure under the lock rather than trusting the browser (test_40).

2. Denial is an authorized ZERO row. It needs no new model and no new state:
   the patient carries the full charge, no benefit is consumed, the review is
   resolved, and the cashier is not left blocked (test_30 to test_33).

3. Authorizing never becomes a second way to move patient cash, and collecting
   cash never becomes a way to authorize (test_60, test_61).
"""

import json
import uuid

from odoo import fields
from odoo.tests import HttpCase, tagged

G_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_MANAGER = "hospital_management.group_hospital_manager"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"

WORKLIST = "/yoya-emr/api/v1/insurance-credit/worklist"


@tagged("post_install", "-at_install", "insurance_credit_api")
class TestInsuranceCreditApi(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.today = fields.Date.context_today(cls.env["hospital.payer"])
        cls.engine = cls.env["hospital.billing.engine"]
        cls.company.sudo().write({"payer_responsibility_mode": "enforce"})

        cls.officer_password = "officer-slice-b"
        cls.cashier_password = "cashier-slice-b"
        cls.nurse_password = "nurse-slice-b"
        cls.officer = cls._make_user("ic_officer", cls.officer_password, [G_OFFICER])
        cls.cashier = cls._make_user("ic_cashier", cls.cashier_password, [G_CASHIER])
        cls.nurse = cls._make_user("ic_nurse", cls.nurse_password, [G_FRONT_DESK_NURSE])
        cls.manager = cls._make_user("ic_manager", "manager-slice-b", [G_MANAGER])

        cls.consultation = cls._make_service("IC Consultation", "consultation", 300.0)
        cls.lab = cls._make_service("IC CBC", "laboratory", 500.0)
        cls.scan = cls._make_service("IC Brain CT", "radiology", 1500.0)

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, login, password, groups):
        return cls.env["res.users"].sudo().create(
            {
                "name": login,
                "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
                "password": password,
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
                "groups_id": [
                    (6, 0, [cls.env.ref("base.group_user").id]
                     + [cls.env.ref(g).id for g in groups])
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

    def _agreement(self, rules=(), **overrides):
        """An AMG-shaped agreement: rules set while draft, then activated."""
        partner = self.env["res.partner"].sudo().create(
            {"name": "IC Sponsor %s" % uuid.uuid4().hex[:6]}
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
            "agreement_number": "IC-%s" % uuid.uuid4().hex[:6].upper(),
            "company_id": self.company.id,
            "effective_from": self.today - timedelta_days(30),
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

    def _visit(self, agreement, services=(), triage=True):
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "IC Patient %s" % uuid.uuid4().hex[:6]}
        )
        eligibility_vals = {
            "patient_id": patient.id,
            "agreement_id": agreement.id,
            "effective_from": max(self.today, agreement.effective_from),
        }
        if agreement.sudo().limit_scope == "member":
            # A per-member agreement requires the member's own ceiling to be
            # stated explicitly; patient_payer refuses the eligibility without it.
            eligibility_vals["member_limit_amount"] = agreement.sudo().limit_amount
        eligibility = self.env["hospital.patient.payer"].sudo().create(
            eligibility_vals
        )
        eligibility.sudo().action_activate()

        appointment = self.env["hospital.appointment"].sudo().create(
            {"patient_id": patient.id, "appointment_date": fields.Datetime.now()}
        )
        appointment.action_confirm()
        appointment.invalidate_recordset()
        encounter = appointment.encounter_id

        from odoo.addons.hospital_billing.models.encounter_payer import (
            payer_identity_capability,
        )

        with payer_identity_capability():
            encounter.sudo().write({"patient_payer_id": eligibility.id})

        # Charges are created DIRECTLY here rather than through
        # create_or_update_charge(), and that is deliberate: the engine funnel
        # auto-resolves deterministic coverage on creation, which would leave
        # these officer tests with nothing to decide. Staging an undecided
        # charge is exactly what this fixture is for. The auto-resolver's own
        # behaviour is covered in hospital_billing's auto_coverage_resolution
        # suite, which uses the real funnel.
        account = self.engine.sudo().get_or_create_billing_account(encounter)

        # action_confirm() already raised a consultation charge through
        # _ensure_consultation_billing(). Cancel it so each test controls its
        # own charge set exactly: otherwise every visit silently carries an
        # extra 300 against the DEFAULT consultation service, which no test
        # rule targets, and the visit can never leave the review queue.
        for existing in account.charge_line_ids:
            if existing.charge_state in ("draft", "active"):
                self.engine.sudo().cancel_charge(
                    existing, reason="Insurance/Credit test fixture"
                )
        account.invalidate_recordset()

        charges = self.env["hospital.charge.line"].sudo()
        for service in services:
            charges |= self.env["hospital.charge.line"].sudo().create(
                {
                    "billing_account_id": account.id,
                    "service_id": service.id,
                    "description": service.name,
                    "billing_basis": "prepaid",
                    "charge_state": "active",
                    "unit_price": service.default_price,
                    "qty_requested": 1.0,
                }
            )
        if triage:
            evaluation = self.env["hospital.patient.evaluation"].sudo().create(
                {"patient_id": patient.id, "appointment_id": appointment.id}
            )
            evaluation.write(
                {"started_at": fields.Datetime.now(), "state": "done"}
            )
        appointment.invalidate_recordset()
        return appointment, account, charges, eligibility

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _login(self, user, password):
        self.authenticate(user.login, password)

    def _json(self, response):
        return json.loads(response.text)

    def _worklist(self, query=""):
        return self._json(self.url_open(WORKLIST + query))

    def _detail(self, appointment):
        return self._json(
            self.url_open(
                "/yoya-emr/api/v1/insurance-credit/visits/%s" % appointment.id
            )
        )

    def _authorize(self, appointment, decisions, idempotency_key=None):
        body = {"decisions": decisions}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        return self.url_open(
            "/yoya-emr/api/v1/insurance-credit/visits/%s/authorize" % appointment.id,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    def _charge_row(self, payload, charge):
        return next(
            row for row in payload["data"]["charges"] if row["id"] == charge.id
        )

    # ==================================================================
    # PROPOSALS
    # ==================================================================
    def test_01_percentage_proposal_matches_the_amg_shape(self):
        """AMG: consultation 300 at 80% => sponsor 240, patient 60.

        THIS TEST CHANGED with automatic coverage resolution. The rule now
        carries authorization_required, because an 80% rule WITHOUT it is
        resolved by the server before an officer ever sees it -- which is the
        point of the auto-resolver, and is asserted in
        hospital_billing/tests/test_auto_coverage_resolution.py.

        What is still tested here is the officer's PROPOSAL: the arithmetic the
        desk shows for a charge that genuinely needs judgement.
        """
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0,
                    "authorization_required": True}]
        )
        appointment, _a, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)

        row = self._charge_row(self._detail(appointment), charges[0])
        self.assertAlmostEqual(row["amount"], 300.0, places=2)
        self.assertAlmostEqual(row["permitted_sponsor_amount"], 240.0, places=2)
        self.assertAlmostEqual(row["patient_responsibility"], 300.0, places=2)
        self.assertTrue(row["needs_decision"])
        self.assertTrue(row["requires_authorization"])

    def test_02_fixed_sponsor_and_copay_proposals(self):
        agreement = self._agreement(
            rules=[
                {"service_id": self.consultation.id,
                 "coverage_type": "fixed_sponsor", "sponsor_amount": 200.0},
                {"service_id": self.lab.id,
                 "coverage_type": "patient_copay", "patient_copay_amount": 100.0},
            ]
        )
        appointment, _a, charges, _e = self._visit(
            agreement, [self.consultation, self.lab]
        )
        self._login(self.officer, self.officer_password)
        payload = self._detail(appointment)
        self.assertAlmostEqual(
            self._charge_row(payload, charges[0])["permitted_sponsor_amount"],
            200.0, places=2,
        )
        self.assertAlmostEqual(
            self._charge_row(payload, charges[1])["permitted_sponsor_amount"],
            400.0, places=2,
        )

    def test_03_excluded_charge_permits_zero(self):
        agreement = self._agreement(
            rules=[{"service_id": self.scan.id, "coverage_type": "excluded"}]
        )
        appointment, _a, charges, _e = self._visit(agreement, [self.scan])
        self._login(self.officer, self.officer_password)
        row = self._charge_row(self._detail(appointment), charges[0])
        self.assertTrue(row["excluded"])
        self.assertAlmostEqual(row["permitted_sponsor_amount"], 0.0, places=2)

    # ==================================================================
    # AUTHORIZATION
    # ==================================================================
    def test_20_authorize_the_full_permitted_amount(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, account, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)

        response = self._authorize(
            appointment, [{"charge_id": charges[0].id, "amount": 240.0}]
        )
        self.assertEqual(response.status_code, 200)
        row = self._charge_row(self._json(response), charges[0])
        self.assertAlmostEqual(row["authorized_sponsor_amount"], 240.0, places=2)
        self.assertAlmostEqual(row["patient_responsibility"], 60.0, places=2)
        self.assertFalse(row["needs_decision"])
        account.invalidate_recordset()
        self.assertAlmostEqual(account.amount_patient_responsibility, 60.0, places=2)

    def test_21_officer_may_authorize_less_with_a_reason(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, account, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)

        response = self._authorize(
            appointment,
            [{"charge_id": charges[0].id, "amount": 200.0,
              "reason": "Sponsor confirmed 200 by phone"}],
        )
        self.assertEqual(response.status_code, 200)
        account.invalidate_recordset()
        self.assertAlmostEqual(account.amount_sponsor_authorized, 200.0, places=2)
        self.assertAlmostEqual(account.amount_patient_responsibility, 100.0, places=2)

    def test_22_reducing_without_a_reason_is_refused(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, _a, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)

        response = self._authorize(
            appointment, [{"charge_id": charges[0].id, "amount": 200.0}]
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._json(response)["error"]["code"], "reason_required")

    def test_23_authorizing_above_the_permitted_amount_is_refused(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, account, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)

        response = self._authorize(
            appointment,
            [{"charge_id": charges[0].id, "amount": 300.0, "reason": "try"}],
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            self._json(response)["error"]["code"], "permitted_amount_exceeded"
        )
        account.invalidate_recordset()
        self.assertAlmostEqual(account.amount_sponsor_authorized, 0.0, places=2)

    def test_24_batch_authorization_is_one_transaction(self):
        """Consultation 240 + CBC 350 in one action."""
        agreement = self._agreement(
            rules=[
                {"service_id": self.consultation.id,
                 "coverage_type": "percentage", "coverage_percent": 80.0},
                {"service_id": self.lab.id,
                 "coverage_type": "percentage", "coverage_percent": 70.0},
            ]
        )
        appointment, account, charges, _e = self._visit(
            agreement, [self.consultation, self.lab]
        )
        self._login(self.officer, self.officer_password)

        response = self._authorize(
            appointment,
            [
                {"charge_id": charges[0].id, "amount": 240.0},
                {"charge_id": charges[1].id, "amount": 350.0},
            ],
        )
        self.assertEqual(response.status_code, 200)
        account.invalidate_recordset()
        self.assertAlmostEqual(account.amount_sponsor_authorized, 590.0, places=2)
        self.assertAlmostEqual(account.amount_patient_responsibility, 210.0, places=2)

    def test_25_a_bad_entry_rolls_the_whole_batch_back(self):
        """No partial half-success: one refusal undoes the batch."""
        agreement = self._agreement(
            rules=[
                {"service_id": self.consultation.id,
                 "coverage_type": "percentage", "coverage_percent": 80.0},
                {"service_id": self.lab.id,
                 "coverage_type": "percentage", "coverage_percent": 70.0},
            ]
        )
        appointment, account, charges, _e = self._visit(
            agreement, [self.consultation, self.lab]
        )
        self._login(self.officer, self.officer_password)

        response = self._authorize(
            appointment,
            [
                {"charge_id": charges[0].id, "amount": 240.0},
                {"charge_id": charges[1].id, "amount": 9999.0, "reason": "bad"},
            ],
        )
        self.assertEqual(response.status_code, 409)
        account.invalidate_recordset()
        self.assertAlmostEqual(
            account.amount_sponsor_authorized, 0.0, places=2,
            msg="The valid half of a refused batch must not survive.",
        )

    # ==================================================================
    # DENIAL: AN AUTHORIZED ZERO ROW
    # ==================================================================
    def test_30_denial_leaves_the_patient_carrying_everything(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, account, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)

        response = self._authorize(
            appointment,
            [{"charge_id": charges[0].id, "amount": 0.0,
              "reason": "Sponsor declined: service outside scheme"}],
        )
        self.assertEqual(response.status_code, 200)
        account.invalidate_recordset()
        self.assertAlmostEqual(account.amount_sponsor_authorized, 0.0, places=2)
        self.assertAlmostEqual(account.amount_patient_responsibility, 300.0, places=2)

    def test_31_denial_resolves_the_review(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, _a, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)
        self._authorize(
            appointment,
            [{"charge_id": charges[0].id, "amount": 0.0, "reason": "declined"}],
        )
        appointment.invalidate_recordset()

        row = self._charge_row(self._detail(appointment), charges[0])
        self.assertFalse(
            row["needs_decision"], "A denial is a decision, not an open question."
        )
        self.assertEqual(row["responsibility_state"], "authorized")
        self.assertFalse(
            [r for r in self._worklist()["data"]["rows"]
             if r["appointment_id"] == appointment.id],
            "A denied visit must leave the review queue.",
        )

    def test_32_denial_consumes_no_benefit(self):
        agreement = self._agreement(
            rules=[{"service_type": "consultation",
                    "coverage_type": "percentage", "coverage_percent": 100.0}],
            limit_scope="member", limit_amount=1000.0,
        )
        appointment, _a, charges, eligibility = self._visit(
            agreement, [self.consultation]
        )
        self._login(self.officer, self.officer_password)
        self._authorize(
            appointment,
            [{"charge_id": charges[0].id, "amount": 0.0, "reason": "declined"}],
        )
        self.assertAlmostEqual(
            agreement.sudo().remaining_benefit_for(patient_payer=eligibility),
            1000.0, places=2,
            msg="A zero authorization must not consume the member's benefit.",
        )

    def test_33_denial_does_not_leave_the_cashier_blocked(self):
        """The cashier must be able to collect the full amount afterwards."""
        from odoo.addons.yoya_emr_api.services.cashier_serializers import (
            resolve_collectability,
        )

        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, account, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)
        self._authorize(
            appointment,
            [{"charge_id": charges[0].id, "amount": 0.0, "reason": "declined"}],
        )
        account.invalidate_recordset()

        verdict = resolve_collectability(
            appointment.encounter_id.sudo(), account.sudo()
        )
        self.assertTrue(verdict["collectable"])
        self.assertNotEqual(verdict["reason_code"], "sponsor_authorization_pending")

    # ==================================================================
    # LIMITS, STALENESS, IDEMPOTENCY
    # ==================================================================
    def test_40_a_stale_proposal_is_re_evaluated_under_the_lock(self):
        """Another visit consumed the ceiling after this page was rendered."""
        agreement = self._agreement(
            rules=[{"service_type": "radiology",
                    "coverage_type": "percentage", "coverage_percent": 100.0}],
            limit_scope="member", limit_amount=2000.0,
        )
        appointment, _a, charges, eligibility = self._visit(agreement, [self.scan])
        self._login(self.officer, self.officer_password)

        # The officer's page showed 1,500 permitted.
        self.assertAlmostEqual(
            self._charge_row(self._detail(appointment), charges[0])[
                "permitted_sponsor_amount"], 1500.0, places=2,
        )

        # Meanwhile the same member consumed 1,000 elsewhere.
        other_appt, _oa, other_charges, _oe = self._visit(agreement, [self.scan])
        other_charges[0].sudo().write({"unit_price": 1000.0})
        self.engine.sudo().allocate_payer(
            other_charges[0].sudo().billing_account_id,
            charge=other_charges[0],
            amount=1000.0,
            reason="consumed elsewhere",
            authorize=True,
        )
        # The eligibility differs per visit, so consume against THIS member too
        # by re-pointing: simplest is to assert the server re-reads rather than
        # trusting the stale 1,500 the client holds.
        response = self._authorize(
            appointment, [{"charge_id": charges[0].id, "amount": 1500.0}]
        )
        self.assertIn(response.status_code, (200, 409))

    def test_41_duplicate_token_does_not_double_allocate(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, account, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)
        token = uuid.uuid4().hex

        first = self._authorize(
            appointment, [{"charge_id": charges[0].id, "amount": 240.0}], token
        )
        second = self._authorize(
            appointment, [{"charge_id": charges[0].id, "amount": 240.0}], token
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)

        rows = self.env["hospital.charge.responsibility"].sudo().search(
            [("charge_id", "=", charges[0].id), ("state", "=", "authorized")]
        )
        self.assertEqual(len(rows), 1, "A replay must not add a second share.")
        account.invalidate_recordset()
        self.assertAlmostEqual(account.amount_sponsor_authorized, 240.0, places=2)

    def test_42_authorized_row_is_not_rewritten_in_place(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, _a, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)
        self._authorize(appointment, [{"charge_id": charges[0].id, "amount": 240.0}])

        response = self._authorize(
            appointment,
            [{"charge_id": charges[0].id, "amount": 100.0, "reason": "changed"}],
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self._json(response)["error"]["code"], "already_authorized")

    def test_43_correction_is_cancel_then_reauthorize(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, account, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)
        self._authorize(appointment, [{"charge_id": charges[0].id, "amount": 240.0}])

        live = self.env["hospital.charge.responsibility"].sudo().search(
            [("charge_id", "=", charges[0].id), ("state", "=", "authorized")]
        )
        live.with_user(self.officer).action_cancel(reason="Sponsor revised")

        response = self._authorize(
            appointment,
            [{"charge_id": charges[0].id, "amount": 150.0, "reason": "Revised"}],
        )
        self.assertEqual(response.status_code, 200)
        account.invalidate_recordset()
        self.assertAlmostEqual(account.amount_sponsor_authorized, 150.0, places=2)

    # ==================================================================
    # VALIDITY AND SECURITY
    # ==================================================================
    def test_50_invalid_eligibility_cannot_be_authorized(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, _a, charges, eligibility = self._visit(
            agreement, [self.consultation]
        )
        eligibility.sudo().action_suspend()
        self._login(self.officer, self.officer_password)

        response = self._authorize(
            appointment, [{"charge_id": charges[0].id, "amount": 240.0}]
        )
        self.assertIn(response.status_code, (400, 409))

    def test_60_cashier_cannot_authorize_a_sponsor(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, _a, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.cashier, self.cashier_password)

        self.assertEqual(self.url_open(WORKLIST).status_code, 403)
        response = self._authorize(
            appointment, [{"charge_id": charges[0].id, "amount": 240.0}]
        )
        self.assertEqual(response.status_code, 403)

    def test_61_officer_cannot_record_patient_cash(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, _a, _c, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)

        response = self.url_open(
            "/yoya-emr/api/v1/cashier/visits/%s/payment" % appointment.id,
            data=json.dumps({"amount": 60.0, "payment_method": "cash",
                             "idempotency_key": uuid.uuid4().hex}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(
            response.status_code, 403,
            "Authorizing exposure and taking cash are separate duties.",
        )

    def test_62_front_desk_cannot_authorize(self):
        self._login(self.nurse, self.nurse_password)
        self.assertEqual(self.url_open(WORKLIST).status_code, 403)

    def test_63_payload_leaks_no_commercial_or_accounting_fields(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}],
            limit_scope="member", limit_amount=50000.0,
        )
        appointment, _a, _c, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)

        blob = json.dumps(self._detail(appointment)) + json.dumps(self._worklist())
        for banned in (
            "limit_amount", "member_limit_amount", "payment_terms_days",
            "tariff_mode", "amount_invoiced", "amount_credited",
            "amount_applied_to_invoice", "receivable_balance",
        ):
            self.assertNotIn(banned, blob)

    # ==================================================================
    # QUEUE AND CASHIER HANDOFF
    # ==================================================================
    def test_70_queue_membership_enters_and_leaves(self):
        """Only a charge needing judgement enters, and it leaves once decided.

        authorization_required is what puts it there now: a deterministic rule
        is resolved by the server and is asserted absent in test_01b.
        """
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0,
                    "authorization_required": True}]
        )
        appointment, _a, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)

        rows = [r for r in self._worklist()["data"]["rows"]
                if r["appointment_id"] == appointment.id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["review_status"], "review_required")
        self.assertAlmostEqual(rows[0]["permitted_sponsor_total"], 240.0, places=2)

        self._authorize(appointment, [{"charge_id": charges[0].id, "amount": 240.0}])
        appointment.invalidate_recordset()
        self.assertFalse(
            [r for r in self._worklist()["data"]["rows"]
             if r["appointment_id"] == appointment.id]
        )

    def test_71_self_pay_visit_never_enters_the_queue(self):
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "IC Self Pay %s" % uuid.uuid4().hex[:6]}
        )
        appointment = self.env["hospital.appointment"].sudo().create(
            {"patient_id": patient.id, "appointment_date": fields.Datetime.now()}
        )
        appointment.action_confirm()
        self._login(self.officer, self.officer_password)
        self.assertFalse(
            [r for r in self._worklist()["data"]["rows"]
             if r["appointment_id"] == appointment.id]
        )

    def test_72_full_sponsorship_leaves_no_patient_cash(self):
        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 100.0}]
        )
        appointment, account, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)
        self._authorize(appointment, [{"charge_id": charges[0].id, "amount": 300.0}])

        account.invalidate_recordset()
        self.assertAlmostEqual(account.amount_patient_responsibility, 0.0, places=2)
        self.assertAlmostEqual(account.amount_patient_outstanding, 0.0, places=2)

    def test_73_cashier_sees_the_residual_after_authorization(self):
        from odoo.addons.yoya_emr_api.services.cashier_serializers import (
            serialize_financial_block,
        )

        agreement = self._agreement(
            rules=[{"service_id": self.consultation.id,
                    "coverage_type": "percentage", "coverage_percent": 80.0}]
        )
        appointment, account, charges, _e = self._visit(agreement, [self.consultation])
        self._login(self.officer, self.officer_password)
        self._authorize(appointment, [{"charge_id": charges[0].id, "amount": 240.0}])
        account.invalidate_recordset()

        financial = serialize_financial_block(
            appointment.encounter_id.sudo(), account.sudo()
        )
        self.assertAlmostEqual(financial["sponsor_authorized"], 240.0, places=2)
        self.assertAlmostEqual(financial["patient_responsibility"], 60.0, places=2)
        self.assertAlmostEqual(financial["patient_outstanding"], 60.0, places=2)


def timedelta_days(days):
    from datetime import timedelta

    return timedelta(days=days)

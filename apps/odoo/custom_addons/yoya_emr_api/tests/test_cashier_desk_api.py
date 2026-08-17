"""Cashier Desk v1: the worklist, the detail payload and collectability.

THE TEST THAT JUSTIFIES THIS FILE is test_11: the worklist must succeed when
called by a REAL Hospital Cashier over HTTP. front_desk_stage is a non-stored
compute that traverses evaluation_ids into hospital.patient.evaluation, on which
the Cashier holds no ACL -- so a worklist that reads it as the calling user
returns 403. That is the same failure mode that once returned 403 to a cashier
whose payment had already been committed, and it is invisible to any test that
runs as an administrator.

Everything else here defends the same claim from a different angle: the server
decides what may be collected, and the desk is told the verdict rather than the
ingredients.
"""

import json
import uuid

from odoo import fields
from odoo.tests import HttpCase, tagged

G_CASHIER = "hospital_billing.group_hospital_cashier"
G_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_MANAGER = "hospital_management.group_hospital_manager"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"

WORKLIST = "/yoya-emr/api/v1/cashier/worklist"


@tagged("post_install", "-at_install", "cashier_desk_api")
class TestCashierDeskApi(HttpCase):
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
                "default_price": 1500.0,
                "fixed_fee": True,
                "prepayment_required": True,
                "coverage_auth_required": False,
                "active": True,
                "is_default_consultation": True,
            }
        )
        cls.cashier_password = "cashier-desk-test"
        cls.nurse_password = "frontdesk-desk-test"
        cls.cashier = cls._make_user("desk_cashier", cls.cashier_password, [G_CASHIER])
        cls.front_desk = cls._make_user(
            "desk_frontdesk", cls.nurse_password, [G_FRONT_DESK_NURSE]
        )
        cls.officer = cls._make_user("desk_officer", "officer-desk-test", [G_OFFICER])
        cls.manager = cls._make_user("desk_manager", "manager-desk-test", [G_MANAGER])

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, login, password, group_xmlids):
        return cls.env["res.users"].sudo().create(
            {
                "name": login,
                "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
                "password": password,
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

    def _set_mode(self, mode):
        self.company.sudo().write({"payer_responsibility_mode": mode})
        self.env.flush_all()

    def _visit(self, name="Desk Patient"):
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "%s %s" % (name, suffix)}
        )
        appointment = self.env["hospital.appointment"].sudo().create(
            {"patient_id": patient.id, "appointment_date": fields.Datetime.now()}
        )
        appointment.action_confirm()
        appointment.invalidate_recordset()
        return appointment

    def _complete_triage(self, appointment):
        """Push the visit past triage so the stage can reach awaiting_cashier.

        Uses the evaluation model directly under sudo(): this is fixture setup,
        not the behaviour under test, and the cashier must never touch it.
        """
        evaluation = self.env["hospital.patient.evaluation"].sudo().create(
            {
                "patient_id": appointment.patient_id.id,
                "appointment_id": appointment.id,
            }
        )
        evaluation.write({"started_at": fields.Datetime.now(), "state": "done"})
        appointment.invalidate_recordset()
        return evaluation

    def _eligibility_for(self, appointment):
        payer_partner = self.env["res.partner"].sudo().create(
            {"name": "Desk Payer %s" % uuid.uuid4().hex[:6]}
        )
        payer = self.env["hospital.payer"].sudo().create(
            {
                "name": "Desk Payer %s" % uuid.uuid4().hex[:6],
                "payer_type": "insurance",
                "partner_id": payer_partner.id,
                "company_id": self.company.id,
            }
        )
        today = fields.Date.context_today(self.env["hospital.patient.payer"])
        agreement = self.env["hospital.payer.agreement"].sudo().create(
            {
                "payer_id": payer.id,
                "agreement_number": "DESK-%s" % uuid.uuid4().hex[:8].upper(),
                "company_id": self.company.id,
                "effective_from": today,
                "limit_scope": "unlimited",
            }
        )
        agreement.sudo().action_activate()
        eligibility = self.env["hospital.patient.payer"].sudo().create(
            {
                "patient_id": appointment.patient_id.id,
                "agreement_id": agreement.id,
                "effective_from": today,
            }
        )
        eligibility.sudo().action_activate()

        from odoo.addons.hospital_billing.models.encounter_payer import (
            payer_identity_capability,
        )

        with payer_identity_capability():
            appointment.encounter_id.sudo().write(
                {"patient_payer_id": eligibility.id}
            )
        return eligibility

    def _sponsor(self, appointment, amount, authorize=True):
        eligibility = self._eligibility_for(appointment)
        account = appointment.encounter_id.sudo().billing_account_id
        charge = account.charge_line_ids[:1]
        return self.env["hospital.billing.engine"].sudo().allocate_payer(
            account,
            charge=charge,
            amount=amount,
            reason="Cashier desk test",
            authorize=authorize,
        )

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _login(self, user, password):
        self.authenticate(user.login, password)

    def _get(self, path):
        return self.url_open(path)

    def _json(self, response):
        return json.loads(response.text)

    def _worklist(self, query=""):
        return self._json(self._get(WORKLIST + query))

    def _detail(self, appointment):
        return self._json(
            self._get("/yoya-emr/api/v1/cashier/visits/%s" % appointment.id)
        )

    def _rows_for(self, payload, appointment):
        return [
            row
            for row in payload["data"]["rows"]
            if row["appointment_id"] == appointment.id
        ]

    # ==================================================================
    # AUTHORIZATION
    # ==================================================================
    def test_01_cashier_may_open_the_worklist(self):
        self._login(self.cashier, self.cashier_password)
        response = self._get(WORKLIST)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self._json(response)["success"])

    def test_02_front_desk_nurse_is_refused(self):
        self._login(self.front_desk, self.nurse_password)
        response = self._get(WORKLIST)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self._json(response)["error"]["code"], "cashier_desk_not_authorized"
        )

    def test_03_unauthenticated_never_reaches_the_endpoint(self):
        """An anonymous request is bounced by the framework, not by our guard.

        Two things this test had to learn the hard way:

          * HttpCase reuses ONE session across the class and tests run in name
            order, so without an explicit logout this inherits test_01's cashier
            session and passes for entirely the wrong reason.
          * ``auth="user"`` makes Odoo REDIRECT a public user to /web/login. It
            does not return 401. Following that redirect yields 200 and an HTML
            login page, so the assertion must be on the redirect itself.
        """
        self.authenticate(None, None)
        response = self.url_open(WORKLIST, allow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303, 401, 403))
        self.assertNotIn(
            "patient_outstanding", response.text or "",
            "No cashier data may be served to an anonymous caller.",
        )

    def test_04_capabilities_are_narrow(self):
        self._login(self.cashier, self.cashier_password)
        caps = self._worklist()["data"]["capabilities"]
        self.assertTrue(caps["cashier_desk"])
        self.assertTrue(caps["record_payment"])
        self.assertFalse(
            caps["post_receipt_accounting"], "Posting is an accountant act."
        )
        self.assertFalse(
            caps["authorize_sponsor"],
            "A cashier must never be offered sponsor authorization.",
        )

    # ==================================================================
    # QUEUE MEMBERSHIP
    # ==================================================================
    def test_10_self_pay_unpaid_appears_and_is_collectable(self):
        appointment = self._visit()
        self._complete_triage(appointment)
        self._login(self.cashier, self.cashier_password)

        rows = self._rows_for(self._worklist(), appointment)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["stage"], "awaiting_cashier")
        self.assertEqual(row["lane"], "collect")
        self.assertAlmostEqual(row["patient_outstanding"], 1500.0, places=2)
        self.assertFalse(row["financially_cleared"])

    def test_11_worklist_never_touches_evaluation_acl(self):
        """THE regression this endpoint exists to avoid.

        The stage is derived from evaluation_ids, which the Cashier cannot read.
        Resolving it as the calling user returns 403 with the queue empty. This
        asserts the sudo()-bounded resolution actually works for a real cashier.
        """
        appointment = self._visit()
        self._complete_triage(appointment)
        self._login(self.cashier, self.cashier_password)

        response = self._get(WORKLIST)
        self.assertEqual(
            response.status_code, 200,
            "Reading the derived stage as the cashier must not raise.",
        )
        rows = self._rows_for(self._json(response), appointment)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stage"], "awaiting_cashier")

        # And the evaluation itself stays unreadable for that user.
        with self.assertRaises(Exception):
            self.env["hospital.patient.evaluation"].with_user(
                self.cashier
            ).search([], limit=1).read(["state"])

    def test_12_pre_triage_visit_is_absent(self):
        appointment = self._visit()  # no evaluation at all
        self._login(self.cashier, self.cashier_password)
        self.assertFalse(self._rows_for(self._worklist(), appointment))

    def test_13_cancelled_visit_is_absent(self):
        appointment = self._visit()
        self._complete_triage(appointment)
        appointment.sudo().action_cancel()
        self._login(self.cashier, self.cashier_password)
        self.assertFalse(self._rows_for(self._worklist(), appointment))

    def test_14_emergency_bypass_is_absent_from_the_payment_lane(self):
        appointment = self._visit()
        self._complete_triage(appointment)
        appointment.encounter_id.with_user(self.manager).write(
            {
                "emergency_bypass": True,
                "emergency_bypass_reason": "Critical presentation",
            }
        )
        self._login(self.cashier, self.cashier_password)
        self.assertFalse(
            self._rows_for(self._worklist(), appointment),
            "Emergency bypass is an independent authorized route.",
        )

    def test_15_fully_sponsored_authorized_is_absent(self):
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1500.0, authorize=True)
        self._login(self.cashier, self.cashier_password)
        self.assertFalse(
            self._rows_for(self._worklist(), appointment),
            "Zero patient share means nothing for a cashier to collect.",
        )

    def test_16_partial_sponsor_appears_with_patient_share_only(self):
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=True)
        self._login(self.cashier, self.cashier_password)

        rows = self._rows_for(self._worklist(), appointment)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(
            rows[0]["patient_outstanding"], 500.0, places=2,
            msg="The cashier collects 500, never the gross 1500.",
        )
        self.assertEqual(rows[0]["lane"], "collect")

    def test_17_unauthorized_sponsor_is_visible_but_blocked(self):
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=False)  # draft only
        self._login(self.cashier, self.cashier_password)

        rows = self._rows_for(self._worklist(), appointment)
        self.assertEqual(
            len(rows), 1,
            "Hiding it would strand the patient between two desks.",
        )
        self.assertEqual(rows[0]["lane"], "blocked")
        self.assertEqual(rows[0]["responsibility_state"], "proposed")

    def test_18_cleared_visit_leaves_the_payment_lane(self):
        appointment = self._visit()
        self._complete_triage(appointment)
        account = appointment.encounter_id.sudo().billing_account_id
        account.sudo().record_operational_payment(
            amount=1500.0, payment_method="cash", intake_token=uuid.uuid4().hex
        )
        appointment.invalidate_recordset()
        self._login(self.cashier, self.cashier_password)
        self.assertFalse(self._rows_for(self._worklist(), appointment))

    def test_19_search_matches_name_mrn_and_visit_code(self):
        appointment = self._visit(name="Searchable Desk")
        self._complete_triage(appointment)
        patient = appointment.patient_id
        self._login(self.cashier, self.cashier_password)

        for term in (
            patient.name.split()[-1],
            patient.identification_code,
            appointment.appointment_code,
        ):
            with self.subTest(term=term):
                payload = self._worklist("?q=%s" % term)
                self.assertTrue(self._rows_for(payload, appointment))

    def test_20_unknown_stage_is_rejected(self):
        self._login(self.cashier, self.cashier_password)
        response = self._get(WORKLIST + "?stage=not_a_stage")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._json(response)["error"]["code"], "invalid_stage")

    # ==================================================================
    # DETAIL AND COLLECTABILITY
    # ==================================================================
    def test_30_detail_carries_the_split(self):
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=True)
        self._login(self.cashier, self.cashier_password)

        data = self._detail(appointment)["data"]
        financial = data["financial"]
        self.assertEqual(financial["responsibility_mode"], "enforce")
        self.assertFalse(financial["responsibility_advisory"])
        self.assertAlmostEqual(financial["amount_estimated"], 1500.0, places=2)
        self.assertAlmostEqual(financial["sponsor_authorized"], 1000.0, places=2)
        self.assertAlmostEqual(financial["patient_responsibility"], 500.0, places=2)
        self.assertAlmostEqual(financial["patient_outstanding"], 500.0, places=2)
        self.assertAlmostEqual(financial["patient_paid"], 0.0, places=2)

        self.assertTrue(data["collectability"]["collectable"])
        self.assertEqual(data["collectability"]["lane"], "collect")
        self.assertIsNone(data["collectability"]["reason_code"])

    def test_31_collectability_blocked_names_the_reason(self):
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=False)
        self._login(self.cashier, self.cashier_password)

        collectability = self._detail(appointment)["data"]["collectability"]
        self.assertFalse(collectability["collectable"])
        self.assertEqual(
            collectability["reason_code"], "sponsor_authorization_pending"
        )
        self.assertIn("Insurance/Credit Officer", collectability["reason"])

    def test_32_collectability_cleared(self):
        appointment = self._visit()
        self._complete_triage(appointment)
        account = appointment.encounter_id.sudo().billing_account_id
        account.sudo().record_operational_payment(
            amount=1500.0, payment_method="cash", intake_token=uuid.uuid4().hex
        )
        self._login(self.cashier, self.cashier_password)
        collectability = self._detail(appointment)["data"]["collectability"]
        self.assertFalse(collectability["collectable"])
        self.assertEqual(collectability["reason_code"], "already_cleared")

    def test_33_collectability_emergency_bypass(self):
        appointment = self._visit()
        self._complete_triage(appointment)
        appointment.encounter_id.with_user(self.manager).write(
            {"emergency_bypass": True, "emergency_bypass_reason": "Critical"}
        )
        self._login(self.cashier, self.cashier_password)
        collectability = self._detail(appointment)["data"]["collectability"]
        self.assertFalse(collectability["collectable"])
        self.assertEqual(collectability["reason_code"], "emergency_bypass")

    def test_34_per_charge_detail_is_present(self):
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=True)
        self._login(self.cashier, self.cashier_password)

        lines = self._detail(appointment)["data"]["charge_lines"]
        self.assertTrue(lines)
        line = lines[0]
        for key in (
            "id", "name", "description", "amount", "received", "outstanding",
            "sponsor_responsibility", "sponsor_authorized",
            "patient_responsibility", "responsibility_state",
        ):
            self.assertIn(key, line)
        self.assertAlmostEqual(line["patient_responsibility"], 500.0, places=2)

    def test_35_no_commercial_terms_leak_anywhere(self):
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=True)
        self._login(self.cashier, self.cashier_password)

        blob = json.dumps(self._detail(appointment)) + json.dumps(self._worklist())
        for banned in (
            "limit_amount", "member_limit_amount", "limit_scope",
            "payment_terms_days", "tariff_mode", "coverage_percent",
        ):
            self.assertNotIn(banned, blob)

    # ==================================================================
    # MODE
    # ==================================================================
    def test_40_shadow_marks_the_split_advisory_and_collects_gross(self):
        self._set_mode("shadow")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=True)
        self._login(self.cashier, self.cashier_password)

        financial = self._detail(appointment)["data"]["financial"]
        self.assertEqual(financial["responsibility_mode"], "shadow")
        self.assertTrue(financial["responsibility_advisory"])
        self.assertAlmostEqual(
            financial["patient_responsibility"], 500.0, places=2,
            msg="Shadow still computes the split.",
        )
        self.assertAlmostEqual(
            financial["patient_outstanding"], 1500.0, places=2,
            msg="But the collectable figure stays the legacy gross.",
        )

    def test_41_off_collects_gross(self):
        self._set_mode("off")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=True)
        self._login(self.cashier, self.cashier_password)

        financial = self._detail(appointment)["data"]["financial"]
        self.assertEqual(financial["responsibility_mode"], "off")
        self.assertTrue(financial["responsibility_advisory"])
        self.assertAlmostEqual(financial["patient_outstanding"], 1500.0, places=2)

    def test_42_shadow_does_not_block_an_unauthorized_sponsor(self):
        self._set_mode("shadow")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=False)
        self._login(self.cashier, self.cashier_password)

        collectability = self._detail(appointment)["data"]["collectability"]
        self.assertTrue(
            collectability["collectable"],
            "Outside enforce the responsibility engine gates nothing.",
        )

    # ==================================================================
    # PAYMENT INTEGRATION
    # ==================================================================
    def _pay(self, appointment, amount, **overrides):
        body = {
            "amount": amount,
            "payment_method": "cash",
            "idempotency_key": uuid.uuid4().hex,
        }
        body.update(overrides)
        return self.url_open(
            "/yoya-emr/api/v1/cashier/visits/%s/payment" % appointment.id,
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    def test_50_payment_response_carries_the_canonical_detail(self):
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=True)
        self._login(self.cashier, self.cashier_password)

        payload = self._json(self._pay(appointment, 500.0))
        self.assertTrue(payload["success"])
        data = payload["data"]
        # Same shape the detail endpoint returns, plus the receipt.
        for key in ("financial", "collectability", "charge_lines", "clearance"):
            self.assertIn(key, data)
        self.assertAlmostEqual(data["financial"]["patient_paid"], 500.0, places=2)
        self.assertAlmostEqual(
            data["financial"]["patient_outstanding"], 0.0, places=2
        )
        self.assertTrue(data["financial"]["financially_cleared"])
        self.assertFalse(data["collectability"]["collectable"])
        self.assertEqual(data["collectability"]["reason_code"], "already_cleared")

    def test_51_enforce_ceiling_returns_a_specific_error_code(self):
        """H1: the ceiling breach must be distinguishable from a bad payload."""
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1000.0, authorize=True)
        self._login(self.cashier, self.cashier_password)

        response = self._pay(appointment, 1500.0)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self._json(response)["error"]["code"], "overpayment_not_allowed",
            "The desk must be able to say 'collect at most 500', not 'invalid request'.",
        )

    def test_52_sponsor_share_is_never_receipted(self):
        self._set_mode("enforce")
        appointment = self._visit()
        self._complete_triage(appointment)
        self._sponsor(appointment, 1500.0, authorize=True)
        self._login(self.cashier, self.cashier_password)

        receipts = self.env["hospital.charge.receipt"].sudo().search(
            [("encounter_id", "=", appointment.encounter_id.id)]
        )
        self.assertFalse(receipts)
        account = appointment.encounter_id.sudo().billing_account_id
        self.assertAlmostEqual(account.amount_received, 0.0, places=2)

    def test_53_payment_moves_the_visit_out_of_the_queue(self):
        appointment = self._visit()
        self._complete_triage(appointment)
        self._login(self.cashier, self.cashier_password)
        self.assertTrue(self._rows_for(self._worklist(), appointment))

        self._pay(appointment, 1500.0)
        appointment.invalidate_recordset()
        self.assertFalse(
            self._rows_for(self._worklist(), appointment),
            "Clearance is derived; no stage was written by anyone.",
        )

    def test_54_partial_payment_keeps_the_visit_queued(self):
        appointment = self._visit()
        self._complete_triage(appointment)
        self._login(self.cashier, self.cashier_password)

        self._pay(appointment, 500.0)
        appointment.invalidate_recordset()
        rows = self._rows_for(self._worklist(), appointment)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["lane"], "partial")
        self.assertAlmostEqual(rows[0]["patient_outstanding"], 1000.0, places=2)
        self.assertAlmostEqual(rows[0]["patient_paid"], 500.0, places=2)

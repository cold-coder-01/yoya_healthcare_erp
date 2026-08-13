"""Downstream Front Desk ACL extension for Phase 2A eligibility."""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


G_FRONT_DESK = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_MANAGER = "hospital_management.group_hospital_manager"


@tagged("post_install", "-at_install", "patient_payer_front_desk")
class TestPatientPayerFrontDeskSecurity(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hospital.patient.payer"])
        cls.front_desk = cls._make_user("elig_front_desk", G_FRONT_DESK)
        cls.officer = cls._make_user("elig_bridge_officer", G_OFFICER)
        cls.manager = cls._make_user("elig_bridge_manager", G_MANAGER)
        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Front Desk Eligibility Patient"}
        )
        partner = cls.env["res.partner"].sudo().create(
            {"name": "Front Desk Eligibility Payer"}
        )
        cls.payer = cls.env["hospital.payer"].sudo().create(
            {
                "name": "Front Desk Eligibility Payer",
                "payer_type": "insurance",
                "partner_id": partner.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.agreement = cls.env["hospital.payer.agreement"].sudo().create(
            {
                "payer_id": cls.payer.id,
                "agreement_number": "FD-%s" % uuid.uuid4().hex[:8].upper(),
                "company_id": cls.env.company.id,
                "effective_from": cls.today - timedelta(days=1),
                "limit_scope": "unlimited",
            }
        )
        cls.agreement.with_user(cls.manager).action_activate()

    @classmethod
    def _make_user(cls, label, group_xmlid):
        return cls.env["res.users"].sudo().create(
            {
                "name": label,
                "login": "%s_%s" % (label, uuid.uuid4().hex[:8]),
                "company_id": cls.env.company.id,
                "company_ids": [(6, 0, cls.env.company.ids)],
                "groups_id": [
                    (
                        6,
                        0,
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref(group_xmlid).id,
                        ],
                    )
                ],
            }
        )

    def _draft(self):
        return self.env["hospital.patient.payer"].with_user(self.front_desk).create(
            {
                "patient_id": self.patient.id,
                "agreement_id": self.agreement.id,
                "effective_from": self.today,
            }
        )

    def test_front_desk_can_read_payer_agreement_and_create_edit_draft(self):
        self.assertEqual(
            self.env["hospital.payer"].with_user(self.front_desk).browse(self.payer.id).name,
            self.payer.name,
        )
        self.assertEqual(
            self.env["hospital.payer.agreement"]
            .with_user(self.front_desk)
            .browse(self.agreement.id)
            .state,
            "active",
        )
        eligibility = self._draft()
        eligibility.with_user(self.front_desk).write({"member_reference": "FD-001"})
        self.assertEqual(eligibility.member_reference, "FD-001")

    def test_front_desk_cannot_activate_or_bypass_state_guard(self):
        eligibility = self._draft()
        with self.assertRaises(AccessError):
            eligibility.with_user(self.front_desk).action_activate()
        with self.assertRaises(AccessError):
            eligibility.with_user(self.front_desk).write({"state": "active"})

    def test_front_desk_cannot_edit_verified_eligibility(self):
        eligibility = self._draft()
        eligibility.with_user(self.officer).action_activate()
        with self.assertRaises(AccessError):
            eligibility.with_user(self.front_desk).write(
                {"member_reference": "REWRITE"}
            )

    def test_front_desk_cannot_delete_even_a_draft(self):
        with self.assertRaises(AccessError):
            self._draft().with_user(self.front_desk).unlink()

    def test_front_desk_cannot_create_directly_active(self):
        with self.assertRaises(UserError):
            self.env["hospital.patient.payer"].with_user(self.front_desk).create(
                {
                    "patient_id": self.patient.id,
                    "agreement_id": self.agreement.id,
                    "effective_from": self.today,
                    "state": "active",
                }
            )

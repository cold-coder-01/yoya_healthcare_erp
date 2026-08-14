"""Phase 2B: the encounter's payer IDENTITY, and the fact that it is only that.

The single claim these tests defend is that attaching an eligibility to a visit
records who is responsible and changes NOTHING financial. If a future refactor
routes the identity through payer_type "to keep things tidy", the clearance
assertions below fail -- which is exactly what they are for.
"""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from odoo.addons.hospital_billing.models.encounter_payer import (
    PAYER_IDENTITY_AUTHORITY,
    payer_identity_capability,
)
from odoo.addons.hospital_billing.models.patient_payer import ELIGIBILITY_OPERATORS

G_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_MANAGER = "hospital_management.group_hospital_manager"


@tagged("post_install", "-at_install", "encounter_patient_payer")
class TestEncounterPatientPayer(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hospital.patient.payer"])
        cls.company = cls.env.company
        cls.officer = cls._make_user("enc_payer_officer", G_OFFICER)
        cls.manager = cls._make_user("enc_payer_manager", G_MANAGER)
        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Encounter Payer Patient"}
        )
        cls.other_patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Encounter Payer Other Patient"}
        )
        cls.agreement = cls._make_agreement()
        cls.eligibility = cls._make_eligibility(cls.patient, cls.agreement)
        cls.eligibility.with_user(cls.officer).action_activate()

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
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

    @classmethod
    def _make_agreement(cls, company=None, activate=True):
        company = company or cls.env.company
        partner = cls.env["res.partner"].sudo().create(
            {"name": "Encounter Payer Partner %s" % uuid.uuid4().hex[:6]}
        )
        payer = cls.env["hospital.payer"].sudo().create(
            {
                "name": "Encounter Payer %s" % uuid.uuid4().hex[:6],
                "payer_type": "insurance",
                "partner_id": partner.id,
                "company_id": company.id,
            }
        )
        agreement = cls.env["hospital.payer.agreement"].sudo().create(
            {
                "payer_id": payer.id,
                "agreement_number": "ENC-%s" % uuid.uuid4().hex[:8].upper(),
                "company_id": company.id,
                "effective_from": cls.today - timedelta(days=30),
                "limit_scope": "unlimited",
            }
        )
        if activate:
            agreement.sudo().action_activate()
        return agreement

    @classmethod
    def _make_eligibility(cls, patient, agreement, effective_from=None):
        return cls.env["hospital.patient.payer"].sudo().create(
            {
                "patient_id": patient.id,
                "agreement_id": agreement.id,
                "effective_from": effective_from or (cls.today - timedelta(days=5)),
            }
        )

    def _encounter(self, patient=None):
        return self.env["hospital.encounter"].sudo().create(
            {
                "patient_id": (patient or self.patient).id,
                "company_id": self.company.id,
            }
        )

    def _attach(self, encounter, eligibility):
        """Write through the capability, as the authoritative workflow does."""
        with payer_identity_capability():
            encounter.sudo().write(
                {"patient_payer_id": eligibility.id if eligibility else False}
            )
        return encounter

    # ==================================================================
    # Attachment
    # ==================================================================
    def test_self_pay_visit_needs_no_eligibility(self):
        encounter = self._encounter()
        self.assertFalse(encounter.patient_payer_id)
        self.assertEqual(encounter.payer_type, "self_pay")

    def test_valid_eligibility_attaches(self):
        encounter = self._attach(self._encounter(), self.eligibility)
        self.assertEqual(encounter.patient_payer_id, self.eligibility)

    def test_clearing_the_eligibility_is_allowed(self):
        encounter = self._attach(self._encounter(), self.eligibility)
        self._attach(encounter, None)
        self.assertFalse(encounter.patient_payer_id)

    # ==================================================================
    # Rejection
    # ==================================================================
    def test_wrong_patient_rejected(self):
        foreign = self._make_eligibility(self.other_patient, self.agreement)
        foreign.sudo().action_activate()
        with self.assertRaises(ValidationError):
            self._attach(self._encounter(), foreign)

    def test_wrong_company_rejected(self):
        other_company = self.env["res.company"].sudo().create(
            {"name": "Encounter Payer Other Co %s" % uuid.uuid4().hex[:6]}
        )
        agreement = self._make_agreement(company=other_company)
        eligibility = self._make_eligibility(self.patient, agreement)
        eligibility.sudo().action_activate()
        with self.assertRaises(ValidationError):
            self._attach(self._encounter(), eligibility)

    def test_draft_eligibility_rejected(self):
        draft = self._make_eligibility(
            self.patient, self._make_agreement(), effective_from=self.today
        )
        self.assertEqual(draft.state, "draft")
        with self.assertRaises(ValidationError):
            self._attach(self._encounter(), draft)

    def test_suspended_eligibility_rejected(self):
        eligibility = self._make_eligibility(self.patient, self._make_agreement())
        eligibility.sudo().action_activate()
        eligibility.sudo().action_suspend()
        with self.assertRaises(ValidationError):
            self._attach(self._encounter(), eligibility)

    def test_expired_eligibility_rejected(self):
        eligibility = self._make_eligibility(self.patient, self._make_agreement())
        eligibility.sudo().action_activate()
        eligibility.sudo().action_expire()
        with self.assertRaises(ValidationError):
            self._attach(self._encounter(), eligibility)

    def test_cancelled_eligibility_rejected(self):
        eligibility = self._make_eligibility(self.patient, self._make_agreement())
        eligibility.sudo().action_activate()
        eligibility.sudo().action_cancel()
        with self.assertRaises(ValidationError):
            self._attach(self._encounter(), eligibility)

    def test_active_but_out_of_window_eligibility_rejected(self):
        """State 'active' is not the same question as 'valid today'.

        The agreement is closed behind the eligibility, which is precisely the
        composition is_valid_today performs and the reason this test does not
        simply assert on the eligibility's own state.
        """
        agreement = self._make_agreement()
        eligibility = self._make_eligibility(self.patient, agreement)
        eligibility.sudo().action_activate()
        agreement.sudo().action_expire()

        self.assertEqual(eligibility.state, "active")
        self.assertFalse(eligibility.is_valid_today)
        with self.assertRaises(ValidationError):
            self._attach(self._encounter(), eligibility)

    # ==================================================================
    # THE phase boundary: identity is not money
    # ==================================================================
    def test_attachment_leaves_payer_type_unchanged(self):
        encounter = self._attach(self._encounter(), self.eligibility)
        self.assertEqual(encounter.payer_type, "self_pay")
        self.assertFalse(encounter.payer_id)

    def test_attachment_does_not_produce_credit_authorized_clearance(self):
        """The regression test for the whole design decision.

        Had patient_payer_id been expressed as payer_type, this would read
        'credit_authorized' with cleared=True and no money taken.
        """
        encounter = self._encounter()
        engine = self.env["hospital.billing.engine"]
        before = engine.check_financial_clearance(encounter)

        self._attach(encounter, self.eligibility)
        self.env.invalidate_all()
        after = engine.check_financial_clearance(encounter)

        self.assertNotEqual(after["state"], "credit_authorized")
        self.assertEqual(after["state"], before["state"])
        self.assertEqual(after["cleared"], before["cleared"])

    def test_payer_responsibility_mode_remains_off(self):
        self.assertEqual(self.company.payer_responsibility_mode, "off")
        self._attach(self._encounter(), self.eligibility)
        self.env.invalidate_all()
        self.assertEqual(self.company.payer_responsibility_mode, "off")

    def test_allocate_payer_is_still_unimplemented(self):
        """Phase 2B must not have started the responsibility engine."""
        with self.assertRaises(UserError):
            self.env["hospital.billing.engine"].allocate_payer(
                self.env["hospital.billing.account"]
            )

    # ==================================================================
    # Write guard
    # ==================================================================
    def test_patient_payer_id_is_not_writable_without_the_capability(self):
        encounter = self._encounter()
        with self.assertRaises(AccessError):
            encounter.with_user(self.officer).write(
                {"patient_payer_id": self.eligibility.id}
            )

    def test_authority_tuple_matches_the_eligibility_operators(self):
        """Two constants, one boundary. They must not drift apart."""
        self.assertEqual(
            set(PAYER_IDENTITY_AUTHORITY), set(ELIGIBILITY_OPERATORS)
        )

    def test_locked_encounter_still_refuses_the_payer_field(self):
        """No new lock is added: the existing LOCKED_STATES guard covers it."""
        encounter = self._attach(self._encounter(), self.eligibility)
        encounter.sudo().write({"state": "cancelled"})
        self.assertNotIn(
            "patient_payer_id",
            self.env["hospital.encounter"]._get_locked_writable_fields(),
        )
        with self.assertRaises(UserError):
            with payer_identity_capability():
                encounter.sudo().write({"patient_payer_id": False})

"""Phase 2A patient eligibility foundation.

No test here selects a payer for a visit or changes billing behavior.  Exact
Front Desk Nurse ACL coverage lives downstream in yoya_reception_bridge.
"""

import inspect
import re
import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.hospital_billing.models import billing_engine


G_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_MANAGER = "hospital_management.group_hospital_manager"
G_NURSE = "hospital_management.group_hospital_nurse"


@tagged("post_install", "-at_install", "patient_payer_foundation")
class TestPatientPayerFoundation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.today = fields.Date.context_today(cls.env["hospital.patient.payer"])
        cls.officer = cls._make_user("elig_officer", [G_OFFICER])
        cls.manager = cls._make_user("elig_manager", [G_MANAGER])
        cls.nurse = cls._make_user("elig_nurse", [G_NURSE])
        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Phase 2A Patient"}
        )
        cls.payer = cls._make_payer("Phase 2A Payer")
        cls.agreement = cls._make_agreement(
            cls.payer,
            effective_from=cls.today - timedelta(days=30),
            activate=True,
        )

    @classmethod
    def _make_user(cls, label, group_xmlids):
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
                        + [cls.env.ref(xmlid).id for xmlid in group_xmlids],
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
    def _make_agreement(cls, payer, activate=False, **overrides):
        vals = {
            "payer_id": payer.id,
            "agreement_number": "ELIG-%s" % uuid.uuid4().hex[:8].upper(),
            "company_id": payer.company_id.id,
            "effective_from": cls.today,
            "limit_scope": "unlimited",
        }
        vals.update(overrides)
        agreement = cls.env["hospital.payer.agreement"].sudo().create(vals)
        if activate:
            agreement.with_user(cls.manager).action_activate()
        return agreement

    def _eligibility(self, agreement=None, user=None, **overrides):
        agreement = agreement or self.agreement
        vals = {
            "patient_id": self.patient.id,
            "agreement_id": agreement.id,
            "effective_from": max(self.today, agreement.effective_from),
        }
        vals.update(overrides)
        return self.env["hospital.patient.payer"].with_user(
            user or self.officer
        ).create(vals)

    def test_01_sequence_generated(self):
        eligibility = self._eligibility()
        self.assertRegex(eligibility.name, r"^ELIG/\d{5,}$")

    def test_02_create_defaults_to_draft(self):
        eligibility = self._eligibility()
        self.assertEqual(eligibility.state, "draft")
        self.assertEqual(eligibility.relationship_to_principal, "self")

    def test_03_create_directly_active_rejected(self):
        with self.assertRaises(UserError):
            self._eligibility(state="active")

    def test_04_payer_is_derived_from_agreement(self):
        self.assertEqual(self._eligibility().payer_id, self.payer)

    def test_05_company_is_derived_from_agreement(self):
        self.assertEqual(self._eligibility().company_id, self.company)

    def test_05b_caller_cannot_create_company_drift(self):
        company_model = self.env["res.company"].sudo()
        conflicting_company = company_model.search(
            [("id", "!=", self.agreement.company_id.id)],
            order="id",
            limit=1,
        )
        # A second res.company is not an eligibility fixture: Stock installs a
        # warehouse, locations, routes and rules for every company creation.
        # Reuse one when the database has it.  On a genuine single-company test
        # database, use the next unused ID; patient_payer.create() must discard
        # caller input before any FK or stored-related value can consume it.
        conflicting_company_id = conflicting_company.id
        if not conflicting_company_id:
            highest_company = company_model.search([], order="id desc", limit=1)
            conflicting_company_id = highest_company.id + 1

        eligibility = self.env["hospital.patient.payer"].sudo().create(
            {
                "patient_id": self.patient.id,
                "agreement_id": self.agreement.id,
                "effective_from": self.today,
                "company_id": conflicting_company_id,
            }
        )
        self.assertEqual(eligibility.company_id, self.agreement.company_id)
        self.assertNotEqual(eligibility.company_id.id, conflicting_company_id)
        with self.assertRaises(UserError):
            eligibility.write({"company_id": conflicting_company_id})

    def test_06_currency_is_derived_from_agreement(self):
        self.assertEqual(self._eligibility().currency_id, self.company.currency_id)

    def test_07_window_inside_agreement_accepted(self):
        finite = self._make_agreement(
            self._make_payer("Finite Accepted"),
            effective_from=self.today - timedelta(days=10),
            effective_to=self.today + timedelta(days=10),
        )
        eligibility = self._eligibility(agreement=finite, effective_to=False)
        self.assertFalse(eligibility.effective_to)

    def test_08_start_before_agreement_rejected(self):
        agreement = self._make_agreement(self._make_payer("Late Agreement"))
        with self.assertRaises(ValidationError):
            self._eligibility(
                agreement=agreement,
                effective_from=agreement.effective_from - timedelta(days=1),
            )

    def test_09_end_after_finite_agreement_rejected(self):
        agreement = self._make_agreement(
            self._make_payer("Finite Agreement"),
            effective_to=self.today + timedelta(days=5),
        )
        with self.assertRaises(ValidationError):
            self._eligibility(
                agreement=agreement,
                effective_to=agreement.effective_to + timedelta(days=1),
            )

    @mute_logger("odoo.sql_db")
    def test_10_own_invalid_date_range_rejected(self):
        with self.assertRaises(Exception):
            self._eligibility(effective_to=self.today - timedelta(days=1))

    def test_11_member_limit_required_for_member_scope(self):
        agreement = self._make_agreement(
            self._make_payer("Member Scope"),
            limit_scope="member",
            limit_amount=1000.0,
        )
        with self.assertRaises(ValidationError):
            self._eligibility(agreement=agreement)
        eligibility = self._eligibility(
            agreement=agreement, member_limit_amount=0.0
        )
        self.assertEqual(eligibility.member_limit_amount, 0.0)

    def test_12_member_limit_forbidden_for_non_member_scope(self):
        with self.assertRaises(ValidationError):
            self._eligibility(member_limit_amount=0.0)

    @mute_logger("odoo.sql_db")
    def test_13_negative_member_limit_rejected(self):
        agreement = self._make_agreement(
            self._make_payer("Negative Member Scope"),
            limit_scope="member",
            limit_amount=1000.0,
        )
        with self.assertRaises(Exception):
            self._eligibility(agreement=agreement, member_limit_amount=-1.0)

    def test_14_active_eligibility_is_valid_today(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        self.assertTrue(eligibility.is_valid_today)

    def test_15_draft_is_not_valid_today(self):
        self.assertFalse(self._eligibility().is_valid_today)

    def test_16_suspended_is_not_valid_today(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        eligibility.with_user(self.officer).action_suspend()
        self.assertFalse(eligibility.is_valid_today)

    def test_17_expired_is_not_valid_today(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        eligibility.with_user(self.officer).action_expire()
        self.assertFalse(eligibility.is_valid_today)

    def test_18_cancelled_is_not_valid_today(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_cancel()
        self.assertFalse(eligibility.is_valid_today)

    def test_19_suspended_agreement_invalidates_eligibility(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        self.agreement.with_user(self.manager).action_suspend("Phase 2A test")
        self.assertFalse(eligibility.is_valid_today)

    def test_20_expired_or_out_of_window_agreement_is_invalid(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        self.agreement.with_user(self.manager).action_expire()
        self.assertFalse(eligibility.is_valid_today)

        future = self._make_agreement(
            self._make_payer("Future Agreement"),
            effective_from=self.today + timedelta(days=10),
            activate=True,
        )
        future_eligibility = self._eligibility(
            agreement=future, effective_from=future.effective_from
        )
        future_eligibility.with_user(self.officer).action_activate()
        self.assertFalse(future_eligibility.is_valid_today)

    def test_21_overlapping_active_same_patient_agreement_rejected(self):
        first = self._eligibility(effective_to=self.today + timedelta(days=10))
        first.with_user(self.officer).action_activate()
        second = self._eligibility(effective_from=self.today + timedelta(days=5))
        with self.assertRaises(UserError):
            second.with_user(self.officer).action_activate()

    def test_22_non_overlapping_active_same_patient_agreement_allowed(self):
        first = self._eligibility(effective_to=self.today + timedelta(days=1))
        first.with_user(self.officer).action_activate()
        second = self._eligibility(effective_from=self.today + timedelta(days=2))
        second.with_user(self.officer).action_activate()
        self.assertEqual(second.state, "active")

    def test_23_different_agreements_same_patient_allowed(self):
        other = self._make_agreement(
            self._make_payer("Other Active Agreement"),
            effective_from=self.today - timedelta(days=5),
            activate=True,
        )
        first = self._eligibility()
        second = self._eligibility(agreement=other)
        first.with_user(self.officer).action_activate()
        second.with_user(self.officer).action_activate()
        self.assertEqual(first.state, "active")
        self.assertEqual(second.state, "active")

    def test_24_insurance_officer_can_activate(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        self.assertEqual(eligibility.state, "active")

    def test_25_manager_can_activate(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.manager).action_activate()
        self.assertEqual(eligibility.state, "active")

    def test_26_clinical_front_desk_fallback_cannot_activate(self):
        eligibility = self._eligibility()
        with self.assertRaises(AccessError):
            eligibility.with_user(self.nurse).action_activate()

    def test_27_activation_stamps_verifier(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        self.assertEqual(eligibility.verified_by_id, self.officer)
        self.assertTrue(eligibility.verified_at)

    def test_28_direct_write_state_bypass_rejected(self):
        eligibility = self._eligibility()
        with self.assertRaises(AccessError):
            eligibility.with_user(self.nurse).write({"state": "active"})

    def test_29_valid_suspension(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        eligibility.with_user(self.officer).action_suspend()
        self.assertEqual(eligibility.state, "suspended")

    def test_30_valid_resume(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        eligibility.with_user(self.officer).action_suspend()
        eligibility.with_user(self.officer).action_resume()
        self.assertEqual(eligibility.state, "active")

    def test_31_invalid_transition_rejected(self):
        with self.assertRaises(UserError):
            self._eligibility().with_user(self.officer).action_expire()

    def test_32_non_draft_unlink_rejected(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        with self.assertRaises(UserError):
            eligibility.with_user(self.officer).unlink()

    def test_33_draft_unlink_allowed_for_authorized_role(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).unlink()
        self.assertFalse(eligibility.exists())

    def test_34_company_isolation(self):
        company_model = self.env["res.company"].sudo()
        other_company = company_model.search(
            [("id", "!=", self.company.id)],
            order="id",
            limit=1,
        )
        if not other_company:
            # This controlled database is normally single-company. Calling
            # res.company.create() is not a company-rule fixture: installed
            # Resource/Account/Stock modules bootstrap calendars, warehouses,
            # locations, picking types, routes and rules before this test ever
            # reaches hospital.patient.payer.
            #
            # Build one genuine, FK-valid res.company row directly instead.
            # Every installed stored column is cloned from the known-good
            # current company, while name and partner are unique. SQL stays
            # inside TransactionCase's rollback and invokes no business setup.
            partner = self.env["res.partner"].sudo().create(
                {"name": "Eligibility Isolation Company %s" % uuid.uuid4().hex[:8]}
            )
            self.env.cr.execute(
                """
                SELECT column_name
                  FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 'res_company'
                   AND column_name NOT IN (
                       'id', 'name', 'partner_id', 'parent_path',
                       'create_uid', 'create_date', 'write_uid', 'write_date'
                   )
                 ORDER BY ordinal_position
                """
            )
            cloned_columns = [row[0] for row in self.env.cr.fetchall()]
            if not all(
                column.replace("_", "").isalnum() for column in cloned_columns
            ):
                self.fail("Unexpected res_company column name in test fixture")
            quoted_columns = ", ".join(
                '"%s"' % column for column in cloned_columns
            )
            self.env.cr.execute(
                """
                INSERT INTO res_company (name, partner_id, {columns})
                SELECT %s, %s, {columns}
                  FROM res_company
                 WHERE id = %s
                RETURNING id
                """.format(columns=quoted_columns),
                [partner.name, partner.id, self.company.id],
            )
            other_company_id = self.env.cr.fetchone()[0]
            self.env.cr.execute(
                "UPDATE res_company SET parent_path = %s WHERE id = %s",
                ["%s/" % other_company_id, other_company_id],
            )
            company_model.invalidate_model()
            other_company = company_model.browse(other_company_id)
            self.assertTrue(other_company.exists())

        foreign_payer = self._make_payer("Foreign Eligibility Payer", other_company)
        foreign_agreement = self._make_agreement(foreign_payer)
        foreign_patient = self.env["hospital.patient"].sudo().create(
            {"name": "Foreign Eligibility Patient"}
        )
        foreign = self.env["hospital.patient.payer"].sudo().create(
            {
                "patient_id": foreign_patient.id,
                "agreement_id": foreign_agreement.id,
                "effective_from": self.today,
            }
        )
        mine = self._eligibility()
        visible = self.env["hospital.patient.payer"].with_user(self.officer).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(foreign, visible)

    def test_35_agreement_relation_is_readable(self):
        eligibility = self._eligibility()
        eligibility.with_user(self.officer).action_activate()
        self.assertIn(eligibility, self.agreement.eligibility_ids)

    def test_36_allocate_payer_remains_stub(self):
        with self.assertRaises(UserError) as caught:
            self.env["hospital.billing.engine"].allocate_payer(
                self.env["hospital.billing.account"]
            )
        self.assertIn("not implemented", str(caught.exception).lower())

    def test_37_financial_clearance_does_not_read_eligibility(self):
        source = inspect.getsource(
            billing_engine.HospitalBillingEngine.check_financial_clearance
        )
        self.assertNotIn("hospital.patient.payer", source)
        self.assertNotIn("eligibility_id", source)
        self.assertNotIn("eligibility_ids", source)
        self.assertNotRegex(
            source,
            r"""envs*[s*['"]hospital.patient.payer['"]s*]""",
        )

    def test_38_no_legacy_module_dependency(self):
        module = self.env["ir.module.module"].search(
            [("name", "=", "hospital_billing")], limit=1
        )
        dependency_names = set(module.dependencies_id.mapped("name"))
        self.assertFalse(
            {"hospital_insurance", "hospital_insurance_accounting"} & dependency_names
        )

    def test_39_feature_flag_remains_off(self):
        self.assertEqual(self.company.payer_responsibility_mode, "off")
        self.assertNotIn(
            "payer_responsibility_mode",
            inspect.getsource(billing_engine.HospitalBillingEngine.allocate_payer),
        )

    def test_40_agreement_cannot_shorten_past_explicit_eligibility(self):
        finite = self._make_agreement(
            self._make_payer("Parent Window Guard"),
            effective_from=self.today - timedelta(days=5),
            effective_to=self.today + timedelta(days=20),
        )
        self._eligibility(
            agreement=finite,
            effective_to=self.today + timedelta(days=10),
        )
        with self.assertRaises(ValidationError):
            finite.write({"effective_to": self.today + timedelta(days=5)})

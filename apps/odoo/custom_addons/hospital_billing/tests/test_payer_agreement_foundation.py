"""Phase 1A/1B: the payer master and the agreement lifecycle.

These tests pin down three things that are easy to regress and expensive to
discover late:

  1. WHO may bring an agreement into force. The check lives in write(), not only
     in the buttons, so the negative tests deliberately attack the raw ORM path
     rather than calling action_activate(). A guard that only exists in a button
     is not a guard.

  2. THAT ACTIVE TERMS ARE FROZEN. This is the whole reason versioning exists: a
     limit raised next year must not reach a visit that happened last year.

  3. THAT A BOUNDED LIMIT CANNOT BE ACTIVATED YET. Displaying a ceiling that
     nothing enforces is worse than having no ceiling, because staff would
     trust it.

Nothing here touches live billing: no encounter, no charge, no receipt, no
clearance. Phase 1 changes no behaviour, and the last test asserts exactly that.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.hospital_billing.models import payer_agreement

G_INSURANCE_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_MANAGER = "hospital_management.group_hospital_manager"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_FRONT_DESK_NURSE_FALLBACK = "hospital_management.group_hospital_nurse"


@tagged("post_install", "-at_install", "payer_agreement_foundation")
class TestPayerAgreementFoundation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.today = fields.Date.context_today(cls.env["hospital.payer"])

        cls.officer = cls._make_user("payer_officer", [G_INSURANCE_OFFICER])
        cls.manager = cls._make_user("payer_manager", [G_MANAGER])
        cls.accountant = cls._make_user("payer_accountant", [G_ACCOUNTANT])
        # Stands in for "clinical staff with no payer authority at all".
        cls.nurse = cls._make_user("payer_nurse", [G_FRONT_DESK_NURSE_FALLBACK])

        cls.partner = cls.env["res.partner"].sudo().create({"name": "Nyala Insurance"})
        cls.payer = cls.env["hospital.payer"].sudo().create(
            {
                "name": "Nyala Insurance",
                "code": "NIC",
                "payer_type": "insurance",
                "partner_id": cls.partner.id,
                "company_id": cls.company.id,
            }
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, login, group_xmlids):
        return (
            cls.env["res.users"]
            .sudo()
            .create(
                {
                    "name": login,
                    "login": "%s_%s" % (login, uuid.uuid4().hex[:6]),
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

    def _agreement_vals(self, **overrides):
        vals = {
            "payer_id": self.payer.id,
            "agreement_number": "CONTRACT-%s" % uuid.uuid4().hex[:6].upper(),
            "company_id": self.company.id,
            "effective_from": self.today,
            "limit_scope": "unlimited",
        }
        vals.update(overrides)
        return vals

    def _draft(self, user=None, **overrides):
        model = self.env["hospital.payer.agreement"]
        if user:
            model = model.with_user(user)
        return model.create(self._agreement_vals(**overrides))

    # ==================================================================
    # PAYER MASTER
    # ==================================================================
    def test_01_create_payer(self):
        payer = self.env["hospital.payer"].sudo().create(
            {
                "name": "Ethio Telecom",
                "code": "ETC",
                "payer_type": "corporate",
                "partner_id": self.env["res.partner"].sudo().create({"name": "Ethio Telecom"}).id,
            }
        )
        self.assertEqual(payer.company_id, self.company)
        self.assertEqual(payer.currency_id, self.company.currency_id)
        self.assertEqual(payer.display_name, "[ETC] Ethio Telecom")

    def test_02_partner_is_required(self):
        with self.assertRaises(Exception):
            self.env["hospital.payer"].sudo().create(
                {"name": "No Partner", "payer_type": "corporate"}
            )

    def test_03_company_defaults_and_is_required(self):
        payer = self.env["hospital.payer"].sudo().create(
            {
                "name": "Defaulted Company",
                "payer_type": "ngo",
                "partner_id": self.env["res.partner"].sudo().create({"name": "NGO"}).id,
            }
        )
        self.assertTrue(payer.company_id, "company_id must default, never be empty")

    @mute_logger("odoo.sql_db")
    def test_04_duplicate_partner_company_rejected(self):
        with self.assertRaises(Exception):
            self.env["hospital.payer"].sudo().create(
                {
                    "name": "Duplicate Of Nyala",
                    "payer_type": "insurance",
                    "partner_id": self.partner.id,
                    "company_id": self.company.id,
                }
            )

    def test_05_blank_code_does_not_collide(self):
        """'' normalises to NULL, so any number of payers may have no code."""
        make = lambda label: self.env["hospital.payer"].sudo().create(
            {
                "name": label,
                "code": "",
                "payer_type": "donor",
                "partner_id": self.env["res.partner"].sudo().create({"name": label}).id,
            }
        )
        first, second = make("Donor A"), make("Donor B")
        self.assertFalse(first.code)
        self.assertFalse(second.code)

    def test_06_cross_company_isolation(self):
        other_company = self.env["res.company"].sudo().create({"name": "Second Hospital"})
        other_partner = self.env["res.partner"].sudo().create(
            {"name": "Other Co Payer", "company_id": other_company.id}
        )
        foreign = self.env["hospital.payer"].sudo().create(
            {
                "name": "Other Co Payer",
                "payer_type": "corporate",
                "partner_id": other_partner.id,
                "company_id": other_company.id,
            }
        )
        visible = self.env["hospital.payer"].with_user(self.officer).search([])
        self.assertIn(self.payer, visible)
        self.assertNotIn(
            foreign, visible, "the global company rule must hide another company's payers"
        )

    def test_07_payer_type_values(self):
        keys = {key for key, _label in self.env["hospital.payer"]._fields["payer_type"].selection}
        self.assertEqual(
            keys,
            {
                "insurance",
                "corporate",
                "government_program",
                "ngo",
                "donor",
                "credit_agreement",
                "other",
            },
        )

    def test_07b_partner_company_mismatch_rejected(self):
        other_company = self.env["res.company"].sudo().create({"name": "Mismatch Co"})
        partner = self.env["res.partner"].sudo().create(
            {"name": "Foreign Partner", "company_id": other_company.id}
        )
        with self.assertRaises(ValidationError):
            self.env["hospital.payer"].sudo().create(
                {
                    "name": "Mismatched",
                    "payer_type": "corporate",
                    "partner_id": partner.id,
                    "company_id": self.company.id,
                }
            )

    # ==================================================================
    # AGREEMENT: CREATION AND CONSTRAINTS
    # ==================================================================
    def test_08_draft_creation(self):
        agreement = self._draft()
        self.assertEqual(agreement.state, "draft")
        self.assertEqual(agreement.version, 1)
        self.assertFalse(agreement.is_valid_today)

    def test_09_sequence_generated(self):
        agreement = self._draft()
        self.assertNotEqual(agreement.name, "New")
        self.assertTrue(
            agreement.name.startswith("AGR/"), "expected AGR/<year>/ prefix, got %s" % agreement.name
        )

    @mute_logger("odoo.sql_db")
    def test_10_number_version_company_unique(self):
        first = self._draft()
        with self.assertRaises(Exception):
            self._draft(agreement_number=first.agreement_number)

    @mute_logger("odoo.sql_db")
    def test_11_invalid_date_range_rejected(self):
        with self.assertRaises(Exception):
            self._draft(
                effective_from=self.today,
                effective_to=self.today - timedelta(days=1),
            )

    @mute_logger("odoo.sql_db")
    def test_12_negative_limit_rejected(self):
        with self.assertRaises(Exception):
            self._draft(limit_scope="agreement", limit_amount=-1.0)

    @mute_logger("odoo.sql_db")
    def test_13_negative_payment_terms_rejected(self):
        with self.assertRaises(Exception):
            self._draft(payment_terms_days=-1)

    def test_14_limit_scope_is_mandatory(self):
        """No default, and required: a Manager must state the scope explicitly."""
        field = self.env["hospital.payer.agreement"]._fields["limit_scope"]
        self.assertTrue(field.required)
        self.assertIsNone(
            field.default, "limit_scope must have NO default; the hospital has not confirmed it"
        )
        vals = self._agreement_vals()
        vals.pop("limit_scope")
        with self.assertRaises(Exception):
            self.env["hospital.payer.agreement"].create(vals)

    def test_15_only_the_organisation_wide_pool_stays_blocked(self):
        """The phase gate was NARROWED by the benefit engine, not removed.

        'member' and 'visit' now have real enforcement: consumption is derived
        from hospital.charge.responsibility and the evaluator caps every
        permitted sponsor amount by what remains. So they activate.

        'agreement' (organisation-wide pool) does not. Nothing aggregates across
        the members of a contract yet, so its ceiling would still be decorative,
        which is precisely the condition this gate exists to prevent.
        """
        blocked = self._draft(limit_scope="agreement", limit_amount=50000.0)
        with self.assertRaises(UserError) as caught:
            blocked.with_user(self.manager).action_activate()
        self.assertIn("Agreement-wide Pool", str(caught.exception))
        self.assertEqual(blocked.state, "draft")

        for scope in ("member", "visit"):
            with self.subTest(scope=scope):
                # A DISTINCT payer per scope: the EXCLUDE constraint forbids two
                # active agreements for one payer over overlapping dates, and
                # both of these activate. Reusing cls.payer would test that
                # constraint rather than this gate.
                partner = self.env["res.partner"].sudo().create(
                    {"name": "Bounded Scope %s" % uuid.uuid4().hex[:6]}
                )
                payer = self.env["hospital.payer"].sudo().create(
                    {
                        "name": partner.name,
                        "payer_type": "insurance",
                        "partner_id": partner.id,
                        "company_id": self.company.id,
                    }
                )
                agreement = self._draft(
                    payer_id=payer.id, limit_scope=scope, limit_amount=50000.0
                )
                agreement.with_user(self.manager).action_activate()
                self.assertEqual(agreement.state, "active")

    def test_15b_bounded_scope_still_needs_a_positive_amount(self):
        for scope in ("member", "visit"):
            with self.subTest(scope=scope):
                agreement = self._draft(limit_scope=scope, limit_amount=0.0)
                with self.assertRaises(UserError):
                    agreement.with_user(self.manager).action_activate()
                self.assertEqual(agreement.state, "draft")

    def test_16_unlimited_activation_succeeds(self):
        agreement = self._draft()
        agreement.with_user(self.manager).action_activate()
        self.assertEqual(agreement.state, "active")
        self.assertEqual(agreement.activated_by_id, self.manager)
        self.assertTrue(agreement.activated_at)
        self.assertTrue(agreement.is_valid_today)

    def test_17_agreement_price_activation_blocked(self):
        agreement = self._draft(tariff_mode="agreement_price")
        with self.assertRaises(UserError) as caught:
            agreement.with_user(self.manager).action_activate()
        self.assertIn("tariff", str(caught.exception).lower())
        self.assertEqual(agreement.state, "draft")

    # ==================================================================
    # AGREEMENT: AUTHORIZATION BOUNDARY
    # ==================================================================
    def test_18_clinical_user_cannot_activate(self):
        agreement = self._draft()
        with self.assertRaises(AccessError):
            agreement.with_user(self.nurse).action_activate()
        self.assertEqual(agreement.state, "draft")

    def test_19_insurance_officer_cannot_activate(self):
        """Drafting terms and committing to them are separable duties."""
        agreement = self._draft()
        with self.assertRaises(AccessError):
            agreement.with_user(self.officer).action_activate()
        self.assertEqual(agreement.state, "draft")

    def test_19b_raw_orm_state_write_is_guarded(self):
        """The boundary is in write(), not only in the button.

        An accountant holds write access through the ACL, so if the guard lived
        only in action_activate() this would silently activate an agreement.
        """
        agreement = self._draft()
        with self.assertRaises(AccessError):
            agreement.with_user(self.accountant).write({"state": "active"})
        agreement.invalidate_recordset()
        self.assertEqual(agreement.state, "draft")

    def test_19c_unsupported_transition_refused(self):
        agreement = self._draft()
        with self.assertRaises(UserError):
            agreement.with_user(self.manager).write({"state": "superseded"})

    def test_20_manager_can_activate(self):
        agreement = self._draft()
        agreement.with_user(self.manager).action_activate()
        self.assertEqual(agreement.state, "active")

    def test_20b_officer_may_draft(self):
        agreement = self._draft(user=self.officer)
        self.assertEqual(agreement.state, "draft")
        agreement.with_user(self.officer).write({"payment_terms_days": 45})
        self.assertEqual(agreement.payment_terms_days, 45)

    # ==================================================================
    # AGREEMENT: IMMUTABILITY
    # ==================================================================
    def test_21_active_terms_are_frozen(self):
        agreement = self._draft()
        agreement.with_user(self.manager).action_activate()
        for field_name, value in (
            ("limit_amount", 999.0),
            ("limit_scope", "agreement"),
            ("authorization_required", True),
            ("guarantee_required", True),
            ("tariff_mode", "agreement_price"),
            ("effective_from", self.today + timedelta(days=5)),
            ("agreement_number", "REWRITTEN"),
        ):
            with self.assertRaises(UserError, msg="%s must be frozen" % field_name):
                agreement.with_user(self.manager).write({field_name: value})

    def test_21b_permitted_fields_still_writable_when_active(self):
        agreement = self._draft()
        agreement.with_user(self.manager).action_activate()
        agreement.with_user(self.manager).write(
            {"notes": "Reviewed", "payment_terms_days": 60}
        )
        self.assertEqual(agreement.payment_terms_days, 60)

    def test_21c_effective_to_may_shorten_but_not_extend(self):
        agreement = self._draft(effective_to=self.today + timedelta(days=90))
        agreement.with_user(self.manager).action_activate()

        agreement.with_user(self.manager).write(
            {"effective_to": self.today + timedelta(days=30)}
        )
        self.assertEqual(agreement.effective_to, self.today + timedelta(days=30))

        with self.assertRaises(UserError):
            agreement.with_user(self.manager).write(
                {"effective_to": self.today + timedelta(days=60)}
            )
        with self.assertRaises(UserError):
            agreement.with_user(self.manager).write({"effective_to": False})

    # ==================================================================
    # AGREEMENT: SUSPEND / RESUME / TERMINATE
    # ==================================================================
    def test_22_suspension_requires_reason(self):
        agreement = self._draft()
        agreement.with_user(self.manager).action_activate()
        with self.assertRaises(UserError):
            agreement.with_user(self.manager).action_suspend()
        self.assertEqual(agreement.state, "active")

        agreement.with_user(self.manager).action_suspend(reason="Payer under review")
        self.assertEqual(agreement.state, "suspended")

    def test_23_resume_revalidates(self):
        agreement = self._draft()
        agreement.with_user(self.manager).action_activate()
        agreement.with_user(self.manager).action_suspend(reason="Temporary hold")
        agreement.with_user(self.manager).action_resume()
        self.assertEqual(agreement.state, "active")

    def test_23b_resume_blocked_by_a_competing_active_agreement(self):
        first = self._draft()
        first.with_user(self.manager).action_activate()
        first.with_user(self.manager).action_suspend(reason="Hold")

        second = self._draft()
        second.with_user(self.manager).action_activate()

        with self.assertRaises(UserError):
            first.with_user(self.manager).action_resume()
        self.assertEqual(first.state, "suspended")

    def test_24_termination_requires_reason(self):
        agreement = self._draft()
        agreement.with_user(self.manager).action_activate()
        with self.assertRaises(UserError):
            agreement.with_user(self.manager).action_terminate()
        agreement.with_user(self.manager).action_terminate(reason="Contract ended early")
        self.assertEqual(agreement.state, "terminated")
        self.assertEqual(agreement.terminated_by_id, self.manager)

    def test_25_terminated_agreement_stays_readable(self):
        agreement = self._draft()
        agreement.with_user(self.manager).action_activate()
        agreement.with_user(self.manager).action_terminate(reason="Ended")
        self.assertTrue(
            agreement.active, "history must never be archived by a workflow"
        )
        self.assertIn(
            agreement,
            self.env["hospital.payer.agreement"].with_user(self.officer).search([]),
        )

    # ==================================================================
    # AGREEMENT: VERSIONING
    # ==================================================================
    def test_26_amendment_creates_v2_draft(self):
        v1 = self._draft(limit_scope="unlimited")
        v1.with_user(self.manager).action_activate()
        v2 = v1.with_user(self.manager)._create_amendment(
            effective_from=self.today + timedelta(days=30)
        )
        self.assertEqual(v2.state, "draft")
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.supersedes_id, v1)
        self.assertEqual(v2.agreement_number, v1.agreement_number)
        self.assertNotEqual(v2.name, v1.name)

    def test_27_v1_untouched_while_v2_is_draft(self):
        v1 = self._draft()
        v1.with_user(self.manager).action_activate()
        original_to = v1.effective_to
        v1.with_user(self.manager)._create_amendment(
            effective_from=self.today + timedelta(days=30)
        )
        v1.invalidate_recordset()
        self.assertEqual(v1.state, "active")
        self.assertEqual(v1.effective_to, original_to)
        self.assertFalse(v1.superseded_by_id)

    def test_28_v2_activation_supersedes_v1_atomically(self):
        """Supersession must satisfy the LIVE exclusion constraint, not just the cache.

        v1 and v2 overlap by construction: v1 runs today to open-ended, v2
        starts in 30 days. There is therefore a moment during activation when
        both rows would match `WHERE state = 'active'` over intersecting
        windows. action_activate flushes the predecessor before writing the
        successor precisely so PostgreSQL never observes that moment.

        The explicit flush_all() at the end is the real assertion: without it
        this test would pass on ORM cache values alone and would say nothing
        about whether the database accepted the result.
        """
        start_v2 = self.today + timedelta(days=30)
        v1 = self._draft()
        v1.with_user(self.manager).action_activate()
        v2 = v1.with_user(self.manager)._create_amendment(effective_from=start_v2)
        v2.with_user(self.manager).action_activate()

        # Any ordering defect surfaces here as an ExclusionViolation.
        self.env.flush_all()

        v1.invalidate_recordset()
        v2.invalidate_recordset()
        self.assertEqual(v1.state, "superseded")
        self.assertEqual(v1.superseded_by_id, v2)
        self.assertEqual(v1.effective_to, start_v2 - timedelta(days=1))
        self.assertEqual(v2.state, "active")

        # And confirm it against the database rather than the ORM, so a stale
        # cache cannot make a failed supersession look successful.
        self.env.cr.execute(
            "SELECT state, effective_to FROM hospital_payer_agreement WHERE id = %s",
            [v1.id],
        )
        db_state, db_effective_to = self.env.cr.fetchone()
        self.assertEqual(db_state, "superseded")
        self.assertEqual(db_effective_to, start_v2 - timedelta(days=1))

    def test_28b_failed_successor_activation_rolls_back_supersession(self):
        """If the successor's own write fails, the predecessor must unwind with it.

        This is the case the savepoint exists for. Everything is validated
        before the first write, so the only way to reach a half-applied
        supersession is a failure DURING the two-record mutation. That is
        forced here by making the successor's activation raise.

        _write_activation is patched rather than something deeper because it is
        the explicit seam the production code introduces for exactly this
        ordering; patching it is not reaching into an implementation detail.
        """
        start_v2 = self.today + timedelta(days=30)
        v1 = self._draft()
        v1.with_user(self.manager).action_activate()
        v2 = v1.with_user(self.manager)._create_amendment(effective_from=start_v2)
        self.env.flush_all()

        original_state = v1.state
        original_effective_to = v1.effective_to

        boom = UserError("Simulated failure during successor activation")
        with patch.object(type(v2), "_write_activation", side_effect=boom):
            with self.assertRaises(UserError):
                v2.with_user(self.manager).action_activate()

        # Read straight from PostgreSQL: the savepoint rollback also clears the
        # ORM cache, so anything still in memory proves nothing.
        self.env.cr.execute(
            """
            SELECT state, effective_to, superseded_by_id
              FROM hospital_payer_agreement
             WHERE id = %s
            """,
            [v1.id],
        )
        db_state, db_effective_to, db_superseded_by = self.env.cr.fetchone()
        self.assertEqual(
            db_state, original_state, "v1 must not stay superseded after a failed activation"
        )
        # `or None`: an unset Date reads as False through the ORM but comes back
        # as NULL/None from psycopg, and False != None.
        self.assertEqual(db_effective_to, original_effective_to or None)
        self.assertIsNone(db_superseded_by)

        self.env.cr.execute(
            "SELECT state FROM hospital_payer_agreement WHERE id = %s", [v2.id]
        )
        self.assertEqual(self.env.cr.fetchone()[0], "draft")

    def test_29_active_windows_do_not_overlap(self):
        v1 = self._draft()
        v1.with_user(self.manager).action_activate()
        clashing = self._draft(agreement_number="OTHER-%s" % uuid.uuid4().hex[:4])
        with self.assertRaises(UserError) as caught:
            clashing.with_user(self.manager).action_activate()
        self.assertIn("overlap", str(caught.exception).lower())
        self.assertEqual(clashing.state, "draft")

    def test_30_failed_v2_activation_leaves_v1_untouched(self):
        """A failed amendment activation must leave the predecessor untouched.

        THE ARRANGEMENT IS THE DELICATE PART, and an earlier version of this
        test got it wrong in a way worth recording.

        It built the competing agreement by writing state='active' directly,
        which bypasses action_activate and therefore bypasses the overlap
        check. That left TWO overlapping active rows pending in the ORM cache.
        assertRaises opens a savepoint, opening a savepoint flushes, and the
        database's EXCLUDE constraint rejected the flush before the activation
        under test ever ran. The constraint was right and the fixture was
        illegal.

        So every row here is brought into force through action_activate(), and
        the windows are chosen so the fixture itself is legal at all times:

            v1       today        -> today+59   active   (bounded on purpose)
            blocker  today+60     -> open       active   (abuts v1, no overlap)
            v2       today+30     -> open       draft    (amendment of v1)

        v2 overlaps blocker, not v1, so activating it fails on a REAL conflict
        that the database would also have refused. Because _assert_no_active_
        overlap runs before _supersede_source, the failure lands before the
        first write and v1 is never partially superseded.
        """
        v1_end = self.today + timedelta(days=59)
        blocker_start = self.today + timedelta(days=60)
        v2_start = self.today + timedelta(days=30)

        v1 = self._draft(effective_to=v1_end)
        v1.with_user(self.manager).action_activate()

        # Legal: starts the day after v1 ends, so the two active windows abut
        # without overlapping. Activated through the real workflow.
        blocker = self._draft(
            agreement_number="BLOCK-%s" % uuid.uuid4().hex[:4],
            effective_from=blocker_start,
        )
        blocker.with_user(self.manager).action_activate()

        v2 = v1.with_user(self.manager)._create_amendment(effective_from=v2_start)

        # Prove the fixture is valid at the database level BEFORE the assertion
        # opens its savepoint. If the arrangement were illegal again, this line
        # would fail here rather than masquerading as the behaviour under test.
        self.env.flush_all()

        with self.assertRaises(UserError) as caught:
            v2.with_user(self.manager).action_activate()
        self.assertIn("overlap", str(caught.exception).lower())

        v1.invalidate_recordset()
        v2.invalidate_recordset()
        self.assertEqual(v1.state, "active", "v1 must survive a failed amendment activation")
        self.assertEqual(v1.effective_to, v1_end, "v1's window must not have been closed")
        self.assertFalse(v1.superseded_by_id, "v1 must not be partially superseded")
        self.assertEqual(v2.state, "draft")

    def test_30b_historical_version_keeps_its_own_terms(self):
        """The point of the whole design: v2 raising the ceiling must not reach v1."""
        v1 = self._draft(limit_scope="unlimited")
        v1.with_user(self.manager).action_activate()
        v2 = v1.with_user(self.manager)._create_amendment(
            effective_from=self.today + timedelta(days=30)
        )
        v2.with_user(self.manager).write({"authorization_required": True})
        v2.with_user(self.manager).action_activate()

        v1.invalidate_recordset()
        self.assertFalse(
            v1.authorization_required,
            "amending v2 must never rewrite the terms recorded on v1",
        )

    # ==================================================================
    # AGREEMENT: DELETION
    # ==================================================================
    def test_31_non_draft_cannot_unlink(self):
        agreement = self._draft()
        agreement.with_user(self.manager).action_activate()
        with self.assertRaises(UserError):
            agreement.with_user(self.manager).unlink()

    def test_32_draft_unlink_allowed_for_officer(self):
        agreement = self._draft(user=self.officer)
        agreement.with_user(self.officer).unlink()
        self.assertFalse(agreement.exists())

    def test_33_agreement_company_isolation(self):
        other_company = self.env["res.company"].sudo().create({"name": "Isolated Hospital"})
        other_partner = self.env["res.partner"].sudo().create(
            {"name": "Isolated Payer", "company_id": other_company.id}
        )
        other_payer = self.env["hospital.payer"].sudo().create(
            {
                "name": "Isolated Payer",
                "payer_type": "corporate",
                "partner_id": other_partner.id,
                "company_id": other_company.id,
            }
        )
        foreign = self.env["hospital.payer.agreement"].sudo().create(
            {
                "payer_id": other_payer.id,
                "agreement_number": "FOREIGN-1",
                "company_id": other_company.id,
                "effective_from": self.today,
                "limit_scope": "unlimited",
            }
        )
        mine = self._draft()
        visible = self.env["hospital.payer.agreement"].with_user(self.officer).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(foreign, visible)

    def test_33b_payer_with_agreements_cannot_unlink(self):
        self._draft()
        with self.assertRaises(UserError):
            self.payer.sudo().unlink()

    # ==================================================================
    # FEATURE FLAG + NO LIVE BEHAVIOUR CHANGE
    # ==================================================================
    def test_34_feature_flag_defaults_to_off(self):
        field = self.env["res.company"]._fields["payer_responsibility_mode"]
        self.assertEqual(field.default(self.env["res.company"]), "off")
        self.assertEqual(
            {key for key, _label in field.selection}, {"off", "shadow", "enforce"}
        )

    def test_35_phase_1_changes_no_live_behaviour(self):
        """UPDATED IN PHASE 3C/3D. The guard it replaces did its job.

        The original assertion was that allocate_payer remained a stub, so that
        a later phase could not land the responsibility engine silently. This is
        that phase, and this is the conscious update.

        What survives unchanged is the claim that actually protects a live
        hospital: the engine ships at mode 'off', where no sponsor split can
        affect anything, and allocate_payer still refuses to invent an amount.
        """
        # sudo(): the authority check is not what this test is about, and it
        # returns early under env.su, so the refusal below is the one that
        # matters -- the engine declining to invent an amount.
        engine = self.env["hospital.billing.engine"].sudo()
        # The CODE default, not the live company's current value. A UAT or
        # production database may legitimately be switched to shadow or
        # enforce by an operator; what must not drift is that the feature
        # SHIPS off, so an upgrade never turns it on for anyone.
        field = self.env["res.company"]._fields["payer_responsibility_mode"]
        self.assertEqual(field.default(self.env["res.company"]), "off")
        with self.assertRaises(UserError) as caught:
            engine.allocate_payer(self.env["hospital.billing.account"])
        self.assertIn("charge", str(caught.exception).lower())

        # Case-insensitive: the module states its scope as "PHASE 1 SCOPE" in a
        # section heading. The assertion is about the declaration still being
        # there, not about how it is capitalised, so the test normalises rather
        # than the docstring bending to the test.
        source = payer_agreement.__doc__ or ""
        self.assertIn("phase 1", source.lower())

        clearance_states = dict(
            self.env["hospital.billing.account"]._fields["financial_clearance_state"].selection
        )
        self.assertNotIn(
            "payer_authorization_pending",
            clearance_states,
            "Phase 1 must not introduce the Phase 3 clearance state",
        )

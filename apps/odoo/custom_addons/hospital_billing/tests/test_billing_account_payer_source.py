"""Phase 3A pre-flight: the encounter is the ONE payer classification.

hospital.billing.account.payer_type / payer_id used to be a copy taken once at
account creation and never refreshed. The cash gate read the encounter, the
invoice engine read the account, and nothing kept the two honest -- so a
legitimate correction on the encounter left them disagreeing about who is
paying, with no error anywhere.

Every test here defends the same claim: the account cannot hold a payer
classification that its encounter does not hold. If a future change re-adds a
writable copy, these fail.

Deliberately NOT asserted here: any change in clearance behaviour. The self-pay
and sponsored clearance assertions below exist to prove the repair changed
NOTHING about the gate.
"""

import uuid

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.hospital_billing.models.encounter_payer import (
    LEGACY_PAYER_FIELDS,
)

G_MANAGER = "hospital_management.group_hospital_manager"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"


@tagged("post_install", "-at_install", "billing_account_payer_source")
class TestBillingAccountPayerSource(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.engine = cls.env["hospital.billing.engine"]
        cls.manager = cls._make_user("acct_payer_manager", G_MANAGER)
        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Account Payer Source Patient"}
        )
        cls.sponsor_partner = cls.env["res.partner"].sudo().create(
            {"name": "Account Payer Source Sponsor"}
        )
        cls.other_partner = cls.env["res.partner"].sudo().create(
            {"name": "Account Payer Source Other Sponsor"}
        )

    @classmethod
    def _make_user(cls, label, group_xmlid):
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
                        [
                            cls.env.ref("base.group_user").id,
                            cls.env.ref(group_xmlid).id,
                        ],
                    )
                ],
            }
        )

    def _encounter(self, **overrides):
        vals = {
            "patient_id": self.patient.id,
            "encounter_type": "outpatient",
            "company_id": self.company.id,
            "opened_at": fields.Datetime.now(),
        }
        vals.update(overrides)
        return self.env["hospital.encounter"].sudo().create(vals)

    def _account_for(self, encounter):
        return self.engine.sudo().get_or_create_billing_account(encounter)

    # ------------------------------------------------------------------
    # The field is derived, not copied
    # ------------------------------------------------------------------
    def test_01_field_is_a_stored_related_on_the_encounter(self):
        """Structural: a copy cannot come back without this failing."""
        for name, expected in (
            ("payer_type", "encounter_id.payer_type"),
            ("payer_id", "encounter_id.payer_id"),
        ):
            field = self.env["hospital.billing.account"]._fields[name]
            self.assertEqual(
                str(field.related), expected,
                "%s must derive from the encounter, not hold its own copy." % name,
            )
            self.assertTrue(field.store, "%s must stay stored/searchable." % name)
            self.assertTrue(
                field.readonly,
                "%s must be readonly: readonly suppresses the related field's "
                "inverse, which is what stops the account writing back to the "
                "encounter and bypassing the PAYER_IDENTITY_AUTHORITY guard."
                % name,
            )

    def test_02_self_pay_account_matches_its_encounter(self):
        encounter = self._encounter()
        account = self._account_for(encounter)
        self.assertEqual(account.payer_type, "self_pay")
        self.assertEqual(account.payer_type, encounter.payer_type)
        self.assertFalse(account.payer_id)

    def test_03_sponsored_account_matches_its_encounter(self):
        encounter = self._encounter(
            payer_type="insurance", payer_id=self.sponsor_partner.id
        )
        account = self._account_for(encounter)
        self.assertEqual(account.payer_type, "insurance")
        self.assertEqual(account.payer_id, self.sponsor_partner)

    # ------------------------------------------------------------------
    # THE REGRESSION: post-creation change must propagate
    # ------------------------------------------------------------------
    def test_04_encounter_change_after_account_exists_propagates(self):
        """The exact drift the repair removes.

        Before the repair the account kept 'self_pay' here forever, so
        check_financial_clearance (encounter) and _resolve_invoice_partner
        (account) disagreed about who is paying, silently.
        """
        encounter = self._encounter()
        account = self._account_for(encounter)
        self.assertEqual(account.payer_type, "self_pay")

        encounter.sudo().write(
            {"payer_type": "corporate", "payer_id": self.sponsor_partner.id}
        )

        self.assertEqual(account.payer_type, "corporate")
        self.assertEqual(account.payer_id, self.sponsor_partner)

    def test_05_change_propagates_through_a_reload(self):
        """Not just a cache artefact -- the stored column really moved."""
        encounter = self._encounter(
            payer_type="ngo", payer_id=self.sponsor_partner.id
        )
        account = self._account_for(encounter)
        encounter.sudo().write({"payer_id": self.other_partner.id})

        account.flush_recordset()
        account.invalidate_recordset()
        self.env.cr.execute(
            "SELECT payer_type, payer_id FROM hospital_billing_account WHERE id = %s",
            [account.id],
        )
        stored_type, stored_partner = self.env.cr.fetchone()
        self.assertEqual(stored_type, "ngo")
        self.assertEqual(stored_partner, self.other_partner.id)

    def test_06_reverting_to_self_pay_propagates(self):
        encounter = self._encounter(
            payer_type="insurance", payer_id=self.sponsor_partner.id
        )
        account = self._account_for(encounter)
        self.assertEqual(account.payer_type, "insurance")

        # payer_id must clear in the same write: the encounter's own _check_payer
        # forbids a non-self-pay type without a payer, and self_pay with a
        # dangling partner would be exactly the half-state this test guards.
        encounter.sudo().write({"payer_type": "self_pay", "payer_id": False})

        self.assertEqual(account.payer_type, "self_pay")
        self.assertFalse(account.payer_id)

    def test_07_direct_account_write_is_refused(self):
        """A write aimed at the account is not a second source of truth.

        readonly=True alone does NOT cover this: on write() nothing the related
        field depends on has changed, so Odoo schedules no recomputation and the
        supplied value would simply persist in the column. billing_account.write()
        refuses it explicitly, superuser included.
        """
        encounter = self._encounter()
        account = self._account_for(encounter)

        for vals in (
            {"payer_type": "insurance"},
            {"payer_id": self.sponsor_partner.id},
            {"payer_type": "insurance", "payer_id": self.sponsor_partner.id},
        ):
            with self.subTest(vals=vals):
                with self.assertRaises(AccessError):
                    account.sudo().write(dict(vals))

        account.flush_recordset()
        account.invalidate_recordset()
        self.assertEqual(account.payer_type, encounter.payer_type)
        self.assertEqual(account.payer_id, encounter.payer_id)

    def test_07b_unrelated_account_writes_still_work(self):
        """The guard is scoped to the two payer fields and nothing else."""
        encounter = self._encounter()
        account = self._account_for(encounter)
        account.sudo().write({"notes": "Phase 3A pre-flight"})
        self.assertEqual(account.notes, "Phase 3A pre-flight")

    def test_08_create_vals_cannot_seed_drift(self):
        """Legacy callers still pass payer_type; it must not become authoritative."""
        encounter = self._encounter()
        account = self.env["hospital.billing.account"].sudo().create(
            {
                "encounter_id": encounter.id,
                "payer_type": "government",
                "payer_id": self.sponsor_partner.id,
            }
        )
        self.assertEqual(account.payer_type, "self_pay")
        self.assertFalse(account.payer_id)

    def test_09_engine_no_longer_copies_the_classification(self):
        """get_or_create_billing_account must not reintroduce the copy."""
        import inspect

        from odoo.addons.hospital_billing.models import billing_engine

        source = inspect.getsource(
            billing_engine.HospitalBillingEngine.get_or_create_billing_account
        )
        create_call = source.split("].create(")[-1]
        self.assertNotIn('"payer_type"', create_call)
        self.assertNotIn('"payer_id"', create_call)

    # ------------------------------------------------------------------
    # The two consumers now read the same fact
    # ------------------------------------------------------------------
    def test_10_gate_and_invoice_consumers_agree(self):
        """The clearance gate reads the encounter; invoicing reads the account."""
        encounter = self._encounter(
            payer_type="corporate", payer_id=self.sponsor_partner.id
        )
        account = self._account_for(encounter)
        encounter.sudo().write({"payer_id": self.other_partner.id})

        # What check_financial_clearance branches on:
        gate_classification = encounter.sudo().payer_type
        # What _resolve_invoice_partner branches on and bills:
        invoice_classification = account.sudo().payer_type
        invoice_partner = account.sudo().payer_id

        self.assertEqual(gate_classification, invoice_classification)
        self.assertEqual(invoice_partner, encounter.sudo().payer_id)

    def test_11_receipt_payer_follows_the_encounter(self):
        """hospital.charge.receipt.payer_id relates through the account."""
        field = self.env["hospital.charge.receipt"]._fields["payer_id"]
        self.assertEqual(str(field.related), "billing_account_id.payer_id")

        encounter = self._encounter(
            payer_type="insurance", payer_id=self.sponsor_partner.id
        )
        account = self._account_for(encounter)
        self.assertEqual(account.payer_id, self.sponsor_partner)

    # ------------------------------------------------------------------
    # Nothing about clearance or authority changed
    # ------------------------------------------------------------------
    def test_12_self_pay_clearance_is_unchanged(self):
        encounter = self._encounter()
        self._account_for(encounter)
        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertTrue(result["cleared"])
        self.assertEqual(result["state"], "cleared")
        self.assertEqual(result["amount_due"], 0.0)

    def test_13_non_self_pay_clearance_is_unchanged(self):
        """Still the whole-bill credit waiver. Phase 3 replaces it, not 3A."""
        encounter = self._encounter(
            payer_type="insurance", payer_id=self.sponsor_partner.id
        )
        self._account_for(encounter)
        result = self.engine.sudo().check_financial_clearance(encounter)
        self.assertTrue(result["cleared"])
        self.assertEqual(result["state"], "credit_authorized")
        self.assertEqual(result["amount_due"], 0.0)

    def test_14_legacy_payer_fields_remain_authority_guarded(self):
        """The repair must not have widened who may reclassify a visit."""
        self.assertEqual(LEGACY_PAYER_FIELDS, {"payer_type", "payer_id"})
        encounter = self._encounter()
        nurse = self._make_user("acct_payer_nurse", G_FRONT_DESK_NURSE)
        with self.assertRaises(AccessError):
            encounter.with_user(nurse).write({"payer_type": "insurance"})

    def test_15_manager_reclassification_reaches_the_account(self):
        """End to end, as a real authorized user rather than sudo."""
        encounter = self._encounter()
        account = self._account_for(encounter)
        encounter.with_user(self.manager).write(
            {"payer_type": "insurance", "payer_id": self.sponsor_partner.id}
        )
        self.assertEqual(account.sudo().payer_type, "insurance")
        self.assertEqual(account.sudo().payer_id, self.sponsor_partner)

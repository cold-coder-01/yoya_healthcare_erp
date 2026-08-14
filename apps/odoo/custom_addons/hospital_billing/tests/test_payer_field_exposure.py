"""Field-level exposure of payer money and commercial terms.

WHAT THIS EXISTS TO CATCH
-------------------------
The Front Desk Nurse, the Receptionist and the Cashier all hold a READ ACL on
the payer models so the entrance can select a payer identity. ir.rule filters
ROWS; only ``groups=`` filters COLUMNS. Before PAYER_COMMERCIAL_READ, every one
of those roles could read limit_amount and member_limit_amount over plain RPC,
and no view was involved.

These tests assert on the ORM, never on a serializer or a view, because that is
where the boundary now is.
"""

import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.hospital_billing.models.payer_agreement import (
    PAYER_COMMERCIAL_READ,
)

G_FRONT_DESK = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_OFFICER = "hospital_billing.group_hospital_insurance_officer"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_MANAGER = "hospital_management.group_hospital_manager"

# Every field that must be unreadable at the entrance.
PROTECTED_AGREEMENT_FIELDS = (
    "limit_amount",
    "payment_terms_days",
    "tariff_mode",
    "notes",
    "suspension_reason",
    "termination_reason",
)
PROTECTED_ELIGIBILITY_FIELDS = ("member_limit_amount", "notes")

# Identity the picker genuinely needs. Protecting these would break Phase 2B.
IDENTITY_ELIGIBILITY_FIELDS = (
    "name",
    "patient_id",
    "agreement_id",
    "payer_id",
    "member_reference",
    "membership_number",
    "policy_number",
    "employee_id_number",
    "principal_member_name",
    "relationship_to_principal",
    "state",
    "effective_from",
    "effective_to",
    "is_valid_today",
)


@tagged("post_install", "-at_install", "payer_field_exposure")
class TestPayerFieldExposure(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.context_today(cls.env["hospital.patient.payer"])
        cls.front_desk = cls._make_user("expo_front_desk", G_FRONT_DESK)
        cls.receptionist = cls._make_user("expo_receptionist", G_RECEPTIONIST)
        cls.cashier = cls._make_user("expo_cashier", G_CASHIER)
        cls.officer = cls._make_user("expo_officer", G_OFFICER)
        cls.accountant = cls._make_user("expo_accountant", G_ACCOUNTANT)
        cls.manager = cls._make_user("expo_manager", G_MANAGER)

        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Exposure Patient"}
        )
        partner = cls.env["res.partner"].sudo().create({"name": "Exposure Partner"})
        cls.payer = cls.env["hospital.payer"].sudo().create(
            {
                "name": "Exposure Payer",
                "payer_type": "insurance",
                "partner_id": partner.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.agreement = cls.env["hospital.payer.agreement"].sudo().create(
            {
                "payer_id": cls.payer.id,
                "agreement_number": "EXPO-%s" % uuid.uuid4().hex[:8].upper(),
                "company_id": cls.env.company.id,
                "effective_from": cls.today - timedelta(days=10),
                "limit_scope": "unlimited",
                "payment_terms_days": 45,
                "notes": "Confidential commercial arrangement.",
            }
        )
        cls.agreement.sudo().action_activate()
        cls.eligibility = cls.env["hospital.patient.payer"].sudo().create(
            {
                "patient_id": cls.patient.id,
                "agreement_id": cls.agreement.id,
                "effective_from": cls.today - timedelta(days=5),
                "notes": "Internal eligibility note.",
            }
        )
        cls.eligibility.sudo().action_activate()

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

    def _assert_unreadable(self, user, model, record, field_names):
        for name in field_names:
            with self.subTest(model=model, field=name, user=user.name):
                with self.assertRaises(AccessError):
                    record.with_user(user).read([name])

    # ==================================================================
    # The entrance cannot read money or commercial terms
    # ==================================================================
    def test_front_desk_cannot_read_protected_agreement_fields(self):
        self._assert_unreadable(
            self.front_desk,
            "hospital.payer.agreement",
            self.agreement,
            PROTECTED_AGREEMENT_FIELDS,
        )

    def test_front_desk_cannot_read_member_limit_amount(self):
        self._assert_unreadable(
            self.front_desk,
            "hospital.patient.payer",
            self.eligibility,
            PROTECTED_ELIGIBILITY_FIELDS,
        )

    def test_front_desk_cannot_read_payer_notes(self):
        self._assert_unreadable(
            self.front_desk, "hospital.payer", self.payer, ("notes",)
        )

    def test_receptionist_and_cashier_are_equally_blocked(self):
        """They hold the same read ACL and had the same exposure."""
        for user in (self.receptionist, self.cashier):
            self._assert_unreadable(
                user,
                "hospital.payer.agreement",
                self.agreement,
                ("limit_amount", "payment_terms_days"),
            )
            self._assert_unreadable(
                user,
                "hospital.patient.payer",
                self.eligibility,
                ("member_limit_amount",),
            )

    def test_attribute_access_is_blocked_too_not_only_read(self):
        """A picker would reach the field by attribute, not by read()."""
        with self.assertRaises(AccessError):
            self.agreement.with_user(self.front_desk).limit_amount  # noqa: B018
        with self.assertRaises(AccessError):
            self.eligibility.with_user(self.front_desk).member_limit_amount  # noqa: B018

    def test_protected_fields_are_absent_from_an_unqualified_read(self):
        """read() with no field list must not quietly include them either."""
        values = self.agreement.with_user(self.front_desk).read()[0]
        for name in PROTECTED_AGREEMENT_FIELDS:
            self.assertNotIn(name, values)

    # ==================================================================
    # The roles that need the terms still have them
    # ==================================================================
    def test_officer_accountant_and_manager_keep_full_access(self):
        for user in (self.officer, self.accountant, self.manager):
            with self.subTest(user=user.name):
                self.assertEqual(
                    self.agreement.with_user(user).payment_terms_days, 45
                )
                self.assertEqual(self.agreement.with_user(user).limit_scope, "unlimited")
                self.eligibility.with_user(user).read(["member_limit_amount"])

    def test_identity_fields_stay_readable_at_the_entrance(self):
        """Over-protecting would break the picker; assert it did not happen."""
        values = self.eligibility.with_user(self.front_desk).read(
            list(IDENTITY_ELIGIBILITY_FIELDS)
        )[0]
        for name in IDENTITY_ELIGIBILITY_FIELDS:
            self.assertIn(name, values)
        self.assertEqual(
            self.agreement.with_user(self.front_desk).agreement_number,
            self.agreement.agreement_number,
        )

    # ==================================================================
    # Chatter / tracking recovery path
    # ==================================================================
    def _tracking_row(self, fname, old, new):
        """A mail.tracking.value exactly as a real chatter would hold one.

        Built explicitly rather than by writing to the agreement, because a
        TransactionCase cannot produce one by writing: mail.thread discards
        tracking for a record CREATED in the same transaction (_track_discard
        sets its initial values to None, and _track_finalize then skips it), and
        every record here is created in setUpClass on the same cursor. Verified
        against Odoo 18 -- a create-then-write in one transaction yields zero
        tracking rows even as superuser.

        Using the model's own _create_tracking_values keeps the row honest: it
        is stored in the same column, with the same field_id, as production.
        """
        model = self.env["hospital.payer.agreement"].sudo()
        col_info = model.fields_get(
            [fname], attributes=("string", "type", "selection", "currency_field")
        )[fname]
        message = self.agreement.sudo().message_post(body="exposure probe")
        values = self.env["mail.tracking.value"].sudo()._create_tracking_values(
            old, new, fname, col_info, self.agreement.sudo()
        )
        values["mail_message_id"] = message.id
        return self.env["mail.tracking.value"].sudo().create(values)

    def test_protected_tracked_values_are_not_recoverable_through_chatter(self):
        """tracking=True on a protected field must not reopen the leak.

        Three independent controls, because any one alone could be routed
        around:

          0. Field.is_accessible is False for the protected fields -- this is
             the predicate everything below consults;
          1. mail.tracking.value is readable only by base.group_system, so the
             rows cannot be queried directly;
          2. mail.message._message_format filters trackings through
             _filter_has_field_access (mail_message.py:1096), so a protected
             field's history is dropped from the chatter payload.

        A tracked but UNPROTECTED field is included as a control: without it,
        an empty result would pass this test even if the filter were simply
        broken and dropping everything.
        """
        # 0. The predicate itself.
        front_desk_env = self.env(user=self.front_desk)
        agreement_fields = self.env["hospital.payer.agreement"]._fields
        for name in PROTECTED_AGREEMENT_FIELDS:
            self.assertFalse(
                agreement_fields[name].is_accessible(front_desk_env),
                "%s is still accessible to the front desk" % name,
            )

        # 1. No direct ORM read of the tracking table.
        with self.assertRaises(AccessError):
            self.env["mail.tracking.value"].with_user(self.front_desk).search([])

        # 2. No chatter read either -- and the filter still passes the control.
        protected = self._tracking_row("payment_terms_days", 45, 60)
        control = self._tracking_row("state", "draft", "active")
        visible = (protected | control)._filter_has_field_access(front_desk_env)

        self.assertNotIn(
            protected,
            visible,
            "payment_terms_days history leaked through mail.tracking.value",
        )
        self.assertIn(
            control,
            visible,
            "the tracking filter dropped an unprotected field too, so the "
            "assertion above proves nothing",
        )

    def test_commercial_read_constant_names_only_existing_groups(self):
        """No parallel security model: every entry must resolve."""
        for xmlid in PAYER_COMMERCIAL_READ.split(","):
            self.assertTrue(self.env.ref(xmlid), xmlid)
        self.assertNotIn(G_FRONT_DESK, PAYER_COMMERCIAL_READ)
        self.assertNotIn(G_RECEPTIONIST, PAYER_COMMERCIAL_READ)
        self.assertNotIn(G_CASHIER, PAYER_COMMERCIAL_READ)

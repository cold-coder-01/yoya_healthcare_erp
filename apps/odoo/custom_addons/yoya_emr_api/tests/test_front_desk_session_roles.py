"""The Front Desk routing capability carried on the reception session payload.

The Next.js shell decides which workspace a user lands in after login from
role_flags()["front_desk_nurse"] (see apps/web/src/lib/reception-roles.ts).
These tests pin the one property that makes that routing correct: the flag
tracks membership of the Front Desk Nurse group and NOTHING else -- not the
Hospital Nurse group it implies, not Receptionist, not Cashier.

Routing itself is convenience only. Authorization for every front-desk endpoint
is enforced separately in controllers/front_desk.py and is not under test here.
"""
import uuid

from odoo.tests import TransactionCase, tagged

from odoo.addons.yoya_emr_api.services.reception_scope import (
    may_front_desk,
    role_flags,
)

G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_NURSE = "hospital_management.group_hospital_nurse"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_CASHIER = "hospital_billing.group_hospital_cashier"

# Mirrors the ReceptionRoles type in apps/web/src/lib/reception-roles.ts. If a
# flag is added or renamed here the TypeScript contract has to move with it.
EXPECTED_ROLE_KEYS = {
    "receptionist",
    "cashier",
    "accountant",
    "manager",
    "system_administrator",
    "emergency_authorizer",
    "front_desk_nurse",
}


@tagged("post_install", "-at_install", "front_desk_session_roles")
class TestFrontDeskSessionRoles(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.front_desk = cls._make_user("fdsr_fd", [G_FRONT_DESK_NURSE])
        cls.nurse = cls._make_user("fdsr_nurse", [G_NURSE])
        cls.receptionist = cls._make_user("fdsr_recep", [G_RECEPTIONIST])
        cls.cashier = cls._make_user("fdsr_cashier", [G_CASHIER])

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
                        (4, cls.env.ref(xmlid).id) for xmlid in group_xmlids
                    ],
                }
            )
        )

    def _flags(self, user):
        return role_flags(self.env(user=user))

    # ------------------------------------------------------------------
    # The flag itself
    # ------------------------------------------------------------------
    def test_front_desk_nurse_reads_true(self):
        self.assertTrue(self._flags(self.front_desk)["front_desk_nurse"])

    def test_plain_nurse_is_not_a_front_desk_nurse(self):
        """The implication runs one way only.

        group_hospital_front_desk_nurse IMPLIES group_hospital_nurse, so a front
        desk nurse is a nurse. Nothing implies the front desk group, so a plain
        nurse must not read as one -- this is what keeps a plain nurse on the
        old Evaluation Queue.
        """
        flags = self._flags(self.nurse)
        self.assertFalse(flags["front_desk_nurse"])
        self.assertTrue(self.nurse.has_group(G_NURSE))
        # ...and the front desk nurse really does inherit Nurse.
        self.assertTrue(self.front_desk.has_group(G_NURSE))

    def test_plain_receptionist_is_not_a_front_desk_nurse(self):
        flags = self._flags(self.receptionist)
        self.assertFalse(flags["front_desk_nurse"])
        self.assertTrue(flags["receptionist"])

    def test_cashier_is_not_a_front_desk_nurse(self):
        flags = self._flags(self.cashier)
        self.assertFalse(flags["front_desk_nurse"])
        self.assertTrue(flags["cashier"])

    # ------------------------------------------------------------------
    # Why the routing flag is not may_front_desk()
    # ------------------------------------------------------------------
    def test_flag_is_narrower_than_may_front_desk(self):
        """Reusing may_front_desk() for routing would have been the bug.

        may_front_desk() answers "may this user READ the worklist", and is
        deliberately broad. Routing on it would drag every receptionist and
        every plain nurse out of their own workspace and into the front desk.
        """
        for user in (self.nurse, self.receptionist):
            self.assertTrue(may_front_desk(self.env(user=user)))
            self.assertFalse(self._flags(user)["front_desk_nurse"])

        self.assertTrue(may_front_desk(self.env(user=self.front_desk)))
        self.assertTrue(self._flags(self.front_desk)["front_desk_nurse"])

    # ------------------------------------------------------------------
    # Payload contract
    # ------------------------------------------------------------------
    def test_role_flags_keys_match_frontend_contract(self):
        self.assertEqual(set(self._flags(self.front_desk)), EXPECTED_ROLE_KEYS)

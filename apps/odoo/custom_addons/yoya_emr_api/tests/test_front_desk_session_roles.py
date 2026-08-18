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
    may_doctor_desk,
    may_front_desk,
    role_flags,
)

G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"
G_NURSE = "hospital_management.group_hospital_nurse"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_CASHIER = "hospital_billing.group_hospital_cashier"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_MANAGER = "hospital_management.group_hospital_manager"
G_SYSADMIN = "hospital_management.group_hospital_system_administrator"

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
    # Added with the Insurance/Credit Desk. Like front_desk_nurse it is DIRECT
    # membership rather than "may open the desk", because the front end uses it
    # to pick a landing workspace and a manager must not be mistaken for an
    # officer. ReceptionRoles in apps/web/src/lib/reception-roles.ts carries the
    # matching field; this test is what stops the two drifting apart.
    "insurance_officer",
    # Added with the Doctor Desk. NARROW, like the two above: direct membership
    # of group_hospital_doctor, not may_doctor_desk() (which also admits
    # manager and admin). The front end routes a PURE doctor to /doctor with
    # it, and a manager -- who is routed to /reception well before the doctor
    # branch is reached -- is untouched.
    "doctor",
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
        cls.doctor = cls._make_user("fdsr_doctor", [G_DOCTOR])

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
    # The doctor flag
    # ------------------------------------------------------------------
    def test_doctor_reads_true(self):
        self.assertTrue(self._flags(self.doctor)["doctor"])

    def test_doctor_flag_is_not_granted_by_elimination(self):
        """A user with no reception-side role is NOT thereby a doctor.

        This is the property that keeps a plain nurse on /triage. The flag is
        authoritative group membership and nothing else -- there is no title
        heuristic and no "has no other role, must be clinical" inference.
        """
        self.assertFalse(self._flags(self.nurse)["doctor"])
        self.assertFalse(self._flags(self.receptionist)["doctor"])
        self.assertFalse(self._flags(self.cashier)["doctor"])
        self.assertFalse(self._flags(self.front_desk)["doctor"])

    def test_manager_reads_true_because_the_group_is_implied(self):
        """PINS THE PROPERTY THAT MAKES THE ROUTING SUBTLE.

        Unlike front_desk_nurse and insurance_officer -- which nothing implies,
        so they amount to direct membership -- group_hospital_doctor IS
        implied: manager carries implied_ids = receptionist + doctor + nurse,
        and system administrator implies manager. has_group() honours that, so
        both read True here.

        This is not a bug and must not be "fixed" by testing for direct
        membership: a manager genuinely holds the Doctor group and
        _assert_may_start_consultation admits them on exactly that basis. What
        keeps a manager on /reception is the ORDER of the branches in
        landingRouteForRoles, not the width of this flag. If that ordering is
        ever changed, this test is the note explaining why it mattered.
        """
        manager = self._make_user("fdsr_mgr", [G_MANAGER])
        admin = self._make_user("fdsr_admin", [G_SYSADMIN])

        self.assertTrue(self._flags(manager)["doctor"])
        self.assertTrue(self._flags(admin)["doctor"])
        # ...and the reception flag that outranks it is set too, which is what
        # the front end's precedence relies on.
        self.assertTrue(self._flags(manager)["manager"])
        self.assertTrue(self._flags(admin)["system_administrator"])

    def test_may_doctor_desk_admits_doctor_manager_admin_and_nobody_else(self):
        manager = self._make_user("fdsr_mgr2", [G_MANAGER])
        admin = self._make_user("fdsr_admin2", [G_SYSADMIN])

        for user in (self.doctor, manager, admin):
            self.assertTrue(may_doctor_desk(self.env(user=user)))

        # The desk is not a second door into the nursing or cash surfaces.
        for user in (self.nurse, self.front_desk, self.receptionist, self.cashier):
            self.assertFalse(may_doctor_desk(self.env(user=user)))

    # ------------------------------------------------------------------
    # Payload contract
    # ------------------------------------------------------------------
    def test_role_flags_keys_match_frontend_contract(self):
        self.assertEqual(set(self._flags(self.front_desk)), EXPECTED_ROLE_KEYS)

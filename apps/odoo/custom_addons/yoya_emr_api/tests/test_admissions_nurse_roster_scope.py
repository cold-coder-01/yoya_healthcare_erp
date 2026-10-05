"""Admissions Desk: the ward nurse's census follows their CURRENT roster.

THE UAT DEFECT. ward@nurse, rostered to General Medicine and Surgery, saw both
wards, their occupancy and the bed board, but "No open admissions on your
wards" and zero in every lane -- while a fresh Odoo shell, as the same user,
read every admission. Reception saw them all.

ROOT CAUSE. The nurse rule's domain reads `user.yoya_permitted_department_ids`,
and ir.rule._compute_domain is ormcached per user with that id list EVALUATED
into it. res.users.write clears the registry cache only for the fields in
_get_invalidation_fields(); the roster was not one of them. A nurse who touched
hospital.admission before their departments were saved kept the cached
`('ward_id.department_id', 'in', [])` for the life of the server process.
Wards and beds carry no roster rule, which is why they alone kept working.

THE SAME BUG, THE OTHER WAY. A nurse whose department was REMOVED kept seeing
that department's patients until a restart. That direction is the security
one, and it is tested here too.

These tests are HttpCase: the served request runs in this process, so the
ormcache is shared with the test exactly as it is in the live server -- first
visit, then roster change, then second visit.
"""
from odoo.tests import tagged

from .test_admissions_desk_api import (
    DETAIL,
    G_NURSE,
    AdmissionsDeskCase,
)


@tagged("post_install", "-at_install", "admissions_desk")
class TestAdmissionsNurseRosterScope(AdmissionsDeskCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.nurse_both = cls._make_user("nrab", [G_NURSE], [cls.dept_a, cls.dept_b])
        # Beds for the transfer journey, one per ward.
        cls.bed_a_move = cls._bed(cls.room_a, "A1-MV")
        cls.bed_b_move = cls._bed(cls.room_b, "B1-MV")

    # ------------------------------------------------------------------
    def _census(self, user):
        return self._ids(self._worklist(user))

    def _roster(self, user, departments):
        """As an administrator would: a plain write on the user form."""
        user.sudo().write({"yoya_permitted_department_ids": [(6, 0, [d.id for d in departments])]})

    def _active_in(self, ward):
        return {
            a.id for a in (self.adm_mine, self.adm_other_ward, self.adm_draft_bed,
                           self.adm_draft_nobed, self.adm_transferred)
            if a.ward_id == ward
        }

    # ==================================================================
    # 1-5. The roster decides, per department
    # ==================================================================
    def test_1_a_nurse_with_no_departments_sees_no_admissions(self):
        nobody = self._make_user("nrx", [G_NURSE], [])
        data = self._worklist(nobody, lane=None)  # the server default: open work
        self.assertEqual(data["rows"], [])
        self.assertEqual(data["summary"]["active"], 0)
        self.assertEqual(
            self.env["hospital.admission"].with_user(nobody).search_count([("name", "!=", False)]), 0
        )

    def test_2_and_3_department_a_sees_ward_a_and_not_ward_b(self):
        census = self._census(self.nurse_a)
        self.assertIn(self.adm_mine.id, census)
        self.assertTrue(self._active_in(self.ward_a) <= census)
        self.assertNotIn(self.adm_other_ward.id, census)
        self.assertFalse(self._active_in(self.ward_b) & census)

    def test_4_department_b_sees_ward_b_only(self):
        census = self._census(self.nurse_b)
        self.assertEqual(census, {self.adm_other_ward.id})

    def test_5_both_departments_see_both_wards(self):
        census = self._census(self.nurse_both)
        self.assertTrue(self._active_in(self.ward_a) | self._active_in(self.ward_b) <= census)

    def test_active_admissions_are_counted_in_their_lanes(self):
        """The UAT symptom was 'every lane is 0': assert the counts, not just rows."""
        data = self._worklist(self.nurse_a, lane=None)  # the server default: open work
        self.assertGreaterEqual(data["summary"]["admitted"], 1)
        self.assertGreaterEqual(data["summary"]["active"], len(self._active_in(self.ward_a)))
        self.assertIn(self.adm_mine.id, self._ids(data))

    # ==================================================================
    # THE REGRESSION: a roster change reaches a nurse who already visited
    # ==================================================================
    def test_departments_granted_after_a_first_visit_take_effect_without_a_restart(self):
        """Exactly the UAT order: open the desk, THEN get rostered."""
        late = self._make_user("nrlate", [G_NURSE], [])
        self.assertEqual(self._census(late), set())          # primes the rule cache
        self._roster(late, [self.dept_a])
        census = self._census(late)
        self.assertIn(self.adm_mine.id, census)
        self.assertNotIn(self.adm_other_ward.id, census)
        self.assertEqual(self._ok(DETAIL % self.adm_mine.id, late)["admission"]["id"], self.adm_mine.id)

    def test_departments_removed_after_a_visit_are_revoked_immediately(self):
        """The security direction: removal must not wait for a restart."""
        leaving = self._make_user("nrleave", [G_NURSE], [self.dept_a, self.dept_b])
        self.assertIn(self.adm_other_ward.id, self._census(leaving))   # primes the cache
        self._roster(leaving, [self.dept_a])
        self.assertNotIn(self.adm_other_ward.id, self._census(leaving))
        response, _payload = self._get(DETAIL % self.adm_other_ward.id, leaving)
        self.assertEqual(response.status_code, 404)
        self._roster(leaving, [])
        self.assertEqual(self._census(leaving), set())

    # ==================================================================
    # 6. Transfer moves visibility with the patient
    # ==================================================================
    def test_6_transfer_moves_the_admission_between_rosters(self):
        admission = self._admit(self.bed_a_move, self.doctor)
        reference = admission.name
        self.assertIn(admission.id, self._census(self.nurse_a))
        self.assertNotIn(admission.id, self._census(self.nurse_b))
        self.assertIn(admission.id, self._census(self.nurse_both))

        admission.sudo().action_transfer(self.ward_b, self.room_b, self.bed_b_move, reason="surgery")

        self.assertNotIn(admission.id, self._census(self.nurse_a))
        self.assertIn(admission.id, self._census(self.nurse_b))
        self.assertIn(admission.id, self._census(self.nurse_both))
        response, _payload = self._get(DETAIL % admission.id, self.nurse_a)
        self.assertEqual(response.status_code, 404)
        detail = self._ok(DETAIL % admission.id, self.nurse_b)["admission"]
        self.assertEqual(detail["reference"], reference)
        both = self._ok(DETAIL % admission.id, self.nurse_both)["admission"]
        self.assertEqual(both["reference"], reference)

    # ==================================================================
    # 7-8. Everyone else is untouched
    # ==================================================================
    def test_7_and_8_reception_manager_and_admin_keep_the_whole_census(self):
        everything = {a.id for a in (
            self.adm_mine, self.adm_other_ward, self.adm_draft_bed, self.adm_draft_nobed,
            self.adm_transferred, self.adm_discharged, self.adm_cancelled,
        )}
        for user in (self.receptionist, self.manager, self.sysadmin):
            with self.subTest(user=user.login):
                self.assertEqual(self._census(user), everything)

    # ==================================================================
    # 9. The bed board and the census agree
    # ==================================================================
    def test_9_the_bed_board_names_exactly_the_admissions_the_census_shows(self):
        for nurse in (self.nurse_a, self.nurse_b, self.nurse_both):
            with self.subTest(nurse=nurse.login):
                census = self._census(nurse)
                beds = [
                    row for row in self._beds(nurse)
                    if row["occupied"] and row["id"] in (self.bed_a1 | self.bed_b1).ids
                ]
                self.assertEqual(len(beds), 2)
                for row in beds:
                    if row["can_view_admission"]:
                        self.assertIn(row["admission"]["id"], census)
                    else:
                        self.assertIsNone(row["admission"])
                named = {row["admission"]["id"] for row in beds if row["can_view_admission"]}
                self.assertEqual(named, census & {self.adm_mine.id, self.adm_other_ward.id})

    # ==================================================================
    # 10. No way around the scope by id
    # ==================================================================
    def test_10_an_out_of_scope_admission_by_id_is_the_same_404_as_a_missing_one(self):
        for nurse, hidden in ((self.nurse_a, self.adm_other_ward), (self.nurse_b, self.adm_mine)):
            with self.subTest(nurse=nurse.login):
                r1, p1 = self._get(DETAIL % hidden.id, nurse)
                r2, p2 = self._get(DETAIL % 999999999, nurse)
                self.assertEqual((r1.status_code, r2.status_code), (404, 404))
                self.assertEqual(p1["error"], p2["error"])
                self.assertNotIn(hidden.patient_id.name, r1.text)

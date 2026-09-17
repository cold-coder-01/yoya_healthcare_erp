"""Radiology Slice 0A: the two Radiology roles, and Lab Technician's exit.

WHAT THIS PINS
--------------
Before this slice group_hospital_lab_technician was the operational imaging
role: it held every Radiology ACL, the hospital-wide Radiology record rules and
the Radiology menu, so every laboratory technician was also a radiographer and a
radiologist. This slice gives Radiology its own two roles and takes Radiology
away from Lab Technician. The tests below assert:

  1. both groups exist, in the hospital category, implying nothing and implied
     by nothing -- in particular Radiologist does NOT imply Radiology Technician;
  2. the EFFECTIVE model access of each role, as ir.model.access resolves it for
     a real user, matches the approved matrix exactly;
  3. a user holding ONLY Lab Technician has no Radiology model access and no
     Radiology menu;
  4. Doctor, Manager, System Administrator, Receptionist, Nurse and DPO keep
     exactly the effective access they had before the slice;
  5. the Radiology roles are granted nothing outside Radiology.

Effective access is asked of ir.model.access.check() AS THE USER rather than
read off the CSV, because implied groups are part of the answer: Manager
implies Doctor, Receptionist and Nurse, and System Administrator implies
Manager. A matrix that only read rows could pass while a real user saw
something else.
"""
import uuid

from odoo.tests import TransactionCase, tagged

TECHNICIAN = "hospital_radiology.group_hospital_radiology_technician"
RADIOLOGIST = "hospital_radiology.group_hospital_radiologist"
LAB_TECHNICIAN = "hospital_management.group_hospital_lab_technician"
DOCTOR = "hospital_management.group_hospital_doctor"
MANAGER = "hospital_management.group_hospital_manager"
SYSADMIN = "hospital_management.group_hospital_system_administrator"
RECEPTIONIST = "hospital_management.group_hospital_receptionist"
NURSE = "hospital_management.group_hospital_nurse"
DPO = "hospital_management.group_hospital_data_protection_officer"
PHARMACIST = "hospital_management.group_hospital_pharmacist"
ACCOUNTANT = "hospital_management.group_hospital_accountant"

EXAM = "hospital.radiology.exam"
REQUEST = "hospital.radiology.request"
REQUEST_LINE = "hospital.radiology.request.line"
RESULT = "hospital.radiology.result"
RESULT_LINE = "hospital.radiology.result.line"
IMAGE = "hospital.radiology.image"
RADIOLOGY_MODELS = (EXAM, REQUEST, REQUEST_LINE, RESULT, RESULT_LINE, IMAGE)

MODES = ("read", "write", "create", "unlink")

# (read, write, create, unlink). The approved Slice 0A matrix, with the
# deviations from the proposal stated where they are made:
#
#   Technician RESULT write/create: images belong to a result and the existing
#     Odoo result form edits them inline, so a technician cannot attach imaging
#     without creating and saving the result that carries it.
#   Technician RESULT LINE create (no write): creating a result seeds its exam
#     lines from the request; editing the per-exam findings is reporting.
#   Technician IMAGE unlink: removing a mistaken upload from the inline image
#     list is an unlink, and there is no archive control in that list. The
#     image model's own freeze still refuses it from `validated` onward.
#   Radiologist REQUEST write: hospital_billing's action_release() completes
#     the request through _sync_completion_from_results(), which writes the
#     request AS THE RELEASING USER. Without write, a radiologist could not
#     release a report.
TECHNICIAN_MATRIX = {
    EXAM: (1, 0, 0, 0),
    REQUEST: (1, 1, 1, 0),
    REQUEST_LINE: (1, 1, 1, 0),
    RESULT: (1, 1, 1, 0),
    RESULT_LINE: (1, 0, 1, 0),
    IMAGE: (1, 1, 1, 1),
}
RADIOLOGIST_MATRIX = {
    EXAM: (1, 0, 0, 0),
    REQUEST: (1, 1, 0, 0),
    REQUEST_LINE: (1, 0, 0, 0),
    RESULT: (1, 1, 1, 0),
    RESULT_LINE: (1, 1, 1, 0),
    IMAGE: (1, 0, 0, 0),
}
NO_ACCESS = {model: (0, 0, 0, 0) for model in RADIOLOGY_MODELS}

# The effective access every pre-existing role had at a285571, before this
# slice. Unchanged by it: none of these roles implies Lab Technician, so taking
# Radiology away from Lab Technician cannot move them.
DOCTOR_MATRIX = {
    EXAM: (1, 0, 0, 0),
    REQUEST: (1, 1, 1, 0),
    REQUEST_LINE: (1, 1, 1, 0),
    RESULT: (1, 0, 0, 0),
    RESULT_LINE: (1, 0, 0, 0),
    IMAGE: (1, 0, 0, 0),
}
MANAGER_MATRIX = {
    EXAM: (1, 1, 1, 0),
    REQUEST: (1, 1, 1, 0),
    REQUEST_LINE: (1, 1, 1, 0),
    RESULT: (1, 1, 1, 0),
    RESULT_LINE: (1, 1, 1, 0),
    IMAGE: (1, 1, 1, 1),
}
SYSADMIN_MATRIX = {model: (1, 1, 1, 1) for model in RADIOLOGY_MODELS}
READ_ONLY_MATRIX = {model: (1, 0, 0, 0) for model in RADIOLOGY_MODELS}


@tagged("post_install", "-at_install", "radiology_security_roles")
class TestRadiologySecurityRoles(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.technician = cls._make_user("rad0a_tech", TECHNICIAN)
        cls.radiologist = cls._make_user("rad0a_radiologist", RADIOLOGIST)
        cls.both = cls._make_user("rad0a_both", TECHNICIAN, RADIOLOGIST)
        cls.lab_technician = cls._make_user("rad0a_lab", LAB_TECHNICIAN)
        cls.doctor = cls._make_user("rad0a_doctor", DOCTOR)
        cls.manager = cls._make_user("rad0a_manager", MANAGER)
        cls.sysadmin = cls._make_user("rad0a_sysadmin", SYSADMIN)
        cls.receptionist = cls._make_user("rad0a_reception", RECEPTIONIST)
        cls.nurse = cls._make_user("rad0a_nurse", NURSE)
        cls.dpo = cls._make_user("rad0a_dpo", DPO)

    @classmethod
    def _make_user(cls, login, *group_xmlids):
        groups = [cls.env.ref("base.group_user").id] + [
            cls.env.ref(xmlid).id for xmlid in group_xmlids
        ]
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s_%s@example.test" % (login, uuid.uuid4().hex[:6]),
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "groups_id": [(6, 0, groups)],
        })

    # ------------------------------------------------------------------
    def _effective(self, user, model):
        access = self.env["ir.model.access"].with_user(user)
        return tuple(int(access.check(model, mode, False)) for mode in MODES)

    def _assert_matrix(self, user, expected):
        actual = {model: self._effective(user, model) for model in RADIOLOGY_MODELS}
        self.assertEqual(actual, expected)

    # ------------------------------------------------------------------
    # 1. The groups
    # ------------------------------------------------------------------
    def test_radiology_technician_group_exists(self):
        group = self.env.ref(TECHNICIAN)
        self.assertEqual(group._name, "res.groups")
        self.assertEqual(group.name, "Hospital Radiology Technician")
        self.assertEqual(
            group.category_id,
            self.env.ref("hospital_management.module_category_hospital_management"),
        )

    def test_radiologist_group_exists(self):
        group = self.env.ref(RADIOLOGIST)
        self.assertEqual(group._name, "res.groups")
        self.assertEqual(group.name, "Hospital Radiologist")
        self.assertEqual(
            group.category_id,
            self.env.ref("hospital_management.module_category_hospital_management"),
        )

    def test_neither_radiology_role_implies_anything(self):
        """Least privilege: in particular Radiologist does not imply Technician."""
        for xmlid in (TECHNICIAN, RADIOLOGIST):
            self.assertFalse(self.env.ref(xmlid).implied_ids, xmlid)
        self.assertFalse(self.radiologist.has_group(TECHNICIAN))
        self.assertFalse(self.technician.has_group(RADIOLOGIST))

    def test_no_existing_role_implies_a_radiology_role(self):
        """Nothing gains Radiology by inheritance -- least of all Lab Technician."""
        radiology = self.env.ref(TECHNICIAN) | self.env.ref(RADIOLOGIST)
        for xmlid in (
            LAB_TECHNICIAN, DOCTOR, MANAGER, SYSADMIN, RECEPTIONIST, NURSE, DPO,
            PHARMACIST, ACCOUNTANT,
        ):
            group = self.env.ref(xmlid)
            self.assertFalse(group.trans_implied_ids & radiology, xmlid)
        for user in (self.lab_technician, self.manager, self.sysadmin, self.doctor):
            self.assertFalse(user.has_group(TECHNICIAN), user.login)
            self.assertFalse(user.has_group(RADIOLOGIST), user.login)

    def test_a_user_may_hold_both_roles(self):
        both = {
            model: tuple(
                max(t, r) for t, r in zip(TECHNICIAN_MATRIX[model], RADIOLOGIST_MATRIX[model])
            )
            for model in RADIOLOGY_MODELS
        }
        self._assert_matrix(self.both, both)

    # ------------------------------------------------------------------
    # 2. The Radiology roles' access
    # ------------------------------------------------------------------
    def test_radiology_technician_effective_access(self):
        self._assert_matrix(self.technician, TECHNICIAN_MATRIX)

    def test_radiologist_effective_access(self):
        self._assert_matrix(self.radiologist, RADIOLOGIST_MATRIX)

    def test_radiology_roles_are_granted_nothing_outside_radiology(self):
        """Every ACL row naming a Radiology role is on a Radiology model, except
        READ on the patient and the doctor -- and nothing more on either.

        WHY THOSE TWO READS ARE REQUIRED, NOT CONVENIENT. hospital.radiology.
        result.create() and hospital.radiology.request.create() write an audit
        summary that renders patient_id and physician_id display names AS THE
        CALLING USER, and hospital_billing's clearance refusal names the patient
        the same way. Lab Technician held read on both models, which is why the
        imaging bench never met this; without it a Radiology-only user cannot
        create a result at all. Neither model carries a record rule, and Lab
        Technician's read was likewise unscoped, so this mirrors what the bench
        already had rather than opening anything new.
        """
        groups = self.env.ref(TECHNICIAN) | self.env.ref(RADIOLOGIST)
        rows = self.env["ir.model.access"].sudo().search([("group_id", "in", groups.ids)])
        self.assertTrue(rows)
        outside = rows.filtered(lambda row: row.model_id.model not in RADIOLOGY_MODELS)
        self.assertEqual(
            set(outside.mapped("model_id.model")), {"hospital.patient", "hospital.doctor"}
        )
        for row in outside:
            self.assertEqual(
                (row.perm_read, row.perm_write, row.perm_create, row.perm_unlink),
                (True, False, False, False),
                row.name,
            )

    def test_radiology_roles_read_but_never_change_patients_or_doctors(self):
        for user in (self.technician, self.radiologist):
            for model in ("hospital.patient", "hospital.doctor"):
                self.assertEqual(self._effective(user, model), (1, 0, 0, 0), (user.login, model))

    # ------------------------------------------------------------------
    # 3. Lab Technician no longer holds Radiology
    # ------------------------------------------------------------------
    def test_lab_technician_alone_has_no_radiology_model_access(self):
        self._assert_matrix(self.lab_technician, NO_ACCESS)

    def test_no_radiology_acl_row_names_lab_technician(self):
        rows = self.env["ir.model.access"].sudo().search([
            ("model_id.model", "in", list(RADIOLOGY_MODELS)),
            ("group_id", "=", self.env.ref(LAB_TECHNICIAN).id),
        ])
        self.assertFalse(rows)

    # ------------------------------------------------------------------
    # 4. Everyone else is unchanged
    # ------------------------------------------------------------------
    def test_doctor_access_is_unchanged(self):
        self._assert_matrix(self.doctor, DOCTOR_MATRIX)

    def test_manager_access_is_unchanged(self):
        self._assert_matrix(self.manager, MANAGER_MATRIX)

    def test_system_administrator_access_is_unchanged(self):
        self._assert_matrix(self.sysadmin, SYSADMIN_MATRIX)

    def test_front_office_and_dpo_read_access_is_not_widened(self):
        """Their broad read is known debt; this slice must not make it worse."""
        for user in (self.receptionist, self.nurse, self.dpo):
            self._assert_matrix(user, READ_ONLY_MATRIX)

    # ------------------------------------------------------------------
    # 5. The menu follows the roles
    # ------------------------------------------------------------------
    def _visible(self, user, xmlid):
        menu = self.env.ref(xmlid)
        return menu.id in self.env["ir.ui.menu"].with_user(user)._visible_menu_ids()

    def test_radiology_app_is_visible_to_the_radiology_roles(self):
        for user in (self.technician, self.radiologist):
            self.assertTrue(
                self._visible(user, "hospital_radiology.menu_hospital_radiology_root"),
                user.login,
            )
            self.assertTrue(
                self._visible(user, "hospital_radiology.menu_hospital_radiology_requests"),
                user.login,
            )
            self.assertTrue(
                self._visible(user, "hospital_radiology.menu_hospital_radiology_results"),
                user.login,
            )

    def test_radiology_app_is_hidden_from_lab_technician(self):
        for xmlid in (
            "hospital_radiology.menu_hospital_radiology_root",
            "hospital_radiology.menu_hospital_radiology_requests",
            "hospital_radiology.menu_hospital_radiology_results",
            "hospital_radiology.menu_hospital_radiology_configuration",
            "hospital_radiology.menu_hospital_radiology_exams",
        ):
            self.assertFalse(self._visible(self.lab_technician, xmlid), xmlid)
        self.assertNotIn(
            self.env.ref(LAB_TECHNICIAN),
            self.env.ref("hospital_radiology.menu_hospital_radiology_root").groups_id,
        )

    def test_catalogue_maintenance_menu_is_manager_and_admin_only(self):
        """The Radiology roles read the catalogue; they do not maintain it."""
        config = "hospital_radiology.menu_hospital_radiology_configuration"
        for user in (self.technician, self.radiologist, self.lab_technician):
            self.assertFalse(self._visible(user, config), user.login)
        for user in (self.manager, self.sysadmin):
            self.assertTrue(self._visible(user, config), user.login)

    def test_existing_menu_audiences_are_unchanged(self):
        root = "hospital_radiology.menu_hospital_radiology_root"
        for user in (
            self.doctor, self.manager, self.sysadmin, self.receptionist,
            self.nurse, self.dpo,
        ):
            self.assertTrue(self._visible(user, root), user.login)

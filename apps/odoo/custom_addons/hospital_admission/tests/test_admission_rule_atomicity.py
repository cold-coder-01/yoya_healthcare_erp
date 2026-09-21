"""Manager / System Administrator oversight, and record-rule ATOMICITY.

WHAT HAPPENED IN UAT. The Administrator (user 2: all nine hospital groups,
every ACL right on hospital.admission, empty nurse roster) saw zero
admissions, and an Access Error on ADM00001 opened by id. sudo() saw it.
It was not archived.

The database held exactly ONE record rule on hospital.admission: the nurse rule
from yoya_clinical_bridge. C:\\custom_addons had received the Slice 0 copy of
that module but kept the pre-Slice-0 hospital_admission, so the manager,
system-administrator, receptionist, accountant and doctor rules -- all in
hospital_admission/security/admission_security.xml -- were never installed.

Manager and System Administrator IMPLY Nurse, so the nurse rule applied to the
Administrator; Odoo ORs the group rules that EXIST, and with no manager rule to
OR against, the Administrator's view collapsed to
    [('ward_id.department_id', 'in', [])].

THE DEFECT WAS STRUCTURAL: a restrictive rule and the carve-out that
neutralises it for privileged roles lived in two different modules, so a
partial deployment could install one without the other. The carve-out now
travels in the same file as the restriction. These tests pin both the
behaviour and the structural rule that prevents a repeat.
"""
from odoo.exceptions import AccessError
from odoo.tests import tagged
from odoo.tools import mute_logger

from .common import G_ACCOUNTANT, G_ADMIN, G_DOCTOR, G_DPO, G_LAB, G_MANAGER, G_NURSE, G_PHARMACIST, G_RECEPTIONIST, AdmissionCase

ADMISSION_MODELS = ("hospital.admission", "hospital.admission.transfer")
UNRESTRICTED = "[(1, '=', 1)]"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"

# Every hospital group user 2 holds in the UAT database.
USER_2_GROUPS = [
    G_DPO, G_ACCOUNTANT, G_DOCTOR, G_LAB, G_MANAGER, G_NURSE, G_PHARMACIST,
    G_RECEPTIONIST, G_ADMIN,
]


def _normalised(domain):
    return " ".join((domain or "").split())


@tagged("post_install", "-at_install", "admission_rule_atomicity")
class TestAdmissionOversight(AdmissionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Deliberately NOT rostered to any department: the Administrator in
        # UAT had an empty roster, and that is what exposed the defect.
        cls.pure_manager = cls._make_user("adm_pure_manager", [G_MANAGER])
        cls.pure_sysadmin = cls._make_user("adm_pure_sysadmin", [G_ADMIN])
        cls.user_2_twin = cls._make_user("adm_user2_twin", USER_2_GROUPS)
        cls.dpo = cls._make_user("adm_dpo", [G_DPO])

    def _census(self):
        """One admission on each ward, plus an archived one."""
        mine = self._admitted(bed=self.bed_a)
        theirs = self._admitted(bed=self.bed_other)
        archived = self._admitted(bed=self.bed_b)
        archived.action_discharge()
        archived.sudo().write({"active": False})
        return mine, theirs, archived

    # ==================================================================
    # 1-3. Manager, System Administrator, and user 2's exact group set
    # ==================================================================
    def test_manager_sees_every_admission(self):
        mine, theirs, _ = self._census()
        visible = self.env["hospital.admission"].with_user(self.pure_manager).search([])
        self.assertIn(mine, visible)
        self.assertIn(theirs, visible)

    def test_system_administrator_sees_every_admission(self):
        mine, theirs, _ = self._census()
        visible = self.env["hospital.admission"].with_user(self.pure_sysadmin).search([])
        self.assertIn(mine, visible)
        self.assertIn(theirs, visible)

    def test_user_2s_exact_group_set_sees_everything_and_reads_by_id(self):
        """The UAT user, reconstructed: every hospital group, empty roster."""
        mine, theirs, archived = self._census()
        Admission = self.env["hospital.admission"].with_user(self.user_2_twin)
        self.assertEqual(
            Admission.with_context(active_test=False).search_count(
                [("id", "in", (mine | theirs | archived).ids)]
            ),
            3,
        )
        # The Access Error the human hit was a read of a known id.
        for record in (mine, theirs, archived):
            Admission.browse(record.id).check_access("read")
            self.assertTrue(Admission.browse(record.id).name)

    # ==================================================================
    # 4. Archived admissions
    # ==================================================================
    def test_manager_finds_archived_admissions_without_the_active_filter(self):
        _, _, archived = self._census()
        Admission = self.env["hospital.admission"].with_user(self.pure_manager)
        self.assertNotIn(archived, Admission.search([]))
        self.assertIn(archived, Admission.with_context(active_test=False).search([]))

    # ==================================================================
    # 5, 6, 15. Menu and action
    # ==================================================================
    def _visible_menu_xmlids(self, user):
        data = self.env["ir.ui.menu"].with_user(user).load_menus(False)
        return {
            item.get("xmlid")
            for key, item in data.items()
            if key != "root" and isinstance(item, dict)
        }, data

    def test_admissions_menu_is_visible_to_manager_and_system_administrator(self):
        root = self.env.ref("hospital_admission.menu_hospital_admission_root")
        for user in (self.pure_manager, self.pure_sysadmin, self.user_2_twin):
            xmlids, data = self._visible_menu_xmlids(user)
            with self.subTest(user=user.login):
                self.assertIn(root.id, data["root"]["children"])
                for xmlid in (
                    "hospital_admission.menu_hospital_admission_root",
                    "hospital_admission.menu_hospital_admission_admissions",
                    "hospital_admission.menu_hospital_admission_transfers",
                    "hospital_admission.menu_hospital_admission_beds",
                    "hospital_admission.menu_hospital_admission_configuration",
                ):
                    self.assertIn(xmlid, xmlids)

    def test_the_admission_action_hides_nothing_by_itself(self):
        """The action must not be where visibility is decided."""
        action = self.env.ref("hospital_admission.action_hospital_admission")
        self.assertFalse(action.domain)
        self.assertFalse(action.groups_id)
        self.assertNotIn("active_test", action.context or "")

    # ==================================================================
    # 7-13. The restrictions that must survive the fix
    # ==================================================================
    @mute_logger("odoo.addons.base.models.ir_model")
    def test_pharmacist_lab_and_dpo_cannot_enumerate_the_census(self):
        self._census()
        for user in (self.pharmacist, self.lab_tech, self.dpo):
            with self.subTest(user=user.login):
                with self.assertRaises(AccessError):
                    self.env["hospital.admission"].with_user(user).search([])

    def test_doctor_stays_scoped(self):
        mine = self._admitted(bed=self.bed_a)
        theirs = self._draft(bed=self.bed_other, physician_id=self.other_doctor.id)
        theirs.action_confirm_admission()
        visible = self.env["hospital.admission"].with_user(self.doctor_user).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible)

    def test_nurse_stays_scoped_to_permitted_departments(self):
        mine, theirs, _ = self._census()
        visible = self.env["hospital.admission"].with_user(self.nurse).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible)

    def test_front_desk_nurse_is_not_carved_out(self):
        """Front Desk Nurse implies Nurse too, but the oversight carve-out is
        for Manager and System Administrator only."""
        if not self.env.ref(G_FRONT_DESK_NURSE, raise_if_not_found=False):
            self.skipTest("yoya_reception_bridge is not installed")
        self._census()
        front_desk = self._make_user("adm_front_desk_nurse", [G_FRONT_DESK_NURSE])
        self.assertFalse(
            self.env["hospital.admission"].with_user(front_desk).search([])
        )

    def test_receptionist_keeps_operational_access(self):
        mine, theirs, _ = self._census()
        visible = self.env["hospital.admission"].with_user(self.receptionist).search([])
        self.assertIn(mine, visible)
        self.assertIn(theirs, visible)

    def test_accountant_sees_the_census_to_bill_it(self):
        mine, theirs, _ = self._census()
        visible = self.env["hospital.admission"].with_user(self.accountant).search([])
        self.assertIn(mine, visible)
        self.assertIn(theirs, visible)


@tagged("post_install", "-at_install", "admission_rule_atomicity")
class TestAdmissionRuleAtomicity(AdmissionCase):
    # ==================================================================
    # 14. Rules must not intersect to deny Manager / Admin
    # ==================================================================
    def _rules(self, model):
        return self.env["ir.rule"].sudo().search([("model_id.model", "=", model)])

    def test_no_global_rule_narrows_these_models(self):
        """A global rule is ANDed with everything, and no group rule can widen
        past it -- one wrong global rule hides the census from everyone."""
        for model in ADMISSION_MODELS:
            with self.subTest(model=model):
                self.assertFalse(self._rules(model).filtered("global"))

    def test_every_module_that_restricts_also_carves_out_oversight(self):
        """THE STRUCTURAL RULE. Any module shipping a restrictive rule on these
        models must, in the same module, ship an unrestricted rule for Manager
        and System Administrator on every operation the restriction covers --
        so no partial deployment can install one without the other."""
        manager = self.env.ref(G_MANAGER)
        sysadmin = self.env.ref(G_ADMIN)
        data = self.env["ir.model.data"].sudo()
        for model in ADMISSION_MODELS:
            by_module = {}
            for rule in self._rules(model):
                xmlid = data.search(
                    [("model", "=", "ir.rule"), ("res_id", "=", rule.id)], limit=1
                )
                by_module.setdefault(xmlid.module or "<no xmlid>", []).append(rule)
            for module, rules in by_module.items():
                restrictive = [
                    r for r in rules if _normalised(r.domain_force) != UNRESTRICTED
                ]
                carve_outs = [
                    r for r in rules if _normalised(r.domain_force) == UNRESTRICTED
                ]
                for rule in restrictive:
                    for perm in ("perm_read", "perm_write", "perm_create", "perm_unlink"):
                        if not rule[perm]:
                            continue
                        for group in (manager, sysadmin):
                            with self.subTest(model=model, module=module, rule=rule.name, perm=perm, group=group.name):
                                self.assertTrue(
                                    any(group in c.groups and c[perm] for c in carve_outs),
                                    "%s ships restrictive rule %r without a carve-out "
                                    "for %s on %s in the same module."
                                    % (module, rule.name, group.name, perm),
                                )

    def test_partial_deployment_does_not_lock_out_oversight(self):
        """REPLAY THE UAT STATE: remove every rule hospital_admission ships,
        leaving only what yoya_clinical_bridge ships -- which is what the UAT
        database held. Manager, System Administrator and user 2's group set
        must still see and read everything."""
        census = self._admitted(bed=self.bed_a) | self._admitted(bed=self.bed_other)

        own = self.env["ir.model.data"].sudo().search(
            [("module", "=", "hospital_admission"), ("model", "=", "ir.rule")]
        )
        rules = self.env["ir.rule"].sudo().browse(own.mapped("res_id")).exists()
        self.assertTrue(rules, "hospital_admission should ship rules to remove")
        rules.unlink()  # clears the rule cache itself

        remaining = self._rules("hospital.admission")
        self.assertTrue(
            remaining.filtered(lambda r: _normalised(r.domain_force) != UNRESTRICTED),
            "The nurse rule must still be present for this replay to mean anything",
        )

        for user in (
            self._make_user("adm_replay_manager", [G_MANAGER]),
            self._make_user("adm_replay_sysadmin", [G_ADMIN]),
            self._make_user("adm_replay_user2", USER_2_GROUPS),
        ):
            with self.subTest(user=user.login):
                Admission = self.env["hospital.admission"].with_user(user)
                self.assertEqual(
                    Admission.search_count([("id", "in", census.ids)]), 2
                )
                for record in census:
                    Admission.browse(record.id).check_access("read")

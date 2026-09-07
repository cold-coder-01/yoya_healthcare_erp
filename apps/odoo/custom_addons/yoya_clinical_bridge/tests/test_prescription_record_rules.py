"""Slice 6A: a doctor's prescriptions are theirs, not the hospital's.

WHAT THIS INHERITS. hospital_management ships ACL rows for the prescription
models and hospital_pharmacy ships them for the dispense models, and between
them there was not one record rule on any of the four. Every Hospital Doctor
could read AND WRITE every prescription in the building, and read every
dispense: a doctor in one department could open, and alter, the complete
medication history of a patient they have never seen.

WHY THE UNRESTRICTED RULES ARE TESTED TOO, AND NOT ASSUMED.
group_hospital_manager IMPLIES group_hospital_doctor, and
group_hospital_system_administrator implies manager in turn. Odoo ORs the
domains of every rule whose groups the user holds, so without an explicit
[(1,'=',1)] rule for those roles the doctor scope above would silently become a
manager's ceiling. That failure mode is invisible until somebody senior cannot
see a record, which is why it gets its own assertions here.

CONSULTATION IS ABSENT ON PURPOSE. hospital.prescription has no consultation_id
until Slice 6B, so the domains use the prescribing physician and the visit's
doctor. When 6B lands, the third branch joins them and these tests should keep
passing unchanged.
"""
import uuid

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "prescription_record_rules")
class TestPrescriptionRecordRules(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.user_a = cls._make_user(
            "rules_doctor_a", "hospital_management.group_hospital_doctor"
        )
        cls.user_b = cls._make_user(
            "rules_doctor_b", "hospital_management.group_hospital_doctor"
        )
        cls.pharmacist = cls._make_user(
            "rules_pharmacist", "hospital_management.group_hospital_pharmacist"
        )
        cls.manager = cls._make_user(
            "rules_manager", "hospital_management.group_hospital_manager"
        )
        cls.sysadmin = cls._make_user(
            "rules_sysadmin",
            "hospital_management.group_hospital_system_administrator",
        )
        cls.nurse = cls._make_user(
            "rules_nurse", "hospital_management.group_hospital_nurse"
        )
        cls.doctor_a = cls.env["hospital.doctor"].sudo().create(
            {"name": "Rules Doctor A", "user_id": cls.user_a.id}
        )
        cls.doctor_b = cls.env["hospital.doctor"].sudo().create(
            {"name": "Rules Doctor B", "user_id": cls.user_b.id}
        )
        cls.medicine = cls.env["hospital.pharmacy.medicine"].sudo().create({
            "name": "Rules Test Medicine",
            "code": "RTM-%s" % uuid.uuid4().hex[:6],
            "dosage_form": "capsule",
            "route": "oral",
        })

    @classmethod
    def _make_user(cls, login, *group_xmlids):
        groups = [cls.env.ref("base.group_user").id] + [
            cls.env.ref(x).id for x in group_xmlids
        ]
        suffix = uuid.uuid4().hex[:6]
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s_%s@example.test" % (login, suffix),
            "company_id": cls.company.id,
            "company_ids": [(6, 0, cls.company.ids)],
            "groups_id": [(6, 0, groups)],
        })

    def _prescription(self, doctor, visit_doctor=None):
        """A prescription written by `doctor` on a visit owned by
        `visit_doctor` (defaulting to the same one)."""
        visit_doctor = visit_doctor or doctor
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Rules Patient %s" % suffix}
        )
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": visit_doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": "confirmed",
        })
        return self.env["hospital.prescription"].sudo().create({
            "patient_id": patient.id,
            "physician_id": doctor.id,
            "appointment_id": appointment.id,
            "line_ids": [(0, 0, {
                "medicine_id": self.medicine.id,
                "medicine_name": self.medicine.name,
                "quantity": 3.0,
            })],
        })

    # ------------------------------------------------------------------
    # 22 + 23 + 24 -- the doctor scope itself
    # ------------------------------------------------------------------
    def test_doctor_reads_own_prescription(self):
        prescription = self._prescription(self.doctor_a)
        as_a = prescription.with_user(self.user_a)
        self.assertEqual(as_a.read(["name"])[0]["name"], prescription.name)

    def test_doctor_reads_prescription_on_a_visit_assigned_to_them(self):
        """The second branch: another clinician prescribed on Doctor A's visit."""
        prescription = self._prescription(self.doctor_b, visit_doctor=self.doctor_a)
        as_a = prescription.with_user(self.user_a)
        self.assertEqual(as_a.read(["name"])[0]["name"], prescription.name)

    def test_doctor_cannot_read_another_doctors_prescription(self):
        prescription = self._prescription(self.doctor_b)
        with self.assertRaises(AccessError):
            prescription.with_user(self.user_a).read(["name"])

    def test_doctor_cannot_write_another_doctors_prescription(self):
        prescription = self._prescription(self.doctor_b)
        with self.assertRaises(AccessError):
            prescription.with_user(self.user_a).write({"notes": "not mine"})
        prescription.invalidate_recordset()
        self.assertFalse(prescription.notes)

    def test_another_doctors_prescription_is_absent_from_search(self):
        """Invisible, not merely unreadable. A search that returned the id would
        leak the fact that the record exists."""
        mine = self._prescription(self.doctor_a)
        theirs = self._prescription(self.doctor_b)
        visible = self.env["hospital.prescription"].with_user(self.user_a).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible)

    # ------------------------------------------------------------------
    # 25 -- lines follow the parent
    # ------------------------------------------------------------------
    def test_prescription_line_follows_the_parent_scope(self):
        mine = self._prescription(self.doctor_a)
        theirs = self._prescription(self.doctor_b)
        Line = self.env["hospital.prescription.line"].with_user(self.user_a)
        visible = Line.search([])
        self.assertIn(mine.line_ids, visible)
        self.assertNotIn(theirs.line_ids, visible)
        with self.assertRaises(AccessError):
            theirs.line_ids.with_user(self.user_a).read(["medicine_name"])

    # ------------------------------------------------------------------
    # The dispense read scope added alongside
    # ------------------------------------------------------------------
    def test_dispense_scope_follows_the_prescription(self):
        mine = self._prescription(self.doctor_a)
        theirs = self._prescription(self.doctor_b)
        mine.sudo().action_confirm()
        theirs.sudo().action_confirm()
        Dispense = self.env["hospital.pharmacy.dispense"].with_user(self.user_a)
        visible = Dispense.search([])
        self.assertIn(mine.pharmacy_dispense_ids, visible)
        self.assertNotIn(theirs.pharmacy_dispense_ids, visible)

    def test_dispense_scope_grants_no_write(self):
        """Visibility must not have smuggled in an operation."""
        mine = self._prescription(self.doctor_a)
        mine.sudo().action_confirm()
        dispense = mine.pharmacy_dispense_ids[:1]
        with self.assertRaises(AccessError):
            dispense.with_user(self.user_a).write({"priority": "urgent"})

    # ------------------------------------------------------------------
    # 26 + 27 -- the roles that must keep seeing everything
    # ------------------------------------------------------------------
    def test_pharmacist_manager_and_sysadmin_see_every_prescription(self):
        a = self._prescription(self.doctor_a)
        b = self._prescription(self.doctor_b)
        for user in (self.pharmacist, self.manager, self.sysadmin):
            visible = self.env["hospital.prescription"].with_user(user).search([])
            self.assertIn(a, visible, "%s lost sight of a prescription" % user.name)
            self.assertIn(b, visible, "%s lost sight of a prescription" % user.name)

    def test_pharmacist_manager_and_sysadmin_see_every_prescription_line(self):
        a = self._prescription(self.doctor_a)
        b = self._prescription(self.doctor_b)
        for user in (self.pharmacist, self.manager, self.sysadmin):
            visible = self.env["hospital.prescription.line"].with_user(user).search([])
            self.assertIn(a.line_ids, visible)
            self.assertIn(b.line_ids, visible)

    def test_pharmacist_manager_and_sysadmin_see_every_dispense(self):
        a = self._prescription(self.doctor_a)
        b = self._prescription(self.doctor_b)
        a.sudo().action_confirm()
        b.sudo().action_confirm()
        for user in (self.pharmacist, self.manager, self.sysadmin):
            visible = self.env["hospital.pharmacy.dispense"].with_user(user).search([])
            self.assertIn(a.pharmacy_dispense_ids, visible)
            self.assertIn(b.pharmacy_dispense_ids, visible)

    def test_manager_scope_is_not_capped_by_the_implied_doctor_group(self):
        """The specific regression the unrestricted rules exist to prevent."""
        self.assertTrue(
            self.manager.has_group("hospital_management.group_hospital_doctor"),
            "the premise of this test is that Manager implies Doctor",
        )
        theirs = self._prescription(self.doctor_b)
        self.assertEqual(
            theirs.with_user(self.manager).read(["name"])[0]["name"], theirs.name
        )

    # ------------------------------------------------------------------
    # Roles this slice deliberately did not touch
    # ------------------------------------------------------------------
    def test_nurse_read_access_is_unchanged(self):
        """The nurse holds a read ACL and gets no rule, so their scope is
        exactly what it was. Asserted so a future rule cannot narrow it by
        accident and call it a Slice 6A decision."""
        theirs = self._prescription(self.doctor_b)
        self.assertEqual(
            theirs.with_user(self.nurse).read(["name"])[0]["name"], theirs.name
        )
        with self.assertRaises(AccessError):
            theirs.with_user(self.nurse).write({"notes": "nurses cannot write"})

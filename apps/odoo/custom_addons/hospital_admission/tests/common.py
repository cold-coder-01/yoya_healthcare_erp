"""Shared fixtures for the Admissions Slice 0 suites.

EVERY TEST HERE GOES THROUGH A REAL res.users WITH REAL GROUPS and calls the
model directly rather than a controller. That is the point: the boundary has to
hold for anyone who can reach the ORM, not merely for someone clicking a screen
that does not show the button. Nothing below passes a context flag, because a
context flag is client input and could never be the boundary.

The ward built here is given a department, because the nurse record rule in
yoya_clinical_bridge scopes by ward department and a ward without one is
invisible to every nurse. A fixture that omitted it would make the nurse tests
pass for the wrong reason.
"""
import uuid

from odoo import fields
from odoo.tests import TransactionCase

G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_NURSE = "hospital_management.group_hospital_nurse"
G_PHARMACIST = "hospital_management.group_hospital_pharmacist"
G_LAB = "hospital_management.group_hospital_lab_technician"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
G_DPO = "hospital_management.group_hospital_data_protection_officer"


class AdmissionCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company

        cls.department = cls.env["hospital.department"].sudo().create(
            {
                "name": "Admissions Test Department %s" % uuid.uuid4().hex[:6],
                "code": "ADT%s" % uuid.uuid4().hex[:6].upper(),
            }
        )
        cls.other_department = cls.env["hospital.department"].sudo().create(
            {
                "name": "Admissions Other Department %s" % uuid.uuid4().hex[:6],
                "code": "ADO%s" % uuid.uuid4().hex[:6].upper(),
            }
        )

        cls.receptionist = cls._make_user("adm_reception", [G_RECEPTIONIST])
        cls.nurse = cls._make_user("adm_nurse", [G_NURSE])
        cls.other_nurse = cls._make_user("adm_nurse_other", [G_NURSE])
        cls.doctor_user = cls._make_user("adm_doctor", [G_DOCTOR])
        cls.other_doctor_user = cls._make_user("adm_doctor_other", [G_DOCTOR])
        cls.pharmacist = cls._make_user("adm_pharmacist", [G_PHARMACIST])
        cls.lab_tech = cls._make_user("adm_lab", [G_LAB])
        cls.accountant = cls._make_user("adm_accountant", [G_ACCOUNTANT])
        cls.manager = cls._make_user("adm_manager", [G_MANAGER])
        cls.admin = cls._make_user("adm_admin", [G_ADMIN])

        # The nurse roster lives on res.users and is contributed by
        # yoya_clinical_bridge. Rostering the nurse to the test department is
        # what puts the fixture ward inside their record rule.
        if "yoya_permitted_department_ids" in cls.env["res.users"]._fields:
            cls.nurse.sudo().write(
                {"yoya_permitted_department_ids": [(6, 0, [cls.department.id])]}
            )
            cls.other_nurse.sudo().write(
                {"yoya_permitted_department_ids": [(6, 0, [cls.other_department.id])]}
            )

        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Admissions Test Doctor", "user_id": cls.doctor_user.id}
        )
        cls.other_doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Admissions Other Doctor", "user_id": cls.other_doctor_user.id}
        )

        cls.ward = cls._make_ward("Slice0 Ward A", cls.department)
        cls.room = cls._make_room(cls.ward, "S0-A-101")
        cls.bed_a = cls._make_bed(cls.room, "S0-A-101-1")
        cls.bed_b = cls._make_bed(cls.room, "S0-A-101-2")

        cls.ward_other = cls._make_ward("Slice0 Ward B", cls.other_department)
        cls.room_other = cls._make_room(cls.ward_other, "S0-B-201")
        cls.bed_other = cls._make_bed(cls.room_other, "S0-B-201-1")

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------
    @classmethod
    def _make_user(cls, login, groups):
        return cls.env["res.users"].sudo().create(
            {
                "name": login,
                "login": "%s_%s" % (login, uuid.uuid4().hex[:8]),
                "company_id": cls.company.id,
                "company_ids": [(6, 0, cls.company.ids)],
                "groups_id": [
                    (6, 0, [cls.env.ref("base.group_user").id]
                     + [cls.env.ref(g).id for g in groups])
                ],
            }
        )

    @classmethod
    def _make_ward(cls, name, department=None):
        return cls.env["hospital.ward"].sudo().create(
            {
                "name": "%s %s" % (name, uuid.uuid4().hex[:6]),
                "code": "W%s" % uuid.uuid4().hex[:6].upper(),
                "ward_type": "medical",
                "department_id": department.id if department else False,
                "company_id": cls.company.id,
                "admission_fee": 500.0,
                "daily_ward_rate": 800.0,
            }
        )

    @classmethod
    def _make_room(cls, ward, name):
        return cls.env["hospital.room"].sudo().create(
            {
                "name": "%s %s" % (name, uuid.uuid4().hex[:4]),
                "code": "R%s" % uuid.uuid4().hex[:6].upper(),
                "ward_id": ward.id,
                "room_type": "shared",
            }
        )

    @classmethod
    def _make_bed(cls, room, name):
        return cls.env["hospital.bed"].sudo().create(
            {
                "name": "%s %s" % (name, uuid.uuid4().hex[:4]),
                "code": "B%s" % uuid.uuid4().hex[:6].upper(),
                "room_id": room.id,
                "bed_type": "standard",
            }
        )

    def _patient(self, name="Slice0 Patient"):
        return self.env["hospital.patient"].sudo().create(
            {"name": "%s %s" % (name, uuid.uuid4().hex[:6])}
        )

    def _encounter(self, patient, state="active", **overrides):
        """An open episode for the patient.

        Created through sudo() rather than the reception workflow so these
        suites test admission authority rather than registration. The
        single-active-episode guard in yoya_reception_bridge still applies to
        this create, which is why each test uses a fresh patient.
        """
        vals = {
            "patient_id": patient.id,
            "encounter_type": "outpatient",
            "company_id": self.company.id,
            "opened_at": fields.Datetime.now(),
        }
        vals.update(overrides)
        encounter = self.env["hospital.encounter"].sudo().create(vals)
        if state and encounter.state != state:
            encounter.sudo().write({"state": state})
        return encounter

    def _draft(self, patient=None, bed=None, encounter=True, **overrides):
        """A draft admission ready to confirm."""
        patient = patient or self._patient()
        if encounter:
            self._encounter(patient)
        bed = self.bed_a if bed is None else bed
        vals = {
            "patient_id": patient.id,
            "physician_id": self.doctor.id,
            "company_id": self.company.id,
            "admission_date": fields.Datetime.now(),
        }
        if bed:
            vals.update(
                {
                    "ward_id": bed.ward_id.id,
                    "room_id": bed.room_id.id,
                    "bed_id": bed.id,
                }
            )
        vals.update(overrides)
        return self.env["hospital.admission"].sudo().create(vals)

    def _admitted(self, patient=None, bed=None, **overrides):
        admission = self._draft(patient=patient, bed=bed, **overrides)
        admission.action_confirm_admission()
        return admission

    def _raw(self, sql, params):
        """Reach past the ORM to build a corrupt shape the guards must catch.

        FLUSH FIRST, AND INVALIDATE WITHOUT FLUSHING AFTER. Odoo defers writes:
        _set_occupancy()'s write lives in the cache until something flushes it.
        A raw UPDATE issued while that write is still pending is silently undone
        by the next flush -- and env.invalidate_all() flushes by default, so the
        obvious spelling of this helper quietly does nothing. Both arguments
        below are load-bearing.
        """
        self.env.flush_all()
        self.cr.execute(sql, params)
        self.env.invalidate_all(flush=False)

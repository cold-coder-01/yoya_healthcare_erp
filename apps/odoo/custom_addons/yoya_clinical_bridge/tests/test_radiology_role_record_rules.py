"""Radiology Slice 0A: the hospital-wide Radiology rules follow the new roles.

The five "operations" rules on the Radiology models used to name Lab Technician
as the imaging bench. They now name Radiology Technician and Radiologist, next to
Manager and System Administrator. The Doctor rules are not touched.

Three things are asserted, in both directions:

  * the rules' GROUP SETS are exactly the intended four -- read from the rules
    themselves, because an upgrade that only LINKED the new groups would leave
    Lab Technician attached and still pass every behavioural test that happened
    to use a pure Radiology user;
  * the Radiology roles really see the whole department's work, across doctors;
  * a Lab Technician no longer reaches any Radiology row at all, while a doctor
    is still held to their own orders.
"""
import base64
import uuid

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

from odoo.addons.hospital_radiology.models.radiology_result import (
    _result_workflow_capability,
)

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)

TECHNICIAN = "hospital_radiology.group_hospital_radiology_technician"
RADIOLOGIST = "hospital_radiology.group_hospital_radiologist"
LAB_TECHNICIAN = "hospital_management.group_hospital_lab_technician"
DOCTOR = "hospital_management.group_hospital_doctor"
MANAGER = "hospital_management.group_hospital_manager"
SYSADMIN = "hospital_management.group_hospital_system_administrator"

OPERATIONS_RULES = (
    "yoya_clinical_bridge.rule_radiology_request_operations",
    "yoya_clinical_bridge.rule_radiology_request_line_operations",
    "yoya_clinical_bridge.rule_radiology_result_operations",
    "yoya_clinical_bridge.rule_radiology_result_line_operations",
    "yoya_clinical_bridge.rule_radiology_image_operations",
)

RADIOLOGY_MODELS = (
    "hospital.radiology.exam",
    "hospital.radiology.request",
    "hospital.radiology.request.line",
    "hospital.radiology.result",
    "hospital.radiology.result.line",
    "hospital.radiology.image",
)


@tagged("post_install", "-at_install", "radiology_role_record_rules")
class TestRadiologyRoleRecordRules(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.technician = cls._make_user("rad0a_rr_tech", TECHNICIAN)
        cls.radiologist = cls._make_user("rad0a_rr_radiologist", RADIOLOGIST)
        cls.lab_technician = cls._make_user("rad0a_rr_lab", LAB_TECHNICIAN)
        cls.manager = cls._make_user("rad0a_rr_manager", MANAGER)
        cls.sysadmin = cls._make_user("rad0a_rr_sysadmin", SYSADMIN)
        cls.user_a = cls._make_user("rad0a_rr_doc_a", DOCTOR)
        cls.user_b = cls._make_user("rad0a_rr_doc_b", DOCTOR)
        cls.doctor_a = cls.env["hospital.doctor"].sudo().create(
            {"name": "Rad 0A Doctor A", "user_id": cls.user_a.id}
        )
        cls.doctor_b = cls.env["hospital.doctor"].sudo().create(
            {"name": "Rad 0A Doctor B", "user_id": cls.user_b.id}
        )
        tag = uuid.uuid4().hex[:6]
        cls.exam = cls.env["hospital.radiology.exam"].sudo().create({
            "name": "Role Rules Exam %s" % tag,
            "code": "RRX%s" % tag.upper(),
            "modality": "xray",
        })
        cls.study_a = cls._study(cls.doctor_a)
        cls.study_b = cls._study(cls.doctor_b)

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

    @classmethod
    def _study(cls, doctor):
        """Radiology Slice 3: the report model refuses a direct state write, and a report on a request that has not started. This fixture arranges that legacy shape through the result workflow capability -- a server-side ContextVar, never a forged context."""
        with _result_workflow_capability():
            return cls._arrange_study(doctor)

    @classmethod
    def _arrange_study(cls, doctor):
        """One doctor's request, line, result, result line and image."""
        suffix = uuid.uuid4().hex[:8]
        patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Rad 0A Patient %s" % suffix}
        )
        appointment = cls.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": "confirmed",
        })
        request = cls.env["hospital.radiology.request"].sudo().create({
            "patient_id": patient.id,
            "physician_id": doctor.id,
            "appointment_id": appointment.id,
            "line_ids": [(0, 0, {"exam_id": cls.exam.id})],
        })
        result = cls.env["hospital.radiology.result"].sudo().create({
            "request_id": request.id,
            "patient_id": patient.id,
            "physician_id": doctor.id,
        })
        image = cls.env["hospital.radiology.image"].sudo().create({
            "result_id": result.id,
            "name": "Study %s" % suffix,
            "filename": "study.png",
            "file": PNG,
        })
        return {
            "hospital.radiology.request": request,
            "hospital.radiology.request.line": request.line_ids,
            "hospital.radiology.result": result,
            "hospital.radiology.result.line": result.line_ids,
            "hospital.radiology.image": image,
        }

    # ------------------------------------------------------------------
    # The rule definitions
    # ------------------------------------------------------------------
    def test_operations_rules_name_exactly_the_radiology_roles_and_oversight(self):
        expected = (
            self.env.ref(TECHNICIAN)
            | self.env.ref(RADIOLOGIST)
            | self.env.ref(MANAGER)
            | self.env.ref(SYSADMIN)
        )
        for xmlid in OPERATIONS_RULES:
            rule = self.env.ref(xmlid)
            self.assertEqual(rule.groups, expected, xmlid)
            self.assertEqual(rule.domain_force.replace(" ", ""), "[(1,'=',1)]", xmlid)
            self.assertTrue(rule.active, xmlid)

    def test_no_radiology_record_rule_names_lab_technician(self):
        rules = self.env["ir.rule"].sudo().with_context(active_test=False).search([
            ("model_id.model", "in", list(RADIOLOGY_MODELS)),
            ("groups", "in", self.env.ref(LAB_TECHNICIAN).ids),
        ])
        self.assertFalse(rules, rules.mapped("name"))

    # ------------------------------------------------------------------
    # The Radiology roles see the department's work
    # ------------------------------------------------------------------
    def test_radiology_roles_see_every_doctors_radiology_records(self):
        for user in (self.technician, self.radiologist, self.manager, self.sysadmin):
            for model, record_a in self.study_a.items():
                record_b = self.study_b[model]
                visible = self.env[model].with_user(user).search(
                    [("id", "in", (record_a | record_b).ids)]
                )
                self.assertEqual(visible, record_a | record_b, "%s %s" % (user.login, model))

    # ------------------------------------------------------------------
    # Lab Technician is out; the doctor scope is unchanged
    # ------------------------------------------------------------------
    def test_lab_technician_alone_cannot_read_any_radiology_record(self):
        for model, record in self.study_a.items():
            with self.assertRaises(AccessError, msg=model):
                self.env[model].with_user(self.lab_technician).search([("id", "=", record.id)])
            with self.assertRaises(AccessError, msg=model):
                record.with_user(self.lab_technician).read(["id"])
        with self.assertRaises(AccessError):
            self.env["hospital.radiology.exam"].with_user(self.lab_technician).search([])

    def test_lab_technician_alone_cannot_drive_the_radiology_workflow(self):
        request = self.study_a["hospital.radiology.request"]
        with self.assertRaises(AccessError):
            request.with_user(self.lab_technician).action_confirm_request()
        self.assertEqual(request.state, "draft")

    def test_doctor_scope_is_unchanged(self):
        """A doctor still sees their own orders and not another doctor's."""
        for model, mine in self.study_a.items():
            theirs = self.study_b[model]
            visible = self.env[model].with_user(self.user_a).search(
                [("id", "in", (mine | theirs).ids)]
            )
            self.assertEqual(visible, mine, model)

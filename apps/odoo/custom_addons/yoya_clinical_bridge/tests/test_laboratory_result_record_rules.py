"""Slice 7A: a doctor's laboratory results are theirs, not the hospital's.

WHAT THIS INHERITS. hospital_management ships ACL rows for
hospital.laboratory.result and hospital.laboratory.result.line -- read-only for
the doctor group, which is correct -- and NOT ONE RECORD RULE on either model.
The laboratory REQUEST rules were added by the ordering slice and scoped exactly
the models that slice touched; the result models were never brought along. So
every Hospital Doctor could read every laboratory result in the building:
another department's patient, another physician's investigation, values and
reference ranges included.

WHY THE UNRESTRICTED RULES ARE TESTED TOO, AND NOT ASSUMED.
group_hospital_manager IMPLIES group_hospital_doctor, and
group_hospital_system_administrator implies manager in turn. Odoo ORs the
domains of every rule whose groups the user holds, so without an explicit
[(1,'=',1)] rule for those roles the doctor scope would silently become a
manager's ceiling. That failure mode is invisible until somebody senior cannot
see a record, which is why it gets its own assertions here.

THE FIXTURES DRIVE STATE, NOT THE WORKFLOW, AND THAT IS DELIBERATE.
A laboratory result may only be created against a request whose sample exists
(_check_request_state_eligible), so the requests below are walked
draft -> requested -> sample_collected by writing `state`, which the model's own
_check_state_transition whitelist permits at every step. The action methods are
NOT used because hospital_billing overrides them to raise charges, open
encounters and check clearance -- a whole financial apparatus this file has no
business exercising to ask a question about ir.rule composition. Nothing here
asserts anything about the laboratory workflow, and nothing here should.

WHAT THIS FILE DOES NOT TEST, ON PURPOSE. No visibility predicate, no
released-vs-validated decision, no serializer and no endpoint: none of those
exist yet. Results are read here in every state, because a record rule does not
know what a result state is and must not be tested as though it did.
"""
import uuid

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "laboratory_result_record_rules")
class TestLaboratoryResultRecordRules(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.user_a = cls._make_user(
            "labres_doctor_a", "hospital_management.group_hospital_doctor"
        )
        cls.user_b = cls._make_user(
            "labres_doctor_b", "hospital_management.group_hospital_doctor"
        )
        cls.lab_technician = cls._make_user(
            "labres_technician", "hospital_management.group_hospital_lab_technician"
        )
        cls.manager = cls._make_user(
            "labres_manager", "hospital_management.group_hospital_manager"
        )
        cls.sysadmin = cls._make_user(
            "labres_sysadmin",
            "hospital_management.group_hospital_system_administrator",
        )
        cls.nurse = cls._make_user(
            "labres_nurse", "hospital_management.group_hospital_nurse"
        )
        cls.doctor_a = cls.env["hospital.doctor"].sudo().create(
            {"name": "Lab Result Doctor A", "user_id": cls.user_a.id}
        )
        cls.doctor_b = cls.env["hospital.doctor"].sudo().create(
            {"name": "Lab Result Doctor B", "user_id": cls.user_b.id}
        )
        cls.test = cls.env["hospital.laboratory.test"].sudo().create({
            "name": "Rules Test Assay",
            "code": "RTA-%s" % uuid.uuid4().hex[:6],
            "category": "chemistry",
            "sample_type": "blood",
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

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    def _result(
        self,
        result_physician,
        request_physician=None,
        visit_doctor=None,
        consultation_doctor=None,
    ):
        """A laboratory result whose four scope branches are set independently.

        Each argument drives exactly one branch of the doctor domain, so a test
        can make ONE branch true and leave the others pointing elsewhere. That
        is the only way to show a branch carries its own weight rather than
        riding on a sibling.

        `consultation_doctor` is the exception, and the model is why -- see
        test_doctor_reads_result_of_a_consultation_they_conducted.
        """
        request_physician = request_physician or result_physician
        visit_doctor = visit_doctor or request_physician
        suffix = uuid.uuid4().hex[:8]

        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Lab Result Patient %s" % suffix}
        )
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": visit_doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": "confirmed",
        })

        request_vals = {
            "patient_id": patient.id,
            "physician_id": request_physician.id,
            "appointment_id": appointment.id,
            "line_ids": [(0, 0, {"test_id": self.test.id, "sample_type": "blood"})],
        }
        if consultation_doctor is not None:
            encounter = self.env["hospital.encounter"].sudo().create({
                "name": "ENC-%s" % suffix,
                "patient_id": patient.id,
                "appointment_id": appointment.id,
                "company_id": self.company.id,
            })
            consultation = self.env["hospital.consultation"].sudo().create({
                "encounter_id": encounter.id,
                "doctor_id": consultation_doctor.id,
            })
            request_vals["consultation_id"] = consultation.id
            request_vals["encounter_id"] = encounter.id

        request = self.env["hospital.laboratory.request"].sudo().create(request_vals)

        # A result needs a request whose sample exists. Written directly rather
        # than through the action methods -- see the module docstring.
        request.write({"state": "requested"})
        request.write({"state": "sample_collected"})

        # line_ids omitted on purpose: create() builds one result line per
        # ordered request line, carrying request_line_id, which is the only
        # shape the line model accepts.
        return self.env["hospital.laboratory.result"].sudo().create({
            "request_id": request.id,
            "patient_id": patient.id,
            "physician_id": result_physician.id,
            "result_date": fields.Date.context_today(self.env.user),
        })

    # ------------------------------------------------------------------
    # 1-4 -- the four branches, each on its own
    # ------------------------------------------------------------------
    def test_doctor_reads_result_naming_them_as_physician(self):
        """Branch A: the result's own physician_id, and nothing else."""
        result = self._result(
            result_physician=self.doctor_a,
            request_physician=self.doctor_b,
            visit_doctor=self.doctor_b,
        )
        self.assertEqual(
            result.with_user(self.user_a).read(["name"])[0]["name"], result.name
        )

    def test_doctor_reads_result_of_a_request_they_ordered(self):
        """Branch B: they ordered the investigation; someone else reported it."""
        result = self._result(
            result_physician=self.doctor_b,
            request_physician=self.doctor_a,
            visit_doctor=self.doctor_b,
        )
        self.assertEqual(
            result.with_user(self.user_a).read(["name"])[0]["name"], result.name
        )

    def test_doctor_reads_result_of_a_visit_assigned_to_them(self):
        """Branch C: another clinician ordered and reported on THEIR visit.

        The branch that matters for covering colleagues: the result names
        Doctor B twice over, and Doctor A reaches it only through the
        appointment they are assigned to.
        """
        result = self._result(
            result_physician=self.doctor_b,
            request_physician=self.doctor_b,
            visit_doctor=self.doctor_a,
        )
        self.assertEqual(
            result.with_user(self.user_a).read(["name"])[0]["name"], result.name
        )

    def test_doctor_reads_result_of_a_consultation_they_conducted(self):
        """Branch D, asserted for the SHAPE it will actually be served in.

        THIS BRANCH CANNOT BE ISOLATED, and that is a fact about the model
        rather than a gap in the test. yoya_clinical_bridge's
        _check_consultation_references refuses a consultation-linked request
        whose physician_id disagrees with the consultation's doctor_id, so a row
        where branch D holds and branch B does not cannot exist in the database.
        Forging one with sudo would test a state the schema forbids.

        What is asserted instead is the real Slice 7 shape end to end: a result
        for a request placed from a doctor's own consultation is readable by
        that doctor -- and, below, invisible to another.
        """
        result = self._result(
            result_physician=self.doctor_a,
            request_physician=self.doctor_a,
            visit_doctor=self.doctor_a,
            consultation_doctor=self.doctor_a,
        )
        self.assertTrue(
            result.request_id.consultation_id,
            "the fixture did not produce a consultation-linked request",
        )
        self.assertEqual(
            result.with_user(self.user_a).read(["name"])[0]["name"], result.name
        )
        with self.assertRaises(AccessError):
            result.with_user(self.user_b).read(["name"])

    # ------------------------------------------------------------------
    # 5-7 -- what the scope refuses
    # ------------------------------------------------------------------
    def test_doctor_cannot_read_another_doctors_result(self):
        """THE defect this slice exists to close."""
        result = self._result(self.doctor_b)
        with self.assertRaises(AccessError):
            result.with_user(self.user_a).read(["name"])

    def test_another_doctors_result_is_absent_from_search(self):
        """Invisible, not merely unreadable. A search that returned the id would
        leak that another patient's investigation exists."""
        mine = self._result(self.doctor_a)
        theirs = self._result(self.doctor_b)
        visible = self.env["hospital.laboratory.result"].with_user(self.user_a).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible)

    def test_result_line_follows_the_parent_scope(self):
        """The line holds the measured VALUE, so a line readable without its
        result -- or the reverse -- would be the worst of both."""
        mine = self._result(self.doctor_a)
        theirs = self._result(self.doctor_b)
        self.assertTrue(mine.line_ids and theirs.line_ids, "fixture produced no lines")

        Line = self.env["hospital.laboratory.result.line"].with_user(self.user_a)
        visible = Line.search([])
        self.assertIn(mine.line_ids, visible)
        self.assertNotIn(theirs.line_ids, visible)
        with self.assertRaises(AccessError):
            theirs.line_ids.with_user(self.user_a).read(["result_value"])

    # ------------------------------------------------------------------
    # 8-11 -- visibility must not have smuggled in an operation
    # ------------------------------------------------------------------
    def test_doctor_cannot_write_their_own_result(self):
        """Read scope is not authorship. The doctor ACL is 1,0,0,0 and the rule
        grants nothing, so even a result that is unambiguously theirs stays
        unwritable -- reporting is the laboratory's act."""
        result = self._result(self.doctor_a)
        with self.assertRaises(AccessError):
            result.with_user(self.user_a).write({"remarks": "not mine to write"})
        result.invalidate_recordset()
        self.assertFalse(result.remarks)

    def test_doctor_cannot_write_their_own_result_line(self):
        result = self._result(self.doctor_a)
        line = result.line_ids[:1]
        with self.assertRaises(AccessError):
            line.with_user(self.user_a).write({"result_value": "99"})
        line.invalidate_recordset()
        self.assertFalse(line.result_value)

    def test_doctor_cannot_create_a_result(self):
        result = self._result(self.doctor_a)
        with self.assertRaises(AccessError):
            self.env["hospital.laboratory.result"].with_user(self.user_a).create({
                "request_id": result.request_id.id,
                "patient_id": result.patient_id.id,
                "physician_id": self.doctor_a.id,
                "result_date": fields.Date.context_today(self.env.user),
            })

    def test_doctor_cannot_create_a_result_line(self):
        result = self._result(self.doctor_a)
        with self.assertRaises(AccessError):
            self.env["hospital.laboratory.result.line"].with_user(self.user_a).create({
                "result_id": result.id,
                "request_line_id": result.request_id.line_ids[:1].id,
                "test_id": self.test.id,
            })

    # ------------------------------------------------------------------
    # 12-17 -- the roles that must keep seeing everything
    # ------------------------------------------------------------------
    def test_operational_roles_see_every_result(self):
        a = self._result(self.doctor_a)
        b = self._result(self.doctor_b)
        for user in (self.lab_technician, self.manager, self.sysadmin):
            visible = self.env["hospital.laboratory.result"].with_user(user).search([])
            self.assertIn(a, visible, "%s lost sight of a result" % user.name)
            self.assertIn(b, visible, "%s lost sight of a result" % user.name)

    def test_operational_roles_see_every_result_line(self):
        a = self._result(self.doctor_a)
        b = self._result(self.doctor_b)
        for user in (self.lab_technician, self.manager, self.sysadmin):
            visible = (
                self.env["hospital.laboratory.result.line"].with_user(user).search([])
            )
            self.assertIn(a.line_ids, visible, "%s lost sight of a line" % user.name)
            self.assertIn(b.line_ids, visible, "%s lost sight of a line" % user.name)

    def test_manager_scope_is_not_capped_by_the_implied_doctor_group(self):
        """The specific regression the unrestricted rules exist to prevent."""
        self.assertTrue(
            self.manager.has_group("hospital_management.group_hospital_doctor"),
            "the premise of this test is that Manager implies Doctor",
        )
        theirs = self._result(self.doctor_b)
        self.assertEqual(
            theirs.with_user(self.manager).read(["name"])[0]["name"], theirs.name
        )
        self.assertEqual(
            theirs.line_ids.with_user(self.manager).read(["test_id"])[0]["id"],
            theirs.line_ids.id,
        )

    def test_sysadmin_scope_is_not_capped_by_the_implied_doctor_group(self):
        """Sysadmin implies manager implies doctor -- two hops, same trap."""
        self.assertTrue(
            self.sysadmin.has_group("hospital_management.group_hospital_doctor"),
            "the premise of this test is that Sysadmin transitively implies Doctor",
        )
        theirs = self._result(self.doctor_b)
        self.assertEqual(
            theirs.with_user(self.sysadmin).read(["name"])[0]["name"], theirs.name
        )

    def test_lab_technician_keeps_the_cross_patient_bench_queue(self):
        """The bench works everyone's samples; scoping it would break the
        laboratory. Asserted rather than assumed because the technician's
        unrestricted rule is new in this change."""
        a = self._result(self.doctor_a)
        b = self._result(self.doctor_b)
        as_tech = self.env["hospital.laboratory.result"].with_user(self.lab_technician)
        self.assertIn(a, as_tech.search([]))
        self.assertIn(b, as_tech.search([]))
        # And still able to do the job: the ACL is 1,1,1,0 for this group, and
        # the rule must not have become the limiting factor.
        b.with_user(self.lab_technician).write({"remarks": "bench note"})
        self.assertEqual(b.remarks, "bench note")

    # ------------------------------------------------------------------
    # 18 -- roles this slice deliberately did not touch
    # ------------------------------------------------------------------
    def test_nurse_read_access_is_unchanged(self):
        """The nurse holds a read ACL, is not in the doctor group and gets no
        rule, so their scope is exactly what it was. Asserted so a future rule
        cannot narrow it by accident and call it a Slice 7A decision."""
        theirs = self._result(self.doctor_b)
        self.assertEqual(
            theirs.with_user(self.nurse).read(["name"])[0]["name"], theirs.name
        )
        with self.assertRaises(AccessError):
            theirs.with_user(self.nurse).write({"remarks": "nurses cannot write"})

    def test_nurse_result_line_read_access_is_unchanged(self):
        theirs = self._result(self.doctor_b)
        visible = (
            self.env["hospital.laboratory.result.line"].with_user(self.nurse).search([])
        )
        self.assertIn(theirs.line_ids, visible)

    # ------------------------------------------------------------------
    # The radiology rules this change mirrors must still hold
    # ------------------------------------------------------------------
    def test_the_doctor_acl_on_both_result_models_is_still_read_only(self):
        """The rules narrow WHICH rows; the ACL decides whether the operation
        exists at all. If a later change opened write on either model, the
        assertions above would still pass for the wrong reason."""
        doctor_group = self.env.ref("hospital_management.group_hospital_doctor")
        for model in (
            "hospital.laboratory.result",
            "hospital.laboratory.result.line",
        ):
            access = self.env["ir.model.access"].sudo().search([
                ("model_id.model", "=", model),
                ("group_id", "=", doctor_group.id),
            ])
            self.assertTrue(access, "no doctor ACL row for %s" % model)
            self.assertTrue(all(access.mapped("perm_read")), model)
            self.assertFalse(any(access.mapped("perm_write")), model)
            self.assertFalse(any(access.mapped("perm_create")), model)
            self.assertFalse(any(access.mapped("perm_unlink")), model)

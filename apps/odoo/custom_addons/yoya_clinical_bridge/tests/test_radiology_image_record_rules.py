"""Slice 8A: a doctor's radiology images are theirs, not the hospital's.

WHY A RULE ON THIS MODEL PROTECTS THE FILE AND NOT JUST A ROW.
`hospital.radiology.image.file` is Binary(attachment=True), so the bytes live in
an ir.attachment carrying res_model='hospital.radiology.image'. Odoo resolves
access to that attachment by browsing res_model/res_id and calling
check_access() on the record -- and there is no ir.rule on ir.attachment
anywhere in this database. The domain asserted below is therefore the only
thing standing between a clinician and another patient's imaging, which is why
it gets its own file rather than a line in someone else's.

WHY THE UNRESTRICTED RULES ARE TESTED TOO, AND NOT ASSUMED.
group_hospital_manager IMPLIES group_hospital_doctor, and
group_hospital_system_administrator implies manager in turn. Odoo ORs the
domains of every rule whose groups the user holds, so without an explicit
[(1,'=',1)] rule for those roles the doctor scope would silently become a
manager's ceiling -- and the imaging bench would lose sight of its own work.
That failure is invisible until somebody senior cannot open a study.

FIXTURES DRIVE STATE, NOT THE WORKFLOW, for the reason the laboratory-result
rule tests give: these are questions about ir.rule composition, and driving
hospital_billing's charge-delivering action methods to ask them would exercise
a financial apparatus this file has no business touching.
"""
import base64
import uuid

from odoo import fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)


@tagged("post_install", "-at_install", "radiology_image_record_rules")
class TestRadiologyImageRecordRules(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.user_a = cls._make_user(
            "radimg_doctor_a", "hospital_management.group_hospital_doctor"
        )
        cls.user_b = cls._make_user(
            "radimg_doctor_b", "hospital_management.group_hospital_doctor"
        )
        cls.bench = cls._make_user(
            "radimg_bench", "hospital_management.group_hospital_lab_technician"
        )
        cls.manager = cls._make_user(
            "radimg_manager", "hospital_management.group_hospital_manager"
        )
        cls.sysadmin = cls._make_user(
            "radimg_sysadmin",
            "hospital_management.group_hospital_system_administrator",
        )
        cls.doctor_a = cls.env["hospital.doctor"].sudo().create(
            {"name": "Rad Image Doctor A", "user_id": cls.user_a.id}
        )
        cls.doctor_b = cls.env["hospital.doctor"].sudo().create(
            {"name": "Rad Image Doctor B", "user_id": cls.user_b.id}
        )
        tag = uuid.uuid4().hex[:6]
        cls.exam = cls.env["hospital.radiology.exam"].sudo().create({
            "name": "Rules Exam %s" % tag,
            "code": "RIX%s" % tag.upper(),
            "modality": "ct",
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
    def _image(self, result_physician, request_physician=None, visit_doctor=None):
        """An image whose three reachable scope branches are set separately.

        The consultation branch is not isolated here for the reason the
        laboratory-result rule tests document: the bridge's own
        _check_consultation_references refuses a consultation-linked request
        whose physician disagrees with the consultation's doctor, so a row where
        that branch holds and the request-physician branch does not cannot exist.
        """
        request_physician = request_physician or result_physician
        visit_doctor = visit_doctor or request_physician
        suffix = uuid.uuid4().hex[:8]
        patient = self.env["hospital.patient"].sudo().create(
            {"name": "Rad Image Patient %s" % suffix}
        )
        appointment = self.env["hospital.appointment"].sudo().create({
            "patient_id": patient.id,
            "doctor_id": visit_doctor.id,
            "appointment_date": fields.Datetime.now(),
            "state": "confirmed",
        })
        request = self.env["hospital.radiology.request"].sudo().create({
            "patient_id": patient.id,
            "physician_id": request_physician.id,
            "appointment_id": appointment.id,
            "line_ids": [(0, 0, {"exam_id": self.exam.id})],
        })
        result = self.env["hospital.radiology.result"].sudo().create({
            "request_id": request.id,
            "patient_id": patient.id,
            "physician_id": result_physician.id,
        })
        return self.env["hospital.radiology.image"].sudo().create({
            "result_id": result.id,
            "name": "Study %s" % suffix,
            "filename": "study.png",
            "file": PNG,
        })

    # ------------------------------------------------------------------
    # The doctor scope
    # ------------------------------------------------------------------
    def test_doctor_reads_an_image_of_a_result_naming_them(self):
        image = self._image(
            result_physician=self.doctor_a,
            request_physician=self.doctor_b,
            visit_doctor=self.doctor_b,
        )
        self.assertEqual(
            image.with_user(self.user_a).read(["name"])[0]["name"], image.name
        )

    def test_doctor_reads_an_image_of_a_request_they_ordered(self):
        image = self._image(
            result_physician=self.doctor_b,
            request_physician=self.doctor_a,
            visit_doctor=self.doctor_b,
        )
        self.assertEqual(
            image.with_user(self.user_a).read(["name"])[0]["name"], image.name
        )

    def test_doctor_reads_an_image_of_a_visit_assigned_to_them(self):
        """The covering-colleague branch: the report names Doctor B twice, and
        Doctor A reaches it only through the visit they hold."""
        image = self._image(
            result_physician=self.doctor_b,
            request_physician=self.doctor_b,
            visit_doctor=self.doctor_a,
        )
        self.assertEqual(
            image.with_user(self.user_a).read(["name"])[0]["name"], image.name
        )

    def test_doctor_CANNOT_read_another_doctors_image(self):
        """The defect this rule exists to prevent."""
        image = self._image(self.doctor_b)
        with self.assertRaises(AccessError):
            image.with_user(self.user_a).read(["name"])

    def test_another_doctors_image_is_absent_from_search(self):
        """Invisible, not merely unreadable. A search returning the id would
        leak that another patient's study exists."""
        mine = self._image(self.doctor_a)
        theirs = self._image(self.doctor_b)
        visible = self.env["hospital.radiology.image"].with_user(self.user_a).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible)

    def test_doctor_CANNOT_read_the_BYTES_of_another_doctors_image(self):
        """THE assertion the whole slice turns on.

        Reading `file` resolves through ir.attachment, whose check() browses
        res_model/res_id and applies this model's record rule. If the rule were
        wrong, the metadata test above would fail -- but so would this, and
        this is the one that means a clinical image leaked.
        """
        theirs = self._image(self.doctor_b)
        with self.assertRaises(AccessError):
            theirs.with_user(self.user_a).read(["file"])

    def test_doctor_reads_the_bytes_of_their_OWN_image(self):
        mine = self._image(self.doctor_a)
        content = mine.with_user(self.user_a).read(["file"])[0]["file"]
        self.assertTrue(content, "the doctor could not read their own image")

    # ------------------------------------------------------------------
    # Visibility is not authorship
    # ------------------------------------------------------------------
    def test_doctor_cannot_write_an_image(self):
        mine = self._image(self.doctor_a)
        with self.assertRaises(AccessError):
            mine.with_user(self.user_a).write({"caption": "not mine to write"})

    def test_doctor_cannot_create_an_image(self):
        mine = self._image(self.doctor_a)
        with self.assertRaises(AccessError):
            self.env["hospital.radiology.image"].with_user(self.user_a).create({
                "result_id": mine.result_id.id,
                "name": "Doctor upload",
                "filename": "x.png",
                "file": PNG,
            })

    def test_doctor_cannot_unlink_an_image(self):
        mine = self._image(self.doctor_a)
        with self.assertRaises(AccessError):
            mine.with_user(self.user_a).unlink()

    # ------------------------------------------------------------------
    # The roles that must keep seeing everything
    # ------------------------------------------------------------------
    def test_the_imaging_bench_sees_every_image(self):
        a = self._image(self.doctor_a)
        b = self._image(self.doctor_b)
        visible = self.env["hospital.radiology.image"].with_user(self.bench).search([])
        self.assertIn(a, visible)
        self.assertIn(b, visible)

    def test_the_imaging_bench_can_still_do_its_job(self):
        image = self._image(self.doctor_b)
        image.with_user(self.bench).write({"caption": "bench note"})
        self.assertEqual(image.caption, "bench note")

    def test_manager_scope_is_not_capped_by_the_implied_doctor_group(self):
        """The specific regression the unrestricted rule exists to prevent."""
        self.assertTrue(
            self.manager.has_group("hospital_management.group_hospital_doctor"),
            "the premise of this test is that Manager implies Doctor",
        )
        theirs = self._image(self.doctor_b)
        self.assertEqual(
            theirs.with_user(self.manager).read(["name"])[0]["name"], theirs.name
        )

    def test_sysadmin_scope_is_not_capped_either(self):
        """Sysadmin implies manager implies doctor: two hops, same trap."""
        self.assertTrue(
            self.sysadmin.has_group("hospital_management.group_hospital_doctor"),
        )
        theirs = self._image(self.doctor_b)
        self.assertEqual(
            theirs.with_user(self.sysadmin).read(["name"])[0]["name"], theirs.name
        )

    # ------------------------------------------------------------------
    # Nothing else moved
    # ------------------------------------------------------------------
    def test_the_doctor_ACL_on_the_image_model_is_read_only(self):
        """The rule narrows WHICH rows; the ACL decides whether the operation
        exists at all. Without this, the refusals above could start passing for
        the wrong reason."""
        doctor_group = self.env.ref("hospital_management.group_hospital_doctor")
        access = self.env["ir.model.access"].sudo().search([
            ("model_id.model", "=", "hospital.radiology.image"),
            ("group_id", "=", doctor_group.id),
        ])
        self.assertTrue(access, "no doctor ACL row for hospital.radiology.image")
        self.assertTrue(all(access.mapped("perm_read")))
        self.assertFalse(any(access.mapped("perm_write")))
        self.assertFalse(any(access.mapped("perm_create")))
        self.assertFalse(any(access.mapped("perm_unlink")))

    def test_radiology_result_and_line_scope_is_unchanged(self):
        """Slice 8A added a model; it must not have moved the rules the report
        and its lines already had."""
        mine = self._image(self.doctor_a)
        theirs = self._image(self.doctor_b)
        Result = self.env["hospital.radiology.result"].with_user(self.user_a)
        visible = Result.search([])
        self.assertIn(mine.result_id, visible)
        self.assertNotIn(theirs.result_id, visible)

"""Slice 8A: the freeze has to hold on the BYTES, not only on the record.

`hospital.radiology.image.file` is Binary(attachment=True), so the clinical
evidence lives in an ir.attachment row the image model does not mediate.
Freezing the child model alone leaves that row reachable: ir.attachment.check()
resolves write access by calling check_access('write') on the OWNING record, and
the imaging bench legitimately holds write on hospital.radiology.image -- so the
image model's own freeze is never consulted, and a signed report's evidence
could be replaced or deleted underneath it.

Every test below operates DIRECTLY on ir.attachment, as the imaging bench, after
the report is validated and again after it is released. They all ask one
question: can the evidence behind a signed report still be changed?

WHAT THE GUARD MUST NOT DO is as important as what it must. Odoo deletes a
record's row BEFORE cleaning up its field attachments (models.py unlink), so a
legitimate cascade reaches the attachment with its parent already gone; and
Binary.write() updates the backing row through sudo whenever the image model
accepts a new file. Both paths are exercised here, so a guard that froze the
bytes by breaking ordinary uploads would fail these tests rather than ship.
"""
import base64
import uuid

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged

from .test_radiology_image import JPEG, PDF, PNG


@tagged("post_install", "-at_install", "radiology_image", "radiology_image_attachment")
class TestRadiologyImageBackingAttachment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]
        cls.bench = cls._make_user(
            "radimg_bench", "hospital_management.group_hospital_lab_technician"
        )
        cls.doctor_user = cls._make_user(
            "radimg_doc", "hospital_management.group_hospital_doctor"
        )
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Imaging Doctor %s" % tag, "user_id": cls.doctor_user.id}
        )
        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Imaging Patient %s" % tag}
        )
        cls.exam = cls.env["hospital.radiology.exam"].sudo().create({
            "name": "Attachment Exam %s" % tag,
            "code": "AEX%s" % tag.upper(),
            "modality": "ct",
        })

    @classmethod
    def _make_user(cls, login, group_xmlid):
        suffix = uuid.uuid4().hex[:6]
        return cls.env["res.users"].sudo().create({
            "name": login,
            "login": "%s_%s@example.test" % (login, suffix),
            "company_id": cls.env.company.id,
            "company_ids": [(6, 0, cls.env.company.ids)],
            "groups_id": [(6, 0, [
                cls.env.ref("base.group_user").id,
                cls.env.ref(group_xmlid).id,
            ])],
        })

    def _image_in(self, state):
        """An image on a result advanced to `state`, plus its backing row."""
        request = self.env["hospital.radiology.request"].sudo().create({
            "patient_id": self.patient.id,
            "physician_id": self.doctor.id,
            "line_ids": [(0, 0, {"exam_id": self.exam.id})],
        })
        result = self.env["hospital.radiology.result"].sudo().create({
            "request_id": request.id,
            "patient_id": self.patient.id,
            "physician_id": self.doctor.id,
        })
        image = self.env["hospital.radiology.image"].sudo().create({
            "result_id": result.id,
            "name": "Signed study",
            "filename": "study.png",
            "file": PNG,
        })
        if state != "draft":
            for step in ("entered", "validated", "released"):
                if state == "cancelled" and step == "validated":
                    result.sudo().write({"state": "cancelled"})
                    break
                result.sudo().write({"state": step})
                if step == state:
                    break
        self.assertEqual(result.state, state)
        attachment = image._backing_attachments()
        self.assertEqual(len(attachment), 1)
        return result, image, attachment

    # ------------------------------------------------------------------
    # The intended workflow still works before validation
    # ------------------------------------------------------------------
    def test_the_bytes_can_still_be_replaced_while_draft(self):
        _result, image, attachment = self._image_in("draft")
        image.sudo().write({"file": JPEG, "filename": "study.jpg"})
        self.assertEqual(image.mimetype, "image/jpeg")
        self.assertTrue(attachment.exists())

    def test_the_bytes_can_still_be_replaced_while_entered(self):
        _result, image, _attachment = self._image_in("entered")
        image.sudo().write({"file": PDF, "filename": "study.pdf"})
        self.assertEqual(image.mimetype, "application/pdf")

    def test_an_image_can_still_be_removed_while_entered(self):
        """The cascade path the guard must not break.

        Odoo deletes the image row first and cleans its field attachments
        afterwards, so the attachment is reached with its parent already gone.
        A guard that refused when it could not resolve the parent would make
        every legitimate deletion fail.
        """
        _result, image, attachment = self._image_in("entered")
        attachment_id = attachment.id
        image.sudo().unlink()
        self.assertFalse(image.exists())
        self.assertFalse(
            self.env["ir.attachment"].sudo().browse(attachment_id).exists(),
            "the backing attachment outlived its image",
        )

    # ------------------------------------------------------------------
    # The bytes are frozen from validated onward
    # ------------------------------------------------------------------
    def _assert_bytes_frozen(self, state):
        _result, image, attachment = self._image_in(state)
        original = attachment.datas

        as_bench = attachment.with_user(self.bench)
        with self.assertRaises(UserError, msg="datas replaced in %s" % state):
            as_bench.write({"datas": JPEG})
        with self.assertRaises(UserError, msg="renamed in %s" % state):
            as_bench.write({"name": "swapped.png"})
        with self.assertRaises(UserError, msg="made public in %s" % state):
            as_bench.write({"public": True})
        with self.assertRaises(UserError, msg="tokenised in %s" % state):
            as_bench.write({"access_token": "forged-token"})
        with self.assertRaises(UserError, msg="unlinked in %s" % state):
            as_bench.unlink()

        attachment.invalidate_recordset()
        self.assertTrue(attachment.exists(), "the evidence was deleted in %s" % state)
        self.assertEqual(
            attachment.datas, original, "the evidence was replaced in %s" % state
        )
        self.assertFalse(attachment.public)
        self.assertFalse(attachment.access_token)

    def test_the_bytes_are_frozen_once_the_result_is_VALIDATED(self):
        self._assert_bytes_frozen("validated")

    def test_the_bytes_are_frozen_once_the_result_is_RELEASED(self):
        self._assert_bytes_frozen("released")

    def test_the_bytes_are_frozen_on_a_cancelled_result(self):
        self._assert_bytes_frozen("cancelled")

    def test_even_sudo_cannot_replace_the_bytes_of_a_signed_report(self):
        """A model override, not a record rule, which is why sudo does not lift
        it: this is a statement about what the record permits, not about who is
        acting."""
        _result, _image, attachment = self._image_in("released")
        with self.assertRaises(UserError):
            attachment.sudo().write({"datas": JPEG})
        with self.assertRaises(UserError):
            attachment.sudo().unlink()

    def test_the_image_record_and_its_bytes_freeze_together(self):
        """Neither guard is load-bearing alone; this pins that both are live."""
        _result, image, attachment = self._image_in("released")
        with self.assertRaises(UserError):
            image.sudo().write({"caption": "changed"})
        with self.assertRaises(UserError):
            attachment.sudo().write({"datas": JPEG})

    # ------------------------------------------------------------------
    # Nothing else in the database is affected
    # ------------------------------------------------------------------
    def test_an_unrelated_attachment_is_completely_unaffected(self):
        """The guard is scoped by res_model AND res_field. Every other
        attachment keeps behaving exactly as Odoo shipped it, public flag and
        access token included."""
        unrelated = self.env["ir.attachment"].sudo().create({
            "name": "unrelated.txt",
            "datas": base64.b64encode(b"not a radiology image"),
        })
        unrelated.sudo().write({"name": "renamed.txt"})
        unrelated.sudo().write({"public": True})
        self.assertTrue(unrelated.public)
        unrelated.sudo().write({"access_token": "fine-here"})
        self.assertEqual(unrelated.access_token, "fine-here")
        unrelated.sudo().unlink()
        self.assertFalse(unrelated.exists())

    def test_an_attachment_on_another_clinical_model_is_unaffected(self):
        document = self.env["hospital.patient.document"].sudo().create({
            "name": "Referral",
            "patient_id": self.patient.id,
            "attachment": PDF,
            "filename": "referral.pdf",
        })
        backing = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "hospital.patient.document"),
            ("res_field", "=", "attachment"),
            ("res_id", "=", document.id),
        ])
        self.assertTrue(backing, "the patient document has no backing attachment")
        backing.sudo().write({"name": "still editable"})
        self.assertEqual(backing[0].name, "still editable")

    def test_a_non_file_attachment_on_the_image_model_is_not_guarded(self):
        """Scoped on res_field too, so a chatter-style attachment on the same
        record would not be frozen by a rule written for the evidence field."""
        _result, image, _attachment = self._image_in("released")
        sidecar = self.env["ir.attachment"].sudo().create({
            "name": "sidecar.txt",
            "res_model": "hospital.radiology.image",
            "res_id": image.id,
            "datas": base64.b64encode(b"not the evidence"),
        })
        sidecar.sudo().write({"name": "renamed sidecar"})
        self.assertEqual(sidecar.name, "renamed sidecar")
        sidecar.sudo().unlink()

    # ------------------------------------------------------------------
    # The doctor route, and the standing guarantees
    # ------------------------------------------------------------------
    def test_a_doctor_cannot_exploit_direct_attachment_access_either(self):
        _result, _image, attachment = self._image_in("released")
        as_doctor = attachment.with_user(self.doctor_user)
        # UserError covers BOTH refusals: AccessError subclasses it, and the
        # doctor may be stopped either by holding no write on the owning record
        # or by the freeze. Which one fires is not the point -- what must not
        # happen is a successful write. (Odoo's assertRaises takes one class,
        # not a tuple.)
        with self.assertRaises(UserError):
            as_doctor.write({"datas": JPEG})
        with self.assertRaises(UserError):
            as_doctor.unlink()

    def test_a_radiology_image_attachment_is_never_public_or_tokenised(self):
        """Refused at EVERY state, not only the frozen ones: a public clinical
        image is wrong while the report is still being written too."""
        for state in ("draft", "entered"):
            _result, _image, attachment = self._image_in(state)
            with self.assertRaises(UserError, msg="public in %s" % state):
                attachment.sudo().write({"public": True})
            with self.assertRaises(UserError, msg="token in %s" % state):
                attachment.sudo().write({"access_token": "forged"})
            self.assertFalse(attachment.public)
            self.assertFalse(attachment.access_token)

    def test_clearing_public_and_token_is_always_allowed(self):
        """The repair path must stay open: _enforce_private_storage() writes
        public=False, and a guard that refused every write to those columns
        would block the very thing that keeps them safe."""
        _result, _image, attachment = self._image_in("draft")
        attachment.sudo().write({"public": False, "access_token": False})
        self.assertFalse(attachment.public)

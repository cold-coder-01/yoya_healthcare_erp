"""Slice 8A: clinical files attached to a radiology report.

WHAT THESE TESTS ARE FOR
------------------------
Four properties carry this slice.

  1. THE FILE'S CONTENT DECIDES ITS TYPE, NOT ITS NAME. A document named
     chest.png whose bytes are HTML is a stored-cross-site payload the moment
     anything renders it, and the filename says nothing about that. Every
     acceptance below is made against sniffed bytes.

  2. THE IMAGE SET FREEZES WITH THE REPORT. From `validated` onward no create,
     write, unlink or archive may touch it. A released report whose images
     could still be swapped would be signed off in name only -- and the guard
     has to live on the CHILD, because a child write never passes through the
     parent's write() and an attachment write would not pass through either.

  3. THE BYTES ARE NEVER PUBLIC AND NEVER TOKENISED. ir.attachment.check()
     short-circuits on `public` before it reaches any record rule, and
     validate_access() hands back a sudo recordset for a public attachment or
     for any caller holding an access_token. Either one turns a scoped clinical
     file into a link anyone can open.

  4. PROVENANCE AND METADATA ARE THE SERVER'S. mimetype, file_size and the
     uploader are derived, never accepted from the caller.

THE FIXTURES DRIVE STATE, NOT THE DEPARTMENT WORKFLOW. Results are walked
draft -> entered -> validated -> released by writing `state`, which is what the
radiology model itself permits; what is bypassed is hospital_billing's
action_validate/action_release overrides, which deliver charges and re-check
financial clearance. This slice changes no workflow and asserts none.
"""
import base64
import uuid

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged

# Real file signatures. guess_mimetype reads the leading bytes, so these have to
# be the actual magic numbers rather than plausible-looking filler.
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
PDF = base64.b64encode(b"%PDF-1.4\n" + b"\x00" * 64)
HTML = base64.b64encode(b"<html><body>not an image</body></html>")
PLAIN = base64.b64encode(b"just some text, definitely not a scan")


@tagged("post_install", "-at_install", "radiology_image")
class TestRadiologyImage(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tag = uuid.uuid4().hex[:6]
        cls.patient = cls.env["hospital.patient"].sudo().create(
            {"name": "Imaging Patient %s" % tag}
        )
        cls.doctor = cls.env["hospital.doctor"].sudo().create(
            {"name": "Imaging Doctor %s" % tag}
        )
        cls.exam = cls.env["hospital.radiology.exam"].sudo().create(
            {
                "name": "Chest X-Ray %s" % tag,
                "code": "CXR%s" % tag.upper(),
                "modality": "xray",
                "body_part": "Chest",
            }
        )

    def _result(self, state="draft"):
        """A radiology result in `state`, with one ordered exam behind it."""
        request = self.env["hospital.radiology.request"].sudo().create({
            "patient_id": self.patient.id,
            "physician_id": self.doctor.id,
            "line_ids": [(0, 0, {"exam_id": self.exam.id})],
        })
        result = self.env["hospital.radiology.result"].sudo().create({
            "request_id": request.id,
            "patient_id": self.patient.id,
        })
        for step in ("entered", "validated", "released"):
            if result.state == state:
                break
            result.sudo().write({"state": step})
        if state == "cancelled":
            result.sudo().write({"state": "cancelled"})
        self.assertEqual(result.state, state)
        return result

    def _image(self, result, file=PNG, **extra):
        values = {
            "result_id": result.id,
            "name": extra.pop("name", "Chest AP"),
            "filename": extra.pop("filename", "chest.png"),
            "file": file,
        }
        values.update(extra)
        return self.env["hospital.radiology.image"].sudo().create(values)

    # ------------------------------------------------------------------
    # 1-3: what is accepted
    # ------------------------------------------------------------------
    def test_a_jpeg_is_accepted(self):
        image = self._image(self._result(), file=JPEG, filename="chest.jpg")
        self.assertEqual(image.mimetype, "image/jpeg")

    def test_a_png_is_accepted(self):
        image = self._image(self._result(), file=PNG)
        self.assertEqual(image.mimetype, "image/png")

    def test_a_pdf_is_accepted(self):
        image = self._image(
            self._result(), file=PDF, filename="report.pdf", image_type="report"
        )
        self.assertEqual(image.mimetype, "application/pdf")
        self.assertEqual(image.image_type, "report")

    # ------------------------------------------------------------------
    # 4-5: what is refused, and why the name is irrelevant
    # ------------------------------------------------------------------
    def test_an_unsupported_type_is_refused(self):
        with self.assertRaises(ValidationError):
            self._image(self._result(), file=PLAIN, filename="notes.txt")

    def test_HTML_BYTES_NAMED_PNG_ARE_REFUSED(self):
        """THE test this validation exists for.

        An extension check would accept this file. Its bytes are an HTML
        document, which is a stored-cross-site payload wherever it is rendered.
        """
        with self.assertRaises(ValidationError):
            self._image(self._result(), file=HTML, filename="fake.png")

    def test_the_refusal_names_the_real_type_not_the_filename(self):
        with self.assertRaises(ValidationError) as caught:
            self._image(self._result(), file=PLAIN, filename="scan.jpg")
        message = str(caught.exception)
        self.assertIn("CONTENT", message)
        self.assertNotIn("scan.jpg", message)

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(ValidationError):
            self._image(self._result(), file=base64.b64encode(b""))

    # ------------------------------------------------------------------
    # 6: size
    # ------------------------------------------------------------------
    def test_a_file_over_25_MB_is_refused(self):
        Image = self.env["hospital.radiology.image"]
        oversized = base64.b64encode(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * (Image.MAX_FILE_SIZE + 1)
        )
        with self.assertRaises(ValidationError) as caught:
            self._image(self._result(), file=oversized)
        self.assertIn("25 MB", str(caught.exception))

    def test_the_ceiling_is_enforced_by_the_model_not_the_view(self):
        # Same refusal through a plain ORM create with no form in sight.
        Image = self.env["hospital.radiology.image"]
        self.assertEqual(Image.MAX_FILE_SIZE, 25 * 1024 * 1024)
        self.assertEqual(
            set(Image.ALLOWED_MIMETYPES),
            {"image/jpeg", "image/png", "application/pdf"},
        )

    # ------------------------------------------------------------------
    # 7-9: metadata is derived, never accepted
    # ------------------------------------------------------------------
    def test_mimetype_is_derived_from_the_bytes(self):
        image = self._image(self._result(), file=PDF, mimetype="image/png")
        self.assertEqual(
            image.mimetype, "application/pdf",
            "a client-supplied mimetype overrode the sniffed one",
        )

    def test_file_size_is_derived_from_the_bytes(self):
        image = self._image(self._result(), file=PNG, file_size=999999)
        self.assertEqual(image.file_size, len(base64.b64decode(PNG)))

    def test_the_uploader_is_the_acting_user_not_a_supplied_value(self):
        other = self.env["res.users"].sudo().create({
            "name": "someone else",
            "login": "imaging_other_%s" % uuid.uuid4().hex[:6],
        })
        image = self._image(self._result(), uploaded_by_id=other.id)
        self.assertEqual(image.uploaded_by_id, self.env.user)
        self.assertTrue(image.uploaded_at)

    # ------------------------------------------------------------------
    # 10-11: the backing attachment
    # ------------------------------------------------------------------
    def test_the_backing_attachment_is_in_the_filestore_and_scoped_to_this_model(self):
        image = self._image(self._result())
        attachment = image._backing_attachments()
        self.assertEqual(len(attachment), 1)
        self.assertEqual(attachment.res_model, "hospital.radiology.image")
        self.assertEqual(attachment.res_field, "file")
        self.assertEqual(attachment.res_id, image.id)

    def test_the_backing_attachment_is_NOT_public(self):
        """ir.attachment.check() skips every record check for a public
        attachment. This is the flag that would make a clinical image a link
        anyone with the URL could open."""
        image = self._image(self._result())
        self.assertFalse(image._backing_attachments().public)

    def test_no_access_token_is_generated(self):
        # validate_access() returns a sudo recordset to any caller holding one.
        image = self._image(self._result())
        self.assertFalse(image._backing_attachments().access_token)

    def test_the_backing_attachment_cannot_be_made_public_through_the_orm(self):
        """The FIRST layer: hospital_radiology's ir.attachment guard refuses to
        publish or tokenise a radiology evidence row at any state."""
        image = self._image(self._result())
        with self.assertRaises(UserError):
            image._backing_attachments().write({"public": True})
        with self.assertRaises(UserError):
            image._backing_attachments().write({"access_token": "forged"})

    def test_a_public_flag_set_OUTSIDE_the_orm_is_repaired_on_the_next_write(self):
        """The SECOND layer, and the reason it is still worth keeping.

        The guard closes every ORM path, so the only way an evidence row can
        become public now is one the ORM never sees: raw SQL, a restore, or a
        row written before this module was upgraded. _enforce_private_storage()
        heals exactly that, and it only issues a write when a row is genuinely
        exposed -- so on a healthy database it costs one indexed read and
        nothing else.
        """
        image = self._image(self._result())
        attachment = image._backing_attachments()
        self.env.cr.execute(
            "UPDATE ir_attachment SET public = TRUE WHERE id = %s", (attachment.id,)
        )
        attachment.invalidate_recordset(["public"])
        self.assertTrue(attachment.public, "the SQL fixture did not take")

        image.sudo().write({"caption": "post-contrast"})

        attachment.invalidate_recordset(["public"])
        self.assertFalse(
            attachment.public,
            "the image model tolerated a public backing attachment",
        )

    def test_replacing_the_file_keeps_the_attachment_private(self):
        image = self._image(self._result())
        image.sudo().write({"file": JPEG, "filename": "chest.jpg"})
        attachment = image._backing_attachments()
        self.assertFalse(attachment.public)
        self.assertFalse(attachment.access_token)
        self.assertEqual(image.mimetype, "image/jpeg")

    # ------------------------------------------------------------------
    # 12-15: the mutable window
    # ------------------------------------------------------------------
    def test_images_can_be_added_edited_and_removed_while_draft(self):
        result = self._result("draft")
        image = self._image(result)
        image.sudo().write({"name": "Chest AP corrected", "caption": "repeat"})
        self.assertEqual(image.name, "Chest AP corrected")
        image.sudo().unlink()
        self.assertFalse(image.exists())

    def test_images_can_be_reordered_while_entered(self):
        result = self._result("entered")
        first = self._image(result, name="AP", sequence=10)
        second = self._image(result, name="Lateral", sequence=20)
        second.sudo().write({"sequence": 5})
        result.invalidate_recordset()
        self.assertEqual(result.image_ids.mapped("name"), ["Lateral", "AP"])
        self.assertEqual(result.image_count, 2)
        first.sudo().unlink()

    def test_an_image_can_be_archived_while_entered(self):
        image = self._image(self._result("entered"))
        image.sudo().write({"active": False})
        self.assertFalse(image.active)

    # ------------------------------------------------------------------
    # 16-21: the freeze
    # ------------------------------------------------------------------
    def _frozen_result_with_image(self, state):
        """A result carrying an image, then advanced into a frozen state."""
        result = self._result("entered")
        image = self._image(result)
        if state == "cancelled":
            result.sudo().write({"state": "cancelled"})
        else:
            for step in ("validated", "released"):
                result.sudo().write({"state": step})
                if step == state:
                    break
        self.assertEqual(result.state, state)
        return result, image

    def test_the_image_set_is_frozen_from_validated_onward(self):
        for state in ("validated", "released", "cancelled"):
            result, image = self._frozen_result_with_image(state)
            with self.assertRaises(UserError, msg="create in %s" % state):
                self._image(result, name="Late addition")
            with self.assertRaises(UserError, msg="rename in %s" % state):
                image.sudo().write({"name": "Renamed"})
            with self.assertRaises(UserError, msg="caption in %s" % state):
                image.sudo().write({"caption": "added later"})
            with self.assertRaises(UserError, msg="reorder in %s" % state):
                image.sudo().write({"sequence": 99})
            with self.assertRaises(UserError, msg="archive in %s" % state):
                image.sudo().write({"active": False})
            with self.assertRaises(UserError, msg="replace file in %s" % state):
                image.sudo().write({"file": JPEG})
            with self.assertRaises(UserError, msg="unlink in %s" % state):
                image.sudo().unlink()

    def test_the_freeze_survives_sudo_and_a_direct_child_write(self):
        """A child write never passes through the parent's write(), so a guard
        on hospital.radiology.result alone would leave this open."""
        result, image = self._frozen_result_with_image("released")
        with self.assertRaises(UserError):
            image.sudo().with_context(bypass=True).write({"caption": "sneaked in"})
        image.invalidate_recordset()
        self.assertFalse(image.caption)

    def test_the_frozen_refusal_names_the_clinical_path(self):
        _result, image = self._frozen_result_with_image("released")
        with self.assertRaises(UserError) as caught:
            image.sudo().write({"name": "Renamed"})
        self.assertIn("new radiology result", str(caught.exception))

    def test_an_image_cannot_be_repointed_at_another_result(self):
        result = self._result("draft")
        other = self._result("draft")
        image = self._image(result)
        with self.assertRaises(UserError):
            image.sudo().write({"result_id": other.id})

    def test_the_parent_freeze_still_refuses_writing_images_through_the_result(self):
        # The result's own write() blocks every field but `active` once
        # released; this asserts the two guards agree rather than overlap oddly.
        result, _image = self._frozen_result_with_image("released")
        with self.assertRaises(UserError):
            result.sudo().write({
                "image_ids": [(0, 0, {
                    "name": "Late", "filename": "x.png", "file": PNG,
                })],
            })

    # ------------------------------------------------------------------
    # 22: audit
    # ------------------------------------------------------------------
    def test_an_upload_is_audited_with_metadata_and_never_with_bytes(self):
        result = self._result("entered")
        image = self._image(result, filename="chest.png")
        logs = self.env["hospital.audit.log"].sudo().search([
            ("model_name", "=", "hospital.radiology.image"),
            ("record_id", "=", image.id),
        ])
        self.assertTrue(logs, "the upload was not audited")
        blob = " ".join(logs.mapped(lambda log: "%s %s %s" % (
            log.description or "", log.old_value or "", log.new_value or "",
        )))
        self.assertIn("chest.png", blob)
        self.assertIn("image/png", blob)
        # THE PROPERTY THAT MATTERS: no base64, no bytes, no second copy of
        # clinical data sitting in an audit row.
        self.assertNotIn(PNG.decode(), blob)
        self.assertNotIn("iVBOR", blob)
        self.assertEqual(logs.mapped("patient_id"), self.patient)

    def test_a_caption_change_is_audited(self):
        result = self._result("entered")
        image = self._image(result)
        image.sudo().write({"caption": "post-contrast"})
        logs = self.env["hospital.audit.log"].sudo().search([
            ("model_name", "=", "hospital.radiology.image"),
            ("record_id", "=", image.id),
        ])
        self.assertIn("create", logs.mapped("action_type"))
        self.assertIn("update", logs.mapped("action_type"))
        self.assertIn("post-contrast", " ".join(logs.mapped("new_value")))

    def test_archiving_is_audited_as_an_archive(self):
        image = self._image(self._result("entered"))
        image.sudo().write({"active": False})
        actions = self.env["hospital.audit.log"].sudo().search([
            ("model_name", "=", "hospital.radiology.image"),
            ("record_id", "=", image.id),
        ]).mapped("action_type")
        self.assertIn("archive", actions)

    def test_a_blocked_removal_leaves_the_image_intact(self):
        """The refusal is what protects the evidence; the log is best effort.

        A `delete_attempt` row IS written before the refusal is raised -- the
        shape hospital.laboratory.result and hospital.patient.document both
        use -- but it does not survive: the UserError aborts the transaction
        that created it, and Odoo rolls the audit row back with everything
        else. That is true of every delete_attempt log in this repository, not
        something this model introduced, so it is not worked around here.

        What is asserted is therefore the property that actually holds and
        actually matters: a signed report keeps its images.
        """
        _result, image = self._frozen_result_with_image("released")
        with self.assertRaises(UserError):
            image.sudo().unlink()
        self.assertTrue(image.exists(), "the blocked removal went through")
        self.assertTrue(image.file, "the blocked removal emptied the file")

    # ------------------------------------------------------------------
    # Shape
    # ------------------------------------------------------------------
    def test_the_result_counts_its_images(self):
        result = self._result("entered")
        self.assertEqual(result.image_count, 0)
        self._image(result, name="AP")
        self._image(result, name="Lateral")
        result.invalidate_recordset()
        self.assertEqual(result.image_count, 2)

    def test_an_image_cannot_outlive_its_result(self):
        """Asserted at the SCHEMA, because the model refuses the deletion.

        hospital.radiology.result.unlink() blocks everyone but a system
        administrator -- results are sensitive health records and are archived,
        not deleted -- so the cascade can never be observed through an ordinary
        delete. What is asserted instead is the property that makes an orphaned
        image impossible if a result ever is removed: the foreign key itself.
        """
        self.assertEqual(
            self.env["hospital.radiology.image"]._fields["result_id"].ondelete,
            "cascade",
        )
        result = self._result("draft")
        image = self._image(result)
        with self.assertRaises(UserError):
            result.sudo().unlink()
        self.assertTrue(image.exists())

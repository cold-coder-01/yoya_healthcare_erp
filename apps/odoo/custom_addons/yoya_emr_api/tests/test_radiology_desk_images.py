"""Radiology Desk Slice 4: image upload, byte serving and removal.

WHAT THESE TESTS ARE FOR
------------------------
  1. WHO. Every desk role -- Technician, Radiologist, Manager, System
     Administrator -- uploads, views and removes; every other role is refused
     before a record is touched.
  2. THE BYTES DECIDE. JPEG, PNG and PDF by content, 25 MB, nothing empty. The
     browser's name, type and size are not trusted; a spoofed file is refused.
  3. ONLY WHILE OPEN. Draft and entered reports accept images; validated,
     released and cancelled refuse them, with no bypass.
  4. PRIVATE BYTES. The desk streams them through its own route, scoped to the
     report, never cached, never through a token or a public attachment.
  5. ALL OR NOTHING. A failed confirmation leaves no image, no attachment and no
     audit row -- and undoes a removal.
  6. THE DOCTOR STILL SEES NOTHING UNTIL RELEASE -- no metadata, no bytes.
"""
import base64
import json
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import tagged

from .test_radiology_desk_report import RESULT, RadReportCase

IMAGES = "/yoya-emr/api/v1/radiology/results/%s/images"
IMAGE = IMAGES + "/%s"
REMOVE = IMAGE + "/remove"
DOCTOR_IMAGE = "/yoya-emr/api/v1/doctor/visits/%s/results/images/%s"
CONTROLLER = "odoo.addons.yoya_emr_api.controllers.radiology"
IMAGE_MODEL = "hospital.radiology.image"

# Real file signatures: guess_mimetype reads the leading bytes.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64
TEXT = b"just some text, definitely not a scan"
HTML = b"<html><body><script>alert(1)</script></body></html>"


class RadImageCase(RadReportCase):
    """RadReportCase plus multipart uploads and reports in every state."""

    def _upload(self, report, content=PNG, filename="axial.png", user=None,
                password=None, data=None, content_type="image/png", files=None):
        self._auth(user or self.rad_tech, password or self.tech_password)
        response = self.url_open(
            IMAGES % report.id,
            data=data or {},
            files=files if files is not None else {"file": (filename, content, content_type)},
            timeout=60,
        )
        report.invalidate_recordset()
        return response, json.loads(response.text)

    def _uploaded(self, report, content=PNG, filename="axial.png", **kw):
        response, payload = self._upload(report, content, filename, **kw)
        self.assertEqual(response.status_code, 200, payload)
        return self.env[IMAGE_MODEL].sudo().browse(payload["data"]["image"]["id"]), payload

    def _get_bytes(self, report, image_id, user=None, password=None, query=""):
        self._auth(user or self.rad_tech, password or self.tech_password)
        return self.url_open((IMAGE % (report.id, image_id)) + query)

    def _remove(self, report, image_id, user=None, password=None):
        self._auth(user or self.rad_tech, password or self.tech_password)
        response = self.url_open(
            REMOVE % (report.id, image_id), data="{}",
            headers={"Content-Type": "application/json"},
        )
        report.invalidate_recordset()
        return response, json.loads(response.text)

    def _draft(self):
        record, _patient, appointment, _e = self._in_progress()
        report, _d = self._opened(record)
        return record, report, appointment

    def _report_in(self, state):
        """A real report, moved by the real workflow into `state`."""
        record, _patient, appointment, _e = self._in_progress()
        report, _d = self._opened(record)
        if state == "cancelled":
            report.with_user(self.radiologist).action_cancel()
        elif state != "draft":
            self._enter(report, {"impression": "Normal study."})
            if state in ("validated", "released"):
                report.with_user(self.radiologist).action_validate()
            if state == "released":
                report.with_user(self.radiologist).action_release()
        report.invalidate_recordset()
        self.assertEqual(report.state, state)
        return record, report, appointment

    def _images(self, report):
        return self.env[IMAGE_MODEL].sudo().with_context(active_test=False).search(
            [("result_id", "=", report.id)]
        )

    def _attachments(self, images=None):
        domain = [("res_model", "=", IMAGE_MODEL), ("res_field", "=", "file")]
        if images is not None:
            domain.append(("res_id", "in", images.ids))
        return self.env["ir.attachment"].sudo().search(domain)

    def _image_audit_count(self):
        return self.env["hospital.audit.log"].sudo().search_count(
            [("model_name", "=", IMAGE_MODEL)]
        )


# ===========================================================================
# 1-10 Authorization
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyImageAuthorization(RadImageCase):

    def test_01_to_04_every_desk_role_uploads_views_and_removes(self):
        for user, password in (
            (self.rad_tech, self.tech_password),
            (self.radiologist, self.radiologist_password),
            (self.manager, self.manager_password),
            (self.sysadmin, self.sysadmin_password),
        ):
            _record, report, _a = self._draft()
            image, payload = self._uploaded(report, user=user, password=password)
            self.assertEqual(image.uploaded_by_id, user, user.login)
            self.assertTrue(payload["data"]["capabilities"]["manage_images"])
            response = self._get_bytes(report, image.id, user, password)
            self.assertEqual(response.status_code, 200, user.login)
            response, _p = self._remove(report, image.id, user, password)
            self.assertEqual(response.status_code, 200, user.login)
            self.assertFalse(image.exists(), user.login)

    def test_05_to_09_other_roles_are_refused_and_nothing_is_written(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report)
        before = len(self._images(report))
        for user, password in (
            (self.lab_tech, self.lab_password),
            (self.doctor_user, self.doctor_password),
            (self.nurse, self.nurse_password),
            (self.receptionist, self.receptionist_password),
            (self.cashier, self.cashier_password),
        ):
            response, payload = self._upload(report, user=user, password=password)
            self.assertEqual(response.status_code, 403, user.login)
            self.assertEqual(payload["error"]["code"], "radiology_desk_not_authorized")
            response = self._get_bytes(report, image.id, user, password)
            self.assertEqual(response.status_code, 403, user.login)
            response, payload = self._remove(report, image.id, user, password)
            self.assertEqual(response.status_code, 403, user.login)
        self.assertEqual(len(self._images(report)), before)
        self.assertTrue(image.exists())

    def test_10_unauthenticated_never_reaches_the_endpoint(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report)
        self.authenticate(None, None)
        response = self.url_open(
            IMAGES % report.id, files={"file": ("a.png", PNG, "image/png")},
            allow_redirects=False,
        )
        self.assertIn(response.status_code, (301, 302, 303, 401, 403))
        response = self.url_open(IMAGE % (report.id, image.id), allow_redirects=False)
        self.assertIn(response.status_code, (301, 302, 303, 401, 403))
        self.assertNotEqual(response.content, PNG)
        self.assertEqual(len(self._images(report)), 1)


# ===========================================================================
# 11-26 Upload, and the files it refuses
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyImageUpload(RadImageCase):

    def test_11_to_13_jpeg_png_and_pdf_are_accepted(self):
        _record, report, _a = self._draft()
        for content, filename, mimetype, image_type in (
            (JPEG, "chest.jpg", "image/jpeg", "image"),
            (PNG, "axial.png", "image/png", "image"),
            (PDF, "report.pdf", "application/pdf", "report"),
        ):
            image, payload = self._uploaded(report, content, filename)
            self.assertEqual((image.mimetype, image.image_type), (mimetype, image_type))
            self.assertEqual(payload["data"]["image"]["mimetype"], mimetype)
        self.assertEqual(len(self._images(report)), 3)

    def test_14_to_20_the_server_decides_every_fact_about_the_file(self):
        _record, report, _a = self._draft()
        # The browser claims a different type; the bytes win.
        image, payload = self._uploaded(
            report, PNG, "C:\\fakepath\\axial.png",
            content_type="application/octet-stream",
        )
        meta = payload["data"]["image"]
        self.assertEqual(image.filename, "axial.png", "the client path is stripped")
        self.assertEqual(meta["filename"], "axial.png")
        self.assertEqual((image.mimetype, meta["mimetype"]), ("image/png", "image/png"))
        self.assertEqual((image.file_size, meta["file_size"]), (len(PNG), len(PNG)))
        self.assertEqual(image.uploaded_by_id, self.rad_tech)
        self.assertEqual(meta["uploaded_by"], self.rad_tech.name)
        self.assertTrue(image.uploaded_at and meta["uploaded_at"])
        self.assertEqual(image.result_id, report)
        self.assertEqual(meta["active"], True)
        attachment = self._attachments(image)
        self.assertEqual(len(attachment), 1)
        self.assertFalse(attachment.public)
        self.assertFalse(attachment.access_token)
        self.assertEqual(base64.b64decode(image.file), PNG)
        # The upload shows immediately in the report the response carries.
        self.assertEqual([i["id"] for i in payload["data"]["result"]["images"]], [image.id])

    def test_a_claimed_type_size_or_target_is_refused_not_trusted(self):
        _record, report, _a = self._draft()
        for field in ("mimetype", "file_size", "image_type", "result_id", "name"):
            response, payload = self._upload(report, data={field: "x"})
            self.assertEqual(response.status_code, 400, field)
            self.assertEqual(payload["error"]["code"], "radiology_image_field_not_allowed", field)
        self.assertFalse(self._images(report))

    def test_the_stored_name_is_sanitized(self):
        """Control characters, quotes, angle brackets and any client path are
        removed from the name a file is stored under. Pinned directly: the
        HTTP client percent-encodes CR/LF in a multipart filename."""
        from ..controllers.radiology import _safe_filename

        self.assertEqual(_safe_filename('C:\\fakepath\\ax"ial\r\n.png'), "axial.png")
        self.assertEqual(_safe_filename("/etc/<scan>.jpg"), "scan.jpg")
        self.assertEqual(_safe_filename("\t\r\n"), "radiology-file")
        self.assertEqual(_safe_filename(None), "radiology-file")
        self.assertEqual(len(_safe_filename("a" * 500 + ".png")), 200)

    def test_a_caption_is_kept_and_bounded(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report, data={"caption": "  Axial slice 12  "})
        self.assertEqual(image.caption, "Axial slice 12")
        response, payload = self._upload(report, data={"caption": "x" * 201})
        self.assertEqual((response.status_code, payload["error"]["code"]), (400, "invalid_field"))

    def test_21_to_24_spoofed_executable_text_and_unsupported_files_are_refused(self):
        _record, report, _a = self._draft()
        for content, filename, content_type in (
            (TEXT, "chest.jpg", "image/jpeg"),        # fake jpg
            (EXE, "setup.exe", "application/octet-stream"),
            (EXE, "chest.jpg", "image/jpeg"),        # executable renamed
            (TEXT, "axial.png", "image/png"),        # text renamed png
            (HTML, "axial.png", "image/png"),        # stored-XSS payload renamed
            (GIF, "axial.gif", "image/gif"),         # unsupported image format
        ):
            response, payload = self._upload(report, content, filename, content_type=content_type)
            self.assertEqual(response.status_code, 415, filename)
            self.assertEqual(payload["error"]["code"], "radiology_image_unsupported_type", filename)
        self.assertFalse(self._images(report))

    def test_25_an_empty_file_or_no_file_is_refused(self):
        _record, report, _a = self._draft()
        response, payload = self._upload(report, b"", "empty.png")
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (400, "radiology_image_invalid_file"))
        # A caption alone makes this a real multipart POST with no file in it
        # (url_open with no data and no files would send a GET).
        response, payload = self._upload(report, data={"caption": "no file"}, files={})
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (400, "radiology_image_invalid_file"))
        response, payload = self._upload(report, files={
            "file": ("a.png", PNG, "image/png"), "second": ("b.png", PNG, "image/png"),
        })
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (400, "radiology_image_invalid_file"))
        self.assertFalse(self._images(report))

    def test_26_a_file_over_25_mb_is_refused(self):
        _record, report, _a = self._draft()
        oversize = PNG + b"\x00" * (25 * 1024 * 1024)
        response, payload = self._upload(report, oversize, "huge.png")
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (413, "radiology_image_too_large"))
        self.assertFalse(self._images(report))

    def test_a_missing_or_hidden_report_is_404(self):
        response, payload = self._upload(self.env[RESULT].browse(99999999))
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (404, "radiology_result_not_found"))


# ===========================================================================
# 27-31 State policy
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyImageState(RadImageCase):

    def test_27_28_draft_and_entered_accept_images(self):
        for state in ("draft", "entered"):
            _record, report, _a = self._report_in(state)
            image, payload = self._uploaded(report)
            self.assertEqual(payload["data"]["result"]["state"], state)
            self.assertTrue(payload["data"]["result"]["images_mutable"], state)
            self.assertTrue(image.exists())

    def test_29_to_31_validated_released_and_cancelled_refuse_images(self):
        for state in ("validated", "released", "cancelled"):
            _record, report, _a = self._report_in(state)
            response, payload = self._upload(report)
            self.assertEqual(response.status_code, 409, state)
            self.assertEqual(payload["error"]["code"], "radiology_image_not_editable", state)
            self.assertFalse(self._images(report), state)


# ===========================================================================
# 32-36 Reading
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyImageBytes(RadImageCase):

    def test_32_the_desk_fetches_the_bytes_privately(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report, JPEG, "chest.jpg")
        response = self._get_bytes(report, image.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, JPEG)
        self.assertEqual(response.headers["Content-Type"].split(";")[0], "image/jpeg")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("inline", response.headers.get("Content-Disposition", ""))
        response = self._get_bytes(report, image.id, query="?disposition=attachment")
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
        self.assertNotIn("Set-Cookie", response.headers.get("Content-Disposition", ""))

    def test_the_desk_reads_a_signed_reports_images_too(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report)
        self._enter(report, {"impression": "Normal."})
        report.with_user(self.radiologist).action_validate()
        self.assertEqual(self._get_bytes(report, image.id).status_code, 200)

    def test_33_34_every_miss_is_the_same_404(self):
        _record, report, _a = self._draft()
        _record2, other, _a2 = self._draft()
        image, _p = self._uploaded(report)
        archived, _p = self._uploaded(report, JPEG, "old.jpg")
        archived.sudo().write({"active": False})
        for result_id, image_id in (
            (other.id, image.id),          # another report's pair
            (report.id, archived.id),      # archived image
            (report.id, 99999999),         # never existed
            (99999999, image.id),          # report never existed
        ):
            self._auth(self.rad_tech, self.tech_password)
            response = self.url_open(IMAGE % (result_id, image_id))
            self.assertEqual(response.status_code, 404, (result_id, image_id))
            self.assertEqual(json.loads(response.text)["error"]["code"], "radiology_image_not_found")
            self.assertNotEqual(response.content, PNG)

    def test_35_an_unauthorized_role_gets_403_not_bytes(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report)
        response = self._get_bytes(report, image.id, self.doctor_user, self.doctor_password)
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(response.content, PNG)

    def test_36_metadata_carries_no_token_url_or_attachment_handle(self):
        _record, report, _a = self._draft()
        image, payload = self._uploaded(report)
        text = json.dumps(payload)
        for marker in ("/web/content", "/web/image", "access_token", "ir.attachment",
                       "attachment_id", "checksum", "store_fname", "db_datas", "datas",
                       "http://", "https://", str(self._attachments(image).id) + ","):
            self.assertNotIn(marker, text, marker)
        self.assertEqual(
            set(payload["data"]["image"]),
            {"id", "name", "caption", "image_type", "image_type_label", "filename",
             "mimetype", "file_size", "uploaded_by", "uploaded_at", "sequence", "active"},
        )


# ===========================================================================
# 37-42 Removal
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyImageRemove(RadImageCase):

    def test_37_38_draft_and_entered_images_are_removed_with_their_file(self):
        for state in ("draft", "entered"):
            _record, report, _a = self._report_in(state)
            image, _p = self._uploaded(report)
            attachment = self._attachments(image)
            self.assertEqual(len(attachment), 1)
            response, payload = self._remove(report, image.id)
            self.assertEqual(response.status_code, 200, (state, payload))
            self.assertFalse(image.exists(), state)
            self.assertFalse(attachment.exists(), "no orphan attachment")
            self.assertEqual(payload["data"]["result"]["images"], [])
            self.assertEqual(payload["data"]["result"]["state"], state)

    def test_39_40_validated_and_released_images_cannot_be_removed(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report)
        self._enter(report, {"impression": "Normal."})
        report.with_user(self.radiologist).action_validate()
        for step in (None, "action_release"):
            if step:
                getattr(report.with_user(self.radiologist), step)()
            response, payload = self._remove(report, image.id)
            self.assertEqual(response.status_code, 409, report.state)
            self.assertEqual(payload["error"]["code"], "radiology_image_not_editable")
            self.assertTrue(image.exists(), report.state)

    def test_41_42_a_wrong_pair_or_a_second_removal_is_a_safe_404(self):
        _record, report, _a = self._draft()
        _record2, other, _a2 = self._draft()
        image, _p = self._uploaded(report)
        response, payload = self._remove(other, image.id)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (404, "radiology_image_not_found"))
        self.assertTrue(image.exists())
        self.assertEqual(self._remove(report, image.id)[0].status_code, 200)
        response, payload = self._remove(report, image.id)
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (404, "radiology_image_not_found"))


# ===========================================================================
# 43-46 Atomicity
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyImageAtomicity(RadImageCase):

    def _explode(self, *args, **kwargs):
        raise RuntimeError("serialization exploded")

    def test_43_44_46_a_failed_upload_leaves_no_image_attachment_or_audit(self):
        _record, report, _a = self._draft()
        attachments = len(self._attachments())
        audits = self._image_audit_count()
        with patch(CONTROLLER + ".serialize_image_metadata", side_effect=self._explode), \
                self.assertLogs(CONTROLLER, level="ERROR"):
            response, payload = self._upload(report)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "radiology_image_response_failed")
        self.assertNotIn("exploded", response.text)
        self.assertFalse(self._images(report))
        self.assertEqual(len(self._attachments()), attachments, "no orphan attachment")
        self.assertEqual(self._image_audit_count(), audits, "no partial audit")

    def test_45_46_a_failed_removal_is_undone(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report)
        attachment = self._attachments(image)
        audits = self._image_audit_count()
        with patch(CONTROLLER + ".serialize_operational_result", side_effect=self._explode), \
                self.assertLogs(CONTROLLER, level="ERROR"):
            response, payload = self._remove(report, image.id)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(payload["error"]["code"], "radiology_image_response_failed")
        self.assertTrue(image.exists())
        self.assertTrue(attachment.exists())
        self.assertEqual(base64.b64decode(image.file), PNG)
        self.assertEqual(self._image_audit_count(), audits)


# ===========================================================================
# 47-52 The Doctor sees nothing until release
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyImageDoctorVisibility(RadImageCase):

    def _doctor_bytes(self, appointment, image):
        self._auth(self.doctor_user, self.doctor_password)
        return self.url_open(DOCTOR_IMAGE % (appointment.id, image.id))

    def test_47_to_52_images_reach_the_doctor_only_at_release(self):
        record, report, appointment = self._draft()
        image, _p = self._uploaded(report, JPEG, "SECRETFILENAME.jpg")

        def assert_hidden(stage):
            result = self._doctor_result(record, appointment)
            self.assertEqual((result["status"], result["result"]), ("pending", None), stage)
            response, _payload = self._get(
                "/yoya-emr/api/v1/doctor/visits/%s/results" % appointment.id,
                self.doctor_user, self.doctor_password,
            )
            self.assertNotIn("SECRETFILENAME", response.text, stage)
            self.assertNotIn(str(image.id), json.dumps(result), stage)
            response = self._doctor_bytes(appointment, image)
            self.assertEqual(response.status_code, 404, stage)
            self.assertNotEqual(response.content, JPEG, stage)

        assert_hidden("draft")
        self._enter(report, {"impression": "Normal study."})
        assert_hidden("entered")
        report.with_user(self.radiologist).action_validate()
        assert_hidden("validated")
        report.with_user(self.radiologist).action_release()

        result = self._doctor_result(record, appointment)["result"]
        self.assertIsNotNone(result)
        self.assertEqual([i["id"] for i in result["images"]], [image.id])
        response = self._doctor_bytes(appointment, image)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, JPEG)


# ===========================================================================
# 53-56 The model still holds, whatever the channel
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyImageModelSecurity(RadImageCase):

    def test_53_54_the_backing_attachment_can_never_be_public_or_tokenised(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report)
        attachment = self._attachments(image)
        for values in ({"public": True}, {"access_token": "leak"}):
            with self.assertRaises(UserError, msg=str(values)):
                with self.env.cr.savepoint():
                    attachment.sudo().write(values)
        attachment.invalidate_recordset()
        self.assertFalse(attachment.public or attachment.access_token)

    def test_55_an_image_cannot_move_to_another_report(self):
        _record, report, _a = self._draft()
        _record2, other, _a2 = self._draft()
        image, _p = self._uploaded(report)
        with self.assertRaises(UserError):
            image.sudo().write({"result_id": other.id})
        self.assertEqual(image.result_id, report)

    def test_56_sudo_does_not_bypass_the_freeze(self):
        _record, report, _a = self._draft()
        image, _p = self._uploaded(report)
        self._enter(report, {"impression": "Normal."})
        report.with_user(self.radiologist).action_validate()
        Image = self.env[IMAGE_MODEL].sudo()
        for attempt in (
            lambda: Image.create({"result_id": report.id, "name": "Late", "filename": "late.png",
                                  "file": base64.b64encode(PNG)}),
            lambda: image.sudo().write({"caption": "Changed"}),
            lambda: image.sudo().write({"active": False}),
            lambda: image.sudo().unlink(),
            lambda: self._attachments(image).sudo().unlink(),
        ):
            with self.assertRaises(UserError):
                with self.env.cr.savepoint():
                    attempt()
        self.assertTrue(image.exists() and image.active)


# ===========================================================================
# 57-60 Nothing else moved
# ===========================================================================
@tagged("post_install", "-at_install", "radiology_desk")
class TestRadiologyImageRegression(RadImageCase):

    def test_57_report_save_still_works_beside_images(self):
        _record, report, _a = self._draft()
        self._uploaded(report)
        response, payload = self._save(report, {"findings": "Saved beside an image."})
        self.assertEqual(response.status_code, 200, payload)
        self.assertEqual(report.findings, "Saved beside an image.")
        self.assertEqual(len(payload["data"]["result"]["images"]), 1)

    def test_58_an_entered_reports_text_stays_frozen_while_its_images_change(self):
        _record, report, _a = self._report_in("entered")
        image, payload = self._uploaded(report)
        self.assertEqual(payload["data"]["result"]["state"], "entered")
        response, payload = self._save(report, {"findings": "Reopened?"})
        self.assertEqual((response.status_code, payload["error"]["code"]),
                         (409, "radiology_result_not_editable"))
        self.assertEqual(report.impression, "Normal study.")
        self.assertEqual(self._remove(report, image.id)[0].status_code, 200)
        report.invalidate_recordset()
        self.assertEqual(report.state, "entered")

    def test_60_images_move_no_lane_and_no_count(self):
        record, patient, *_rest = self._in_progress()
        report, _d = self._opened(record)
        self._enter(report, {"impression": "Normal."})
        scope = {"q": patient.name, "status": "awaiting_validation"}
        before = self._worklist(**scope)["summary"]
        image, _p = self._uploaded(report)
        self._remove(report, image.id)
        self._uploaded(report, PDF, "report.pdf")
        after = self._worklist(**scope)["summary"]
        self.assertEqual(before, after)
        self.assertEqual(self._detail(record)["lane"], "awaiting_validation")
        record.invalidate_recordset()
        self.assertEqual(record.state, "in_progress")

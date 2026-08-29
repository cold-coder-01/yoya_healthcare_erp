"""Slice 8B: reaching a released radiology image from the Doctor Desk.

WHAT THESE TESTS ARE FOR
------------------------
Four properties carry this slice.

  1. METADATA AND BYTES ARE TWO INDEPENDENT GATES. The serializer withholds
     images for an unreleased report, and the byte endpoint re-checks the
     release state itself. A bug in either must not hand the other away -- and
     an image id can be guessed without ever reading a payload, so the byte
     route can never rely on the payload having been withheld.

  2. NO URL, NO ATTACHMENT ID, NO TOKEN CROSSES. The payload names a
     hospital.radiology.image id and nothing else. An absolute URL is exactly
     where an Odoo origin, a /web/content path or an access token would leak
     into a browser, so the client composes its own path and the tests sweep
     the whole payload for the alternatives.

  3. EVERY REFUSAL LOOKS THE SAME. Out of scope, another doctor's, unreleased,
     archived, a patient-document id, an ir.attachment id and an id that never
     existed all answer 404, so the response never confirms a record exists.

  4. THE DESK STILL CANNOT WRITE. GET is the only verb, here as everywhere else
     on this surface.

FIXTURES DRIVE STATE, NOT THE DEPARTMENT WORKFLOW, for the reason the Slice 7B
and 8A suites give: hospital_billing's action_validate/action_release deliver
charges and re-check financial clearance, which is a whole apparatus these
tests have no business exercising to ask what a GET returns.
"""
import base64
import json
import uuid

from odoo.tests import tagged

from .test_doctor_results_api import ResultsCase

IMAGES = "/yoya-emr/api/v1/doctor/visits/%s/results/images/%s"

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
PDF = base64.b64encode(b"%PDF-1.4\n" + b"\x00" * 64)

# Anything that would mean a raw upstream reference reached the browser.
FORBIDDEN_IN_PAYLOAD = (
    "http://", "https://", "/web/content", "/web/image", "access_token",
    "8171", "localhost", "ir.attachment", "attachment_id", "datas",
)


class ResultsImagesCase(ResultsCase):
    """ResultsCase plus radiology images on a released report."""

    def _image(self, result, file=PNG, **extra):
        values = {
            "result_id": result.id,
            "name": extra.pop("name", "Brain CT - Axial View"),
            "filename": extra.pop("filename", "images.jpg"),
            "file": file,
        }
        values.update(extra)
        return self.env["hospital.radiology.image"].sudo().create(values)

    def _released_with_images(self, appointment=None, images=None, **result_kw):
        """A released radiology report carrying `images`.

        The images are attached while the result is still ENTERED, because
        Slice 8A freezes the set from validated onward -- which is the whole
        point of that slice and is not worked around here.
        """
        appointment = appointment or self._in_consultation_visit()[0]
        request = self._rad_request(appointment)
        result = self._rad_result(request, state="entered", **result_kw)
        created = [self._image(result, **spec) for spec in (images or [{}])]
        result.sudo().write({"state": "validated"})
        result.sudo().write({"state": "released"})
        return appointment, result, created

    def _content(self, appointment, image_id, user=None, password=None, **params):
        self._auth(user, password)
        query = "&".join("%s=%s" % (k, v) for k, v in params.items())
        url = IMAGES % (appointment.id, image_id)
        return self.url_open("%s%s" % (url, "?" + query if query else ""))

    def _radiology_row(self, appointment, user=None, password=None):
        _response, payload = self._results(appointment, user=user, password=password)
        return payload["data"]["radiology"][0]

    def _radiology_result(self, appointment):
        """The released result object, where images and their count live."""
        return self._radiology_row(appointment)["result"]


# ======================================================================
# Metadata
# ======================================================================
@tagged("post_install", "-at_install", "doctor_results", "doctor_results_images")
class TestResultImageMetadata(ResultsImagesCase):
    def test_a_released_jpeg_is_described_in_the_payload(self):
        appointment, _result, created = self._released_with_images(
            images=[{"file": JPEG, "filename": "images.jpg"}]
        )
        row = self._radiology_row(appointment)
        self.assertEqual(row["result"]["image_count"], 1)
        image = row["result"]["images"][0]
        self.assertEqual(image["id"], created[0].id)
        self.assertEqual(image["kind"], "image")
        self.assertEqual(image["mimetype"], "image/jpeg")
        self.assertEqual(image["filename"], "images.jpg")
        self.assertEqual(image["name"], "Brain CT - Axial View")
        self.assertGreater(image["file_size"], 0)

    def test_a_released_png_is_described(self):
        appointment, _result, _created = self._released_with_images(
            images=[{"file": PNG, "filename": "scan.png"}]
        )
        image = self._radiology_row(appointment)["result"]["images"][0]
        self.assertEqual(image["kind"], "image")
        self.assertEqual(image["mimetype"], "image/png")

    def test_a_released_pdf_is_described_as_a_pdf(self):
        appointment, _result, _created = self._released_with_images(
            images=[{"file": PDF, "filename": "report.pdf", "image_type": "report"}]
        )
        image = self._radiology_row(appointment)["result"]["images"][0]
        self.assertEqual(image["kind"], "pdf")
        self.assertEqual(image["mimetype"], "application/pdf")

    def test_the_kind_follows_the_sniffed_mimetype_not_the_extension(self):
        """The model already refused a file whose bytes disagree with its name;
        re-deciding from the name here would reopen that question."""
        appointment, _result, _created = self._released_with_images(
            images=[{"file": PDF, "filename": "actually_a_pdf.png"}]
        )
        image = self._radiology_row(appointment)["result"]["images"][0]
        self.assertEqual(image["kind"], "pdf")

    def test_the_count_and_the_order_are_deterministic(self):
        appointment, _result, _created = self._released_with_images(
            images=[
                {"name": "Third", "sequence": 30},
                {"name": "First", "sequence": 10},
                {"name": "Second", "sequence": 20},
            ]
        )
        row = self._radiology_row(appointment)
        self.assertEqual(row["result"]["image_count"], 3)
        self.assertEqual(
            [image["name"] for image in row["result"]["images"]],
            ["First", "Second", "Third"],
        )

    def test_an_archived_image_is_neither_counted_nor_listed(self):
        """Archived BEFORE the freeze, because Slice 8A refuses it afterwards
        -- which is that slice working, not an obstacle to route around."""
        appointment, _e = self._in_consultation_visit()
        request = self._rad_request(appointment)
        result = self._rad_result(request, state="entered")
        kept = self._image(result, name="Kept")
        dropped = self._image(result, name="Archived")
        dropped.sudo().write({"active": False})
        result.sudo().write({"state": "validated"})
        result.sudo().write({"state": "released"})

        row = self._radiology_row(appointment)
        self.assertEqual(row["result"]["image_count"], 1)
        self.assertEqual(
            [image["id"] for image in row["result"]["images"]], [kept.id]
        )

    def test_an_unreleased_report_describes_no_images_at_all(self):
        """The FIRST gate. `images` lives inside `result`, which a pending
        request does not have."""
        appointment, _e = self._in_consultation_visit()
        request = self._rad_request(appointment)
        result = self._rad_result(request, state="entered")
        self._image(result)

        row = self._radiology_row(appointment)
        self.assertEqual(row["status"], "pending")
        self.assertIsNone(row["result"])
        self.assertNotIn("images", json.dumps(row))

    def test_a_report_with_no_narrative_still_reports_its_images(self):
        """`has_report` is about the TEXT. A released report carrying only a
        picture is still a report with no text, and says so -- while the image
        is listed beside that sentence."""
        appointment, _result, _created = self._released_with_images()
        row = self._radiology_row(appointment)
        self.assertFalse(row["result"]["has_report"])
        self.assertEqual(row["result"]["image_count"], 1)

    def test_a_report_with_no_images_reports_an_empty_list(self):
        appointment, _e = self._in_consultation_visit()
        request = self._rad_request(appointment)
        self._rad_result(request, impression="Normal.")
        row = self._radiology_row(appointment)
        self.assertEqual(row["result"]["images"], [])
        self.assertEqual(row["result"]["image_count"], 0)

    # ------------------------------------------------------------------
    def test_NO_url_attachment_id_or_token_appears_anywhere(self):
        """THE payload boundary. Swept over the whole response, because a raw
        reference in any nested field is a raw reference in the browser."""
        appointment, _result, _created = self._released_with_images(
            images=[{"file": JPEG}, {"file": PDF, "filename": "r.pdf"}]
        )
        _response, payload = self._results(appointment)
        blob = json.dumps(payload).lower()
        for banned in FORBIDDEN_IN_PAYLOAD:
            self.assertNotIn(
                banned, blob,
                "'%s' reached the Doctor Results payload" % banned,
            )

    def test_the_id_is_the_image_id_and_never_the_attachment_id(self):
        appointment, _result, created = self._released_with_images()
        image = created[0]
        attachment = image._backing_attachments()
        self.assertTrue(attachment)

        row = self._radiology_row(appointment)
        serialized = row["result"]["images"][0]["id"]
        self.assertEqual(serialized, image.id)
        # They are different tables; if these ever coincide the assertion above
        # would pass for the wrong reason, so the identity is stated too.
        self.assertNotEqual(serialized, attachment.id)


# ======================================================================
# Bytes
# ======================================================================
@tagged("post_install", "-at_install", "doctor_results", "doctor_results_images")
class TestResultImageContent(ResultsImagesCase):
    def test_the_doctor_can_fetch_their_own_released_image(self):
        appointment, _result, created = self._released_with_images(
            images=[{"file": JPEG, "filename": "images.jpg"}]
        )
        response = self._content(appointment, created[0].id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertEqual(response.content, base64.b64decode(JPEG))

    def test_the_response_carries_the_length_and_a_safe_filename(self):
        appointment, _result, created = self._released_with_images(
            images=[{"file": PNG, "filename": "scan.png"}]
        )
        response = self._content(appointment, created[0].id)
        self.assertEqual(
            int(response.headers["Content-Length"]), len(base64.b64decode(PNG))
        )
        self.assertIn("scan.png", response.headers.get("Content-Disposition", ""))

    def test_clinical_bytes_are_never_cached(self):
        """`private` alone would still let a browser serve them from history on
        a shared workstation, which is where a hospital desk lives."""
        appointment, _result, created = self._released_with_images()
        response = self._content(appointment, created[0].id)
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")

    def test_a_pdf_can_be_requested_as_a_download(self):
        appointment, _result, created = self._released_with_images(
            images=[{"file": PDF, "filename": "report.pdf"}]
        )
        inline = self._content(appointment, created[0].id)
        self.assertNotIn("attachment", inline.headers.get("Content-Disposition", ""))

        download = self._content(
            appointment, created[0].id, disposition="attachment"
        )
        self.assertIn("attachment", download.headers["Content-Disposition"])
        self.assertEqual(download.headers["Content-Type"], "application/pdf")

    def test_an_arbitrary_disposition_is_ignored_rather_than_reflected(self):
        """The header is built from two literals, so a client cannot steer it."""
        appointment, _result, created = self._released_with_images()
        response = self._content(
            appointment, created[0].id, disposition="inline%0d%0aX-Injected:%20yes"
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Injected", response.headers)

    def test_a_filename_carrying_control_characters_cannot_split_the_header(self):
        appointment, _e = self._in_consultation_visit()
        request = self._rad_request(appointment)
        result = self._rad_result(request, state="entered")
        image = self._image(result, filename='ev\ril"\n.png')
        result.sudo().write({"state": "validated"})
        result.sudo().write({"state": "released"})

        response = self._content(appointment, image.id)
        self.assertEqual(response.status_code, 200)
        disposition = response.headers.get("Content-Disposition", "")
        self.assertNotIn("\r", disposition)
        self.assertNotIn("\n", disposition)

    # ------------------------------------------------------------------
    # Every refusal is the same 404
    # ------------------------------------------------------------------
    def _assert_not_found(self, response, message):
        self.assertEqual(response.status_code, 404, message)
        payload = json.loads(response.text)
        self.assertEqual(payload["success"], False)
        self.assertEqual(payload["error"]["code"], "image_not_found")

    def test_an_id_that_never_existed_is_not_found(self):
        appointment, _result, _created = self._released_with_images()
        self._assert_not_found(
            self._content(appointment, 99999999), "nonexistent id"
        )

    def test_another_doctors_image_is_not_found(self):
        theirs, _e = self._in_consultation_visit(doctor=self.other_doctor)
        request = self.env["hospital.radiology.request"].sudo().create({
            "patient_id": theirs.patient_id.id,
            "physician_id": self.other_doctor.id,
            "appointment_id": theirs.id,
            "encounter_id": theirs.encounter_id.id,
            "consultation_id": self._consultation_of(
                theirs.encounter_id
            ).id,
            "line_ids": [(0, 0, {"exam_id": self.chest_xray.id})],
        })
        result = self._rad_result(request, state="entered")
        image = self._image(result)
        result.sudo().write({"state": "validated"})
        result.sudo().write({"state": "released"})

        # As the caller's OWN visit id, so the refusal is about the image and
        # not about the appointment.
        mine, _r, _c = self._released_with_images()
        self._assert_not_found(
            self._content(mine, image.id), "another doctor's image"
        )

    def test_a_mismatched_appointment_is_not_found(self):
        _appointment, _result, created = self._released_with_images()
        other, _r2, _c2 = self._released_with_images()
        self._assert_not_found(
            self._content(other, created[0].id), "image from another visit"
        )

    def test_an_unreleased_parent_withholds_the_bytes_INDEPENDENTLY(self):
        """THE SECOND GATE, and the reason it is tested separately.

        The serializer would never have named these ids, but an id can be
        guessed. The byte route re-checks `result_id.state` itself, so the
        metadata gate is never load-bearing for the bytes.
        """
        for state in ("draft", "entered", "validated", "cancelled"):
            appointment, _e = self._in_consultation_visit()
            request = self._rad_request(appointment)
            result = self._rad_result(request, state="entered")
            image = self._image(result)
            if state == "cancelled":
                result.sudo().write({"state": "cancelled"})
            elif state != "entered":
                if state == "draft":
                    result.sudo().write({"state": "draft"})
                else:
                    result.sudo().write({"state": "validated"})
            self.assertEqual(result.state, state)
            self._assert_not_found(
                self._content(appointment, image.id), "parent in %s" % state
            )

    def test_an_archived_image_is_not_found(self):
        appointment, _e = self._in_consultation_visit()
        request = self._rad_request(appointment)
        result = self._rad_result(request, state="entered")
        image = self._image(result)
        image.sudo().write({"active": False})
        result.sudo().write({"state": "validated"})
        result.sudo().write({"state": "released"})
        self._assert_not_found(self._content(appointment, image.id), "archived")

    def test_a_patient_document_id_is_not_found(self):
        """A different model's id must not resolve here."""
        appointment, _result, _created = self._released_with_images()
        document = self.env["hospital.patient.document"].sudo().create({
            "name": "Referral",
            "patient_id": appointment.patient_id.id,
            "attachment": PDF,
            "filename": "referral.pdf",
        })
        self._assert_not_found(
            self._content(appointment, document.id), "patient document id"
        )

    def test_an_ir_attachment_id_is_not_found(self):
        """THE endpoint accepts an image id, never a database-wide file handle."""
        appointment, _result, created = self._released_with_images()
        attachment = created[0]._backing_attachments()
        self.assertTrue(attachment)
        response = self._content(appointment, attachment.id)
        # It either resolves to no image at all, or -- if the two tables happen
        # to share an integer -- to an image this visit does not own. Both are
        # the same 404.
        self._assert_not_found(response, "ir.attachment id")

    def test_a_visit_with_no_consultation_is_not_found(self):
        appointment, _result, created = self._released_with_images()
        bare, _encounter = self._ready_visit()
        self._assert_not_found(
            self._content(bare, created[0].id), "visit with no consultation"
        )

    # ------------------------------------------------------------------
    # Roles, completion, and the absence of any other verb
    # ------------------------------------------------------------------
    def test_a_completed_consultation_still_serves_its_images(self):
        """Chasing imaging is precisely what a doctor does after the visit."""
        appointment, encounter = self._in_consultation_visit()
        _a, _result, created = self._released_with_images(appointment=appointment)

        self._consultation_of(encounter).sudo().write({"state": "completed"})
        appointment.sudo().write({"state": "done"})
        appointment.invalidate_recordset()

        response = self._content(appointment, created[0].id)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)

    def test_the_front_office_and_the_bench_cannot_fetch_an_image(self):
        appointment, _result, created = self._released_with_images()
        for user, password in (
            (self.receptionist, self.receptionist_password),
            (self.cashier, self.cashier_password),
            (self.nurse, self.nurse_password),
            (self.accountant, self.accountant_password),
        ):
            response = self._content(
                appointment, created[0].id, user=user, password=password
            )
            self.assertIn(
                response.status_code, (403, 404),
                "%s reached the image bytes" % user.login,
            )

    def test_the_image_route_answers_no_verb_but_get(self):
        appointment, _result, created = self._released_with_images()
        self._auth()
        url = self.base_url() + (IMAGES % (appointment.id, created[0].id))
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            response = self.opener.request(method, url)
            self.assertNotEqual(
                response.status_code, 200,
                "%s on the image route was accepted" % method,
            )

    def test_fetching_an_image_changes_nothing_about_it(self):
        """A GET is a read. The 8A freeze means it could not write even if it
        tried, but the endpoint must not be trying."""
        appointment, _result, created = self._released_with_images()
        image = created[0]
        before = (image.name, image.filename, image.file_size, image.active)
        self._content(appointment, image.id)
        image.invalidate_recordset()
        self.assertEqual(
            (image.name, image.filename, image.file_size, image.active), before
        )
        attachment = image._backing_attachments()
        self.assertFalse(attachment.public)
        self.assertFalse(attachment.access_token)

import base64

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.mimetypes import guess_mimetype


class HospitalRadiologyImage(models.Model):
    """A clinical file attached to a radiology result.

    WHY A CHILD MODEL RATHER THAN A MANY2MANY TO ir.attachment.
    Three reasons, and the third is the one that matters.

    A caption, an ordering and a category have to live somewhere, and a
    many2many relation table has no identity to hang them on. Second, this
    mirrors hospital.patient.document, the only document model this repository
    already has, so the shape is one a maintainer here has met before.

    Third, and decisively: ir.attachment.check() resolves access by browsing
    res_model/res_id and calling check_access() on THAT record, so the record
    rules of the model owning the attachment are what protect the bytes. Owning
    the attachment through this model means the doctor scope written for
    radiology results reaches the file itself. A many2many to ir.attachment
    would have to be secured against ir.attachment, which is shared with every
    other model in the database and carries no record rule at all.

    STORAGE. `file` is Binary(attachment=True), which is Odoo's default: the
    bytes live in ir_attachment and therefore in the filestore, not in a column
    of this table. Every existing binary in this repository is stored that way
    (743 attachments, none with db_datas), so nothing new is introduced here.

    THE IMAGE SET FREEZES WITH THE REPORT. See _assert_mutable(): once the
    result is validated the files are part of a document a radiologist has
    signed off, and none of create, write, unlink or archive may touch them.
    That guard lives on THIS model rather than on the parent, because a child
    write never passes through the parent's write() -- and an attachment write
    would not pass through either. A released report whose images could still be
    swapped would be frozen in name only.
    """

    _name = "hospital.radiology.image"
    _description = "Radiology Image"
    _order = "sequence, id"

    # The result states in which the image set may still be changed. Derived
    # from the report workflow, not invented: 'validated' is the point at which
    # a radiologist has signed the report, and hospital_billing delivers the
    # imaging charge at release a step later.
    MUTABLE_RESULT_STATES = ("draft", "entered")

    # THE ONLY THREE TYPES PHASE 1 ACCEPTS, matched against SNIFFED bytes.
    ALLOWED_MIMETYPES = ("image/jpeg", "image/png", "application/pdf")

    # 25 MB. A chest study exported as JPEG sits far below this and a reported
    # PDF further below again; the cap exists to stop an accidental upload of
    # something that is not a clinical image at all.
    MAX_FILE_SIZE = 25 * 1024 * 1024

    result_id = fields.Many2one(
        "hospital.radiology.result",
        string="Radiology Result",
        required=True,
        ondelete="cascade",
        index=True,
        help="The report this file belongs to. Cascade: an image has no "
        "meaning without the result it illustrates.",
    )
    name = fields.Char(
        required=True,
        help="What the file shows, in clinical terms: 'Chest AP', not "
        "'IMG_0042'.",
    )
    caption = fields.Char()
    sequence = fields.Integer(default=10)
    image_type = fields.Selection(
        [
            ("image", "Image"),
            ("report", "Report document"),
            ("other", "Other"),
        ],
        default="image",
        required=True,
    )
    file = fields.Binary(
        string="File",
        required=True,
        attachment=True,
    )
    filename = fields.Char(required=True)
    # DERIVED FROM THE BYTES, NEVER FROM THE CLIENT. Both are written by
    # _apply_file_metadata() on every create and on every write that carries a
    # new file, and a value supplied by a caller is discarded.
    mimetype = fields.Char(readonly=True)
    file_size = fields.Integer(string="File Size (bytes)", readonly=True)
    uploaded_by_id = fields.Many2one(
        "res.users",
        string="Uploaded By",
        readonly=True,
        default=lambda self: self.env.user,
    )
    uploaded_at = fields.Datetime(
        readonly=True,
        default=fields.Datetime.now,
    )
    active = fields.Boolean(default=True)

    @api.depends("name", "filename")
    def _compute_display_name(self):
        for image in self:
            image.display_name = image.name or image.filename or "New Image"

    # ------------------------------------------------------------------
    # File inspection
    # ------------------------------------------------------------------
    @api.model
    def _decode(self, value):
        """The raw bytes behind a Binary value.

        Odoo hands a Binary field base64 from the web client and may hand it
        raw bytes from Python. Both are accepted; anything else is a caller
        error rather than a validation failure and says so.
        """
        if isinstance(value, bytes):
            try:
                return base64.b64decode(value, validate=True)
            except (ValueError, base64.binascii.Error):
                return value
        if isinstance(value, str):
            try:
                return base64.b64decode(value.encode(), validate=True)
            except (ValueError, base64.binascii.Error):
                return value.encode()
        raise ValidationError("A radiology image must carry file content.")

    @api.model
    def _inspect_file(self, value):
        """(mimetype, size) read from the CONTENT, plus the two refusals.

        THE EXTENSION IS NEVER CONSULTED, and that is the whole point. A file
        named chest.png whose bytes are an HTML document is a stored-cross-site
        payload the moment anything renders it, and its name says nothing at
        all about that. odoo.tools.mimetypes.guess_mimetype reads the leading
        bytes -- the same helper ir.attachment itself uses -- so the answer
        comes from the file rather than from whoever named it.
        """
        raw = self._decode(value)
        size = len(raw)
        if not size:
            raise ValidationError("A radiology image must carry file content.")
        if size > self.MAX_FILE_SIZE:
            raise ValidationError(
                "This file is %.1f MB. Radiology images are limited to %d MB "
                "each. Attach a compressed export, or split the study across "
                "several files."
                % (size / (1024 * 1024), self.MAX_FILE_SIZE // (1024 * 1024))
            )
        mimetype = guess_mimetype(raw)
        if mimetype not in self.ALLOWED_MIMETYPES:
            raise ValidationError(
                "This file is '%s', which radiology imaging does not accept. "
                "Attach a JPEG, a PNG or a PDF.\n\nThe file's CONTENT decides "
                "this, not its name: renaming a file does not change what it "
                "is." % mimetype
            )
        return mimetype, size

    def _apply_file_metadata(self, values):
        """Overwrite any client-supplied mimetype/file_size with the truth.

        A `file` key that is present but EMPTY is refused here rather than left
        to the field's `required`, so clearing the content of an existing image
        fails with the same sentence as uploading nothing.
        """
        if "file" not in values:
            return values
        if not values["file"]:
            raise ValidationError("A radiology image must carry file content.")
        mimetype, size = self._inspect_file(values["file"])
        values["mimetype"] = mimetype
        values["file_size"] = size
        return values

    # ------------------------------------------------------------------
    # Backing attachment hardening
    # ------------------------------------------------------------------
    def _backing_attachments(self):
        """The ir.attachment rows Odoo created for `file` on these records."""
        if not self.ids:
            return self.env["ir.attachment"].sudo()
        return self.env["ir.attachment"].sudo().search(
            [
                ("res_model", "=", self._name),
                ("res_field", "=", "file"),
                ("res_id", "in", self.ids),
            ]
        )

    def _enforce_private_storage(self):
        """The SECOND layer keeping a clinical image out of public reach.

        THE FIRST is hospital_radiology's ir.attachment override, which refuses
        to publish or tokenise an evidence row through any ORM path at all.
        This one heals what that guard cannot see: a row made public by raw SQL,
        by a restore, or before this module was upgraded.

        Why it is worth keeping rather than deleting as redundant.
        ir.attachment.check() short-circuits on `public` before it ever reaches
        the record rules -- `if public and mode == 'read': continue` -- and
        validate_access() returns a sudo recordset for a public attachment or
        for any caller presenting an access_token. Either one turns a scoped
        clinical file into a link anyone with the URL can open, so a state the
        ORM cannot produce is still a state worth repairing on sight.

        IT WRITES ONLY WHEN SOMETHING IS ACTUALLY EXPOSED. On a healthy
        database this is one indexed read and no write at all. NARROWLY SCOPED:
        it only ever touches attachments belonging to this model's own `file`
        field.
        """
        exposed = self._backing_attachments().filtered(
            lambda attachment: attachment.public or attachment.access_token
        )
        if exposed:
            exposed.write({"public": False, "access_token": False})

    # ------------------------------------------------------------------
    # The freeze
    # ------------------------------------------------------------------
    @api.model
    def _assert_result_mutable(self, result, action):
        """Refuse to change the image set of a signed report.

        NO MANAGER OVERRIDE, deliberately. There is no amendment or retraction
        workflow in this module for an override to be part of, and a released
        report whose images can be replaced is worse than one that cannot be
        corrected: the narrative would still read as signed while the evidence
        under it had changed. The clinical path for a correction is a new
        result, which is what hospital.laboratory.request already tells users
        about repeat testing.
        """
        if not result:
            return
        state = result.sudo().state
        if state in self.MUTABLE_RESULT_STATES:
            return
        raise UserError(
            "Radiology result %s is '%s', so its imaging can no longer be %s.\n\n"
            "The images are part of a report a radiologist has signed off. "
            "Correcting them means issuing a new radiology result, not "
            "changing the evidence behind one that has already been reported."
            % (result.sudo().display_name, state, action)
        )

    def _assert_mutable(self, action):
        for image in self:
            self._assert_result_mutable(image.result_id, action)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        Result = self.env["hospital.radiology.result"]
        for values in vals_list:
            # The freeze is checked BEFORE the file is inspected, so a refusal
            # on a signed report says so rather than complaining about bytes.
            if values.get("result_id"):
                self._assert_result_mutable(
                    Result.browse(values["result_id"]), "added to"
                )
            self._apply_file_metadata(values)
            # Provenance is the server's, never the client's.
            values["uploaded_by_id"] = self.env.user.id
            values.setdefault("uploaded_at", fields.Datetime.now())
        images = super().create(vals_list)
        images._enforce_private_storage()
        for image in images:
            image._create_audit_log(
                action_type="create",
                description="Radiology image attached.",
                new_value=image._audit_summary(
                    ("name", "filename", "mimetype", "file_size", "image_type", "sequence")
                ),
            )
        return images

    def write(self, values):
        # ARCHIVING IS A CHANGE TOO. Without naming `active` here, a signed
        # report's images could be made to vanish from every list in the system
        # while the freeze reported success.
        action = "archived" if values.get("active") is False else "changed"
        self._assert_mutable(action)
        # Re-pointing an image at another result would move evidence between
        # reports; it is provenance, exactly as request_id is on the result.
        if "result_id" in values:
            for image in self:
                if values["result_id"] != image.result_id.id:
                    raise UserError(
                        "The radiology result on image %s is clinical "
                        "provenance and cannot be changed. Remove this image "
                        "and attach the file to the correct result."
                        % image.display_name
                    )
        self._apply_file_metadata(values)

        tracked = {
            key: value
            for key, value in values.items()
            # The bytes never reach the audit log: a base64 blob in an audit
            # row is unreadable, unbounded and a second copy of clinical data.
            if key not in ("write_date", "write_uid", "display_name", "file")
        }
        old_values = {
            image.id: image._audit_summary(tracked.keys()) for image in self
        }
        result = super().write(values)
        # On EVERY write, not only a file change: the guarantee is that an
        # image record can never be left with a public or tokenised backing
        # attachment, and that has to hold whatever the caller was editing.
        self._enforce_private_storage()
        if tracked:
            action_type = "archive" if values.get("active") is False else "update"
            description = (
                "Radiology image archived."
                if action_type == "archive"
                else "Radiology image updated."
            )
            for image in self:
                image._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(image.id),
                    new_value=image._audit_summary(tracked.keys()),
                )
        return result

    def unlink(self):
        """Removal, and the audit trail it does and does not leave.

        A BLOCKED removal is logged; a successful one is not. That is this
        repository's convention, not a shortcut: hospital.audit.log's
        action_type vocabulary has no 'delete' -- it offers 'delete_attempt' --
        and hospital.laboratory.result follows exactly the same shape, logging
        the refusal and letting the permitted deletion pass unlogged. The
        create entry plus the record's absence is the trail for a file removed
        before anyone signed anything, and the freeze is what makes that safe:
        nothing on a validated report can be removed at all.

        The refusal is logged BEFORE it is raised, so an attempt to strip
        evidence off a signed report leaves a mark whether or not it succeeds.
        """
        blocked = self.filtered(
            lambda image: image.result_id.sudo().state
            not in self.MUTABLE_RESULT_STATES
        )
        for image in blocked:
            image._create_audit_log(
                action_type="delete_attempt",
                description="Removal of an image from a signed radiology "
                "result was blocked.",
                old_value=image._audit_summary(
                    ("name", "filename", "mimetype", "file_size", "image_type")
                ),
            )
        self._assert_mutable("removed")
        return super().unlink()

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    def _audit_summary(self, field_names):
        values = []
        for field_name in field_names:
            if field_name in self._fields and field_name != "file":
                value = self[field_name]
                if hasattr(value, "mapped"):
                    value = ", ".join(value.mapped("display_name"))
                values.append(f"{field_name}: {value}")
        return "; ".join(values)

    def _create_audit_log(self, action_type, description, old_value=False, new_value=False):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for image in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=image.result_id.sudo().patient_id.id,
                model_name=image._name,
                record_id=image.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )

from odoo import api, models
from odoo.exceptions import UserError


class IrAttachment(models.Model):
    """The clinical freeze, extended to the bytes it is actually about.

    WHY THIS EXISTS, AND WHY THE CHILD MODEL WAS NOT ENOUGH.
    hospital.radiology.image freezes create/write/unlink from `validated`
    onward, but `file` is Binary(attachment=True): the evidence lives in an
    ir.attachment row that model does not mediate. ir.attachment.check()
    resolves write access by calling check_access('write') on the OWNING record
    -- and the imaging bench legitimately holds write on
    hospital.radiology.image -- so a direct write to the attachment never
    consults the image model's guard at all.

    That was verified rather than assumed: before this override, an imaging
    bench user could replace `datas` on the backing row of a RELEASED report
    and the write succeeded silently. A report that reads as signed while its
    images can still be swapped is signed in name only, so the freeze belongs
    here as well.

    THE SCOPE IS TWO COLUMNS WIDE, DELIBERATELY. Only rows whose res_model is
    hospital.radiology.image AND whose res_field is `file` are touched.
    Everything else in ir.attachment -- every other model, every chatter
    attachment, every web asset, and even a non-`file` attachment sitting on an
    image record -- behaves exactly as Odoo shipped it. A guard on ir.attachment
    is a guard on the whole database's file storage; narrowing it to the rows
    that are radiology evidence is what keeps that acceptable.

    WHY AN OVERRIDE AND NOT A RECORD RULE. A rule decides WHO may act; this is a
    statement about what the record permits regardless of who is asking, so it
    has to survive sudo. It does: a model override runs for the superuser too,
    which a rule does not.
    """

    _inherit = "ir.attachment"

    # The one field on the one model whose attachments are clinical evidence.
    EVIDENCE_MODEL = "hospital.radiology.image"
    EVIDENCE_FIELD = "file"

    def _radiology_evidence(self):
        """The subset of `self` that is radiology clinical evidence."""
        return self.filtered(
            lambda attachment: attachment.res_model == self.EVIDENCE_MODEL
            and attachment.res_field == self.EVIDENCE_FIELD
        )

    def _radiology_image_of(self, attachment):
        """The image record behind an evidence row, or an empty recordset.

        EMPTY IS A LEGITIMATE ANSWER, and getting that wrong would break every
        ordinary deletion. Odoo's unlink deletes a record's row FIRST and cleans
        up its field attachments afterwards (models.py), so the cascade always
        reaches this code with the parent already gone. An unresolvable parent
        therefore means "the image has been removed through its own guarded
        path", not "something suspicious is happening".

        sudo() because this is a property of the DATA: a user who may write the
        attachment must be refused on a signed report whether or not they can
        read the image record itself.
        """
        if not attachment.res_id:
            return self.env[self.EVIDENCE_MODEL].sudo().browse()
        return self.env[self.EVIDENCE_MODEL].sudo().browse(attachment.res_id).exists()

    @api.model
    def _radiology_exposure_requested(self, values):
        """True when `values` would publish or tokenise an attachment.

        Only a TRUTHY value counts. Clearing either column is always allowed:
        hospital.radiology.image._enforce_private_storage() writes
        public=False, and a guard that refused every write to these columns
        would block the very repair that keeps them safe.
        """
        return bool(values.get("public")) or bool(values.get("access_token"))

    def _assert_radiology_evidence_mutable(self, values, action):
        """Refuse a change to evidence behind a signed report, or any exposure.

        The exposure refusal is state-independent: `public` short-circuits
        ir.attachment.check() before it reaches any record rule, and an
        access_token hands back a sudo recordset to anyone holding the string.
        Neither is acceptable on a clinical image at ANY point in the workflow,
        so it is refused while the report is still being written too.
        """
        if self._radiology_exposure_requested(values):
            raise UserError(
                "A radiology image is a scoped clinical record and can never "
                "be made public or given an access token. Doctors reach it "
                "through their own visit, and nothing else may."
            )
        Image = self.env[self.EVIDENCE_MODEL]
        for attachment in self:
            image = self._radiology_image_of(attachment)
            if not image:
                continue
            Image._assert_result_mutable(image.result_id, action)

    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """A new evidence row may not appear on a report that is already signed.

        Odoo's own Binary.create() reaches this while the image is being
        created, which can only happen on a draft or entered result, so the
        ordinary upload path passes unchanged.
        """
        for values in vals_list:
            if (
                values.get("res_model") == self.EVIDENCE_MODEL
                and values.get("res_field") == self.EVIDENCE_FIELD
            ):
                if self._radiology_exposure_requested(values):
                    raise UserError(
                        "A radiology image can never be created as a public or "
                        "tokenised attachment."
                    )
                image = self.env[self.EVIDENCE_MODEL].sudo().browse(
                    values.get("res_id") or 0
                ).exists()
                if image:
                    self.env[self.EVIDENCE_MODEL]._assert_result_mutable(
                        image.result_id, "added to"
                    )
        return super().create(vals_list)

    def write(self, values):
        evidence = self._radiology_evidence()
        if evidence:
            evidence._assert_radiology_evidence_mutable(values, "changed")
        return super().write(values)

    def unlink(self):
        evidence = self._radiology_evidence()
        if evidence:
            evidence._assert_radiology_evidence_mutable({}, "removed")
        return super().unlink()

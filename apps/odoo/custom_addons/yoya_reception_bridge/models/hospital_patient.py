"""MRN protection.

hospital.patient.identification_code carries no database constraint, so two
patients can hold the same MRN if a sequence is reset or a value is written
directly.

A Python constraint is used rather than a SQL unique index on purpose: it
installs safely on a database that ALREADY contains duplicates, blocking new
ones without making the upgrade fail. Promoting it to a real unique index is a
separate, deliberate step -- see README notes and the detection SQL reported
with this module.
"""
from odoo import api, models
from odoo.exceptions import ValidationError


class HospitalPatient(models.Model):
    _inherit = "hospital.patient"

    @api.constrains("identification_code")
    def _check_identification_code_unique(self):
        for patient in self:
            code = (patient.identification_code or "").strip()
            # 'New' is the pre-sequence placeholder and is never an identity.
            if not code or code == "New":
                continue
            clash = self.with_context(active_test=False).search(
                [
                    ("identification_code", "=", code),
                    ("id", "!=", patient.id),
                ],
                limit=1,
            )
            if clash:
                raise ValidationError(
                    "Medical record number %s is already assigned to %s. "
                    "An MRN must identify exactly one patient."
                    % (code, clash.display_name)
                )

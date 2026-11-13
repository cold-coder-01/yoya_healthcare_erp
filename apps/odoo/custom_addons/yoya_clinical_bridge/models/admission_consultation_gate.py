"""Decision 3 (docs/business-decisions.md): an open consultation blocks discharge.

hospital_admission owns every discharge gate (the doctor's medical-discharge
request, the final discharge, and the discharge-checks list) and asks one hook,
hospital.admission._discharge_open_consultation(), whether the visit still has
an open consultation. hospital.consultation lives here, above hospital_admission,
so this module answers it.

WHAT "THE RELEVANT CONSULTATION" IS. A consultation is unique per encounter. The
admission's own visit (encounter_id) is the one that matters; the encounter of
the admission's appointment is checked too, so a stay whose two links disagree
cannot slip through on the one that happens to be clean.

WHAT BLOCKS. A consultation in state 'draft' -- archived or not. Archiving is
not completion: counting only active rows would make archiving a way around
the doctor's completion, and the consultation charge would never be delivered.

WHAT DOES NOT BLOCK, AND WHY NO SPECIAL CASE IS NEEDED. An admission with no
appointment, a consultation that was never started (the row is created only at
start), and an emergency stay without a consultation all have no draft
consultation to find. An emergency stay WITH an open consultation is blocked
like any other: emergency is not an exemption from completing the note.

NEVER AUTO-COMPLETES. Nothing here writes. Only the doctor completes a
consultation, through hospital.consultation.action_complete().
"""

from odoo import models


class HospitalAdmissionConsultationGate(models.Model):
    _inherit = "hospital.admission"

    def _discharge_consultation_encounters(self):
        """The admission's visit plus its appointment's visit, if different."""
        self.ensure_one()
        admission = self.sudo()
        encounters = admission.encounter_id
        appointment = admission.appointment_id
        if appointment:
            encounters |= self.env["hospital.encounter"].sudo().search(
                [("appointment_id", "=", appointment.id)]
            )
        return encounters

    def _discharge_open_consultation(self, lock=False):
        self.ensure_one()
        encounters = self._discharge_consultation_encounters()
        if not encounters:
            return False
        # sudo(): whether the visit's note is still open is a property of the
        # visit; the admissions clerk's rights on clinical notes do not change
        # it. active_test=False: an archived draft still blocks.
        Consultation = (
            self.env["hospital.consultation"].sudo().with_context(active_test=False)
        )
        if lock:
            # The same advisory lock get_or_create_for_appointment() takes, so a
            # consultation cannot be opened for this visit between the check and
            # the discharge.
            for encounter in encounters.sorted("id"):
                Consultation._lock_encounter_consultation(encounter)
        return Consultation.search(
            [("encounter_id", "in", encounters.ids), ("state", "=", "draft")],
            order="id",
            limit=1,
        ) or False

    def _discharge_consultation_detail(self, consultation):
        """Amount-free and safe for both desks: the consultation reference, the
        holding doctor's name, when it was started, and whether it is archived
        (an administrator must restore it before the doctor can complete it)."""
        if not consultation:
            return {}
        consultation = consultation.sudo()
        return {
            "reference": consultation.name,
            "doctor_name": consultation.doctor_id.name or None,
            "started_at": consultation.started_at.isoformat() if consultation.started_at else None,
            "archived": not consultation.active,
        }

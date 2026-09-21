"""Encounter <-> admission coherence (Admissions Slice 0, PART 17).

hospital.encounter is the episode of care; an inpatient admission is what
happens to one. Once the two are linked, they can contradict each other in a
way neither model could previously see:

  * the encounter is closed or cancelled while a patient is still in a bed, so
    the stay belongs to an episode that is over;
  * the admission's own constraint cannot catch it, because @api.constrains on
    hospital.admission does not fire when hospital.encounter is the record
    being written.

So the guard has to live on the encounter side as well. hospital_management
left `_check_can_close()` as an extension hook for exactly this, and
hospital_billing deliberately does not use it -- its comment says physical
discharge clearance "will be enforced in hospital_admission, not here". This
is that enforcement.

WHAT IS AND IS NOT DECIDED HERE
-------------------------------
This slice blocks closing or cancelling an episode that still has a patient in
a bed. It does NOT decide what happens to the encounter when the patient is
discharged -- whether discharge completes the episode, who may close it, and
what financial clearance is required first. That is discharge policy and it
belongs to Slice 4.
"""
from odoo import api, fields, models

from .admission_authority import (
    ADMISSION_ACTIVE_STATES,
    AdmissionWorkflowError,
)


class HospitalEncounter(models.Model):
    _inherit = "hospital.encounter"

    admission_ids = fields.One2many(
        "hospital.admission",
        "encounter_id",
        string="Admissions",
    )
    active_admission_id = fields.Many2one(
        "hospital.admission",
        string="Active Admission",
        compute="_compute_active_admission_id",
        help="The inpatient admission currently attached to this visit, if any.",
    )
    is_inpatient_active = fields.Boolean(
        string="Patient Is Admitted",
        compute="_compute_active_admission_id",
    )

    @api.depends("admission_ids.state")
    def _compute_active_admission_id(self):
        for encounter in self:
            active = encounter.admission_ids.filtered(
                lambda adm: adm.state in ADMISSION_ACTIVE_STATES
            )[:1]
            encounter.active_admission_id = active
            encounter.is_inpatient_active = bool(active)

    def _active_admissions(self):
        """Admissions holding a bed against this encounter.

        sudo(): whether a patient is physically admitted is a property of the
        data, not of what the user closing the encounter may read. A
        receptionist closing a visit may have no record-rule access to the
        ward the patient is on, and closing it anyway would be exactly the
        bug. It only ever refuses.
        """
        self.ensure_one()
        return (
            self.env["hospital.admission"]
            .sudo()
            .search(
                [
                    ("encounter_id", "=", self.id),
                    ("state", "in", list(ADMISSION_ACTIVE_STATES)),
                ],
                limit=1,
            )
        )

    def _open_admissions(self):
        """Admissions still OPEN against this encounter: a draft request not yet
        admitted, or a patient in a bed. sudo() for the reason
        _active_admissions() gives; it only ever defers or refuses."""
        self.ensure_one()
        return (
            self.env["hospital.admission"]
            .sudo()
            .search(
                [
                    ("encounter_id", "=", self.id),
                    ("state", "in", ["draft"] + list(ADMISSION_ACTIVE_STATES)),
                ],
                limit=1,
            )
        )

    def action_complete(self):
        """A visit with an open admission is NOT finished when its consultation is.

        THE FLOW THIS PROTECTS (Admissions Slice 2). A doctor requests an
        admission during a consultation and then completes the consultation --
        the normal order. Completing the consultation runs
        hospital.appointment.action_done(), which (hospital_billing) calls this
        method and would move the visit active -> completed. A completed visit
        is a CLOSED episode, so the admissions desk could then no longer admit
        from it, and a patient already in a bed would be stranded on a closed
        episode (the Slice 1 needs-review reason `encounter_closed`).

        So an encounter carrying an open admission -- draft or active -- is
        left active. The consultation, the appointment and the consultation
        charge all finish exactly as before; only the EPISODE stays open,
        because it has become an inpatient stay. Completing it is the discharge
        workflow's job (Slice 4), not the outpatient consultation's.

        Every other encounter completes exactly as before. Nothing here raises,
        so completing a consultation can never be blocked by an admission.
        """
        deferred = self.filtered(lambda encounter: encounter._open_admissions())
        for encounter in deferred:
            encounter._log_audit(
                "update",
                "Completion of visit %s deferred: an inpatient admission is open "
                "against it." % encounter.name,
            )
        remaining = self - deferred
        if remaining:
            return super(HospitalEncounter, remaining).action_complete()
        return True

    def _check_can_close(self):
        """hospital_management's extension hook. Runs inside action_close()."""
        result = super()._check_can_close()
        for encounter in self:
            if encounter._active_admissions():
                raise AdmissionWorkflowError(
                    "admission_encounter_has_active_admission"
                )
        return result

    def action_cancel(self):
        """action_close() consults _check_can_close(); action_cancel() does not.

        Cancelling an episode with a patient in a bed strands the admission
        just as thoroughly as closing it, so the same refusal applies. Checked
        before super() so nothing is written when it refuses.
        """
        for encounter in self:
            if encounter._active_admissions():
                raise AdmissionWorkflowError(
                    "admission_encounter_has_active_admission"
                )
        return super().action_cancel()

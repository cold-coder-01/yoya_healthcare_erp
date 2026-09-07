"""Opening the consultation record is a side effect of starting a consultation.

THE INVARIANT THIS FILE EXISTS FOR
----------------------------------
    appointment.state == 'in_consultation'
        =>  a hospital.consultation exists for that appointment's encounter

WHY THE OVERRIDE IS HERE AND NOT IN THE CONTROLLER
--------------------------------------------------
The Doctor Desk endpoint is not the only way a visit reaches in_consultation.
hospital.appointment carries a Start Consultation button in the Odoo backend
form, and action_start_consultation() is reachable by RPC and from any future
service. An endpoint that created the consultation itself would satisfy the
invariant for exactly one caller and leave every other route producing a visit
in consultation with no note to write in -- which the read path would then have
to report as a server error to a clinician who did nothing wrong.

Putting it in the model is the same reasoning yoya_clinical_bridge already
applies to _assert_triage_minimum_data: a rule that only one channel obeys is
not a rule.

ATOMICITY IS INHERITED, NOT REIMPLEMENTED
------------------------------------------
The Doctor API wraps action_start_consultation() in env.cr.savepoint(), so this
creation runs inside that savepoint automatically. If anything downstream fails
-- including building the success response -- the savepoint rolls back BOTH the
appointment transition and the consultation. There is no second savepoint here,
and there must not be: nesting one would let the consultation survive a rollback
of the transition that justified it.

NO CLINICAL DECISION IS ADDED
------------------------------
super() is yoya_reception_bridge's override, which runs the assignment gate and
the triage gate before deferring to hospital_billing's financial-clearance gate
and finally to hospital_management's state change. None of that is duplicated,
inspected or bypassed here. This block runs AFTER all of it and asks exactly one
question: did the visit actually end up in consultation? Only then is a record
opened, and opening it is idempotent.
"""
from odoo import models

from .consultation import CONSULTATION_APPOINTMENT_STATE


class HospitalAppointment(models.Model):
    _inherit = "hospital.appointment"

    def action_start_consultation(self):
        """Start the consultation, then ensure its clinical record exists.

        FILTERED ON THE RESULTING STATE, DELIBERATELY.
        action_start_consultation() filters on state == 'confirmed', so calling
        it on a visit that is already in consultation, already done or
        cancelled is a no-op that leaves the state alone. Keying off the state
        AFTER super() therefore means:

          * a genuine start        -> in_consultation -> record opened
          * a double-click / retry -> already in_consultation -> record reused,
                                      because get_or_create_for_appointment is
                                      idempotent under an advisory lock and a
                                      unique index
          * a no-op on a done or cancelled visit -> nothing created, and no
            spurious refusal raised at a caller who was not starting anything

        That last case is why this is not simply `for appointment in self`:
        get_or_create_for_appointment refuses a visit that is not in
        consultation, and raising that at a caller whose no-op start was
        previously harmless would be a behaviour change this slice has no
        business making.
        """
        result = super().action_start_consultation()

        # Read as the caller, created as the caller. The three roles
        # _assert_may_start_consultation admits -- the assigned doctor, a
        # Hospital Manager and a System Administrator -- all hold create on
        # hospital.consultation and all satisfy its record rule for this visit,
        # so no elevation is needed and none is taken.
        consultation = self.env["hospital.consultation"]
        for appointment in self.filtered(
            lambda record: record.state == CONSULTATION_APPOINTMENT_STATE
        ):
            consultation.get_or_create_for_appointment(appointment)
        return result

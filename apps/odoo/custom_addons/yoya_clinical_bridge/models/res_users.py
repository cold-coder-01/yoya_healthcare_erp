from odoo import api, fields, models

# THE ACTIVE CARE RELATIONSHIP, stated once.
#
# Verified against the live workflow vocabulary rather than assumed:
# hospital.appointment.state is
#   draft / confirmed / in_consultation / done / cancelled.
#
# 'confirmed' and 'in_consultation' are the two states in which a doctor is
# actually responsible for a patient right now. Everything else is excluded on
# purpose:
#
#   draft      a booking nobody has confirmed. A record that may never become a
#              visit must not hand out a patient's whole clinical past.
#   done       THE IMPORTANT EXCLUSION. Including it would make longitudinal
#              access permanent after a single completed visit, which is
#              exactly the "every patient ever treated" scope this policy
#              exists to refuse.
#   cancelled  no care relationship was ever established.
#
# THE CONSEQUENCE, STATED PLAINLY: when the current episode closes -- the
# appointment moves to done or cancelled -- this doctor's cross-provider
# History access to that patient LAPSES. That is the intended Phase-1
# behaviour, not an oversight.
ACTIVE_CARE_APPOINTMENT_STATES = ("confirmed", "in_consultation")


class ResUsers(models.Model):
    _inherit = "res.users"

    # Neither hospital_management nor hospital_billing links a user to a
    # department: hospital.doctor.department_id covers doctors only, and
    # hospital.department.manager_id names a single supervisor. The nurse
    # record rule needs a department roster, so the bridge carries its own
    # rather than reaching into hospital_hr (not a declared dependency).
    yoya_permitted_department_ids = fields.Many2many(
        "hospital.department",
        "yoya_user_permitted_department_rel",
        "user_id",
        "department_id",
        string="Permitted Clinical Departments",
        help="Departments whose unassigned evaluations this user may triage. "
        "Leave empty to restrict the user to evaluations assigned to them.",
    )

    # ------------------------------------------------------------------
    # LONGITUDINAL CARE RELATIONSHIP
    # ------------------------------------------------------------------
    yoya_care_relationship_patient_ids = fields.Many2many(
        "hospital.patient",
        string="Patients Under My Active Care",
        compute="_compute_yoya_care_relationship_patient_ids",
        # NON-STORED, AND THAT IS THE WHOLE POINT. A stored column would be a
        # cached authorization decision: it would have to be invalidated every
        # time an appointment was booked, reassigned, completed or cancelled,
        # and any missed invalidation would leave a doctor holding access to a
        # patient they no longer treat. Recomputing per request is cheap (one
        # indexed-ish search, cached in the environment for the rest of the
        # request) and cannot go stale.
        store=False,
        help="Patients this user currently has an active clinical appointment "
        "for. Read by the longitudinal History record rules; it is not a "
        "setting and cannot be edited.",
    )

    @api.depends_context("uid")
    def _compute_yoya_care_relationship_patient_ids(self):
        """Patients with a CURRENTLY ACTIVE appointment assigned to this user.

        THIS FEEDS ir.rule DOMAINS, so its properties are security properties.

        NO SUDO. The search runs as the calling user, so hospital.appointment's
        own record rules still apply on top of the domain below. The domain
        pins ('doctor_id.user_id', '=', user.id) explicitly rather than
        relying on those rules, so the answer is the same whatever combination
        of groups the caller holds -- a manager, who implies Hospital Doctor
        and whose appointment rule is unrestricted, still gets only the
        patients assigned to THEM, never the hospital's whole census.

        NO RECURSION, and this is load-bearing rather than lucky. Evaluating a
        History rule reads this field, which searches hospital.appointment,
        whose rules must therefore never themselves mention this field. That is
        precisely why Slice 9A adds NO care-relationship rule to
        hospital.appointment -- see the security XML, which states the same
        constraint from the other side. Adding one later would make rule
        evaluation re-enter this compute.

        WRITES NOTHING. No record is created, no hospital.patient is touched,
        and the field has no inverse -- an assignment to it raises rather than
        silently granting access.

        FAIL-CLOSED for a user other than the caller: the search still runs
        under the caller's rules, so a pure doctor inspecting a colleague's
        field reads an empty set rather than that colleague's patients.
        """
        Appointment = self.env["hospital.appointment"]
        for user in self:
            appointments = Appointment.search(
                [
                    ("doctor_id.user_id", "=", user.id),
                    ("state", "in", list(ACTIVE_CARE_APPOINTMENT_STATES)),
                ]
            )
            # .patient_id on a recordset is the DISTINCT union of the targets,
            # so a patient with three active appointments appears once.
            user.yoya_care_relationship_patient_ids = appointments.patient_id

"""Visit metadata and consultation-start authorization.

hospital.appointment grants write access to every doctor AND every nurse, and
carries no record rules, so before this module any nurse could call
action_start_consultation() and move a patient into consultation. The guard
below closes that at model level, not in the API.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"

CONSULTATION_OVERRIDE_GROUPS = (G_MANAGER, G_ADMIN)

# Exact operator-facing wording for the triage gate.
TRIAGE_REQUIRED_MESSAGE = (
    "Nursing triage must be completed before consultation can start."
)


class HospitalAppointment(models.Model):
    _inherit = "hospital.appointment"

    visit_type = fields.Selection(
        [
            ("routine", "Routine"),
            ("emergency", "Emergency"),
            ("follow_up", "Follow Up"),
            ("referral", "Referral"),
        ],
        required=True,
        default="routine",
        index=True,
        tracking=True,
    )
    registered_by_id = fields.Many2one(
        "res.users",
        string="Registered By",
        readonly=True,
        copy=False,
    )
    registered_at = fields.Datetime(readonly=True, copy=False)
    reception_workflow_managed = fields.Boolean(
        string="Reception Managed",
        default=False,
        readonly=True,
        copy=False,
        index=True,
        help="True only for visits registered through "
        "hospital.reception.workflow.create_visit(). Legacy appointments stay "
        "False and keep their original behaviour, which is what makes the "
        "triage gate safe to introduce on a live database.",
    )
    triage_destination_id = fields.Many2one(
        "hospital.department",
        string="Triage Destination",
        help="Department the patient is sent to for triage. Defaults to the "
        "appointment department.",
    )

    # Reverse link so the queue stage can reason about triage without a search.
    evaluation_ids = fields.One2many(
        "hospital.patient.evaluation",
        "appointment_id",
        string="Evaluations",
    )

    clinical_queue_stage = fields.Selection(
        [
            ("registered", "Registered"),
            ("awaiting_payment", "Awaiting Payment"),
            ("awaiting_triage", "Awaiting Triage"),
            ("in_triage", "In Triage"),
            ("awaiting_doctor", "Awaiting Doctor"),
            ("in_consultation", "In Consultation"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        compute="_compute_clinical_queue_stage",
        string="Queue Stage",
        help="Derived from the appointment state, the evaluation, financial "
        "clearance and any emergency bypass. It is never written directly.",
    )

    # ------------------------------------------------------------------
    # Queue stage
    # ------------------------------------------------------------------
    #
    # Deliberately NOT stored. The inputs traverse billing_blocked and
    # encounter_id, both non-stored compute_sudo fields on other models, plus
    # emergency_bypass on the encounter. Odoo cannot invalidate a stored value
    # when those change, so storing it would produce a queue that is silently
    # wrong -- worse than one that is recomputed on read. Queue filtering is
    # done in the API by composing the underlying domains.
    #
    @api.depends(
        "state",
        "evaluation_ids.state",
        "evaluation_ids.started_at",
    )
    def _compute_clinical_queue_stage(self):
        for appointment in self:
            appointment.clinical_queue_stage = appointment._resolve_queue_stage()

    def _resolve_queue_stage(self):
        self.ensure_one()

        if self.state == "cancelled":
            return "cancelled"
        if self.state == "done":
            return "completed"
        if self.state == "in_consultation":
            return "in_consultation"
        if self.state == "draft":
            return "registered"

        # state == 'confirmed': the patient is checked in.
        evaluation = self._latest_evaluation()
        if evaluation:
            if evaluation.state == "done":
                return "awaiting_doctor"
            if evaluation.state == "draft" and evaluation.started_at:
                return "in_triage"

        # An emergency bypass must never park a patient in a payment queue.
        if self._is_payment_blocking():
            return "awaiting_payment"
        return "awaiting_triage"

    def _latest_evaluation(self):
        self.ensure_one()
        evaluations = self.evaluation_ids.sorted(
            key=lambda record: (record.evaluation_date or fields.Datetime.now(), record.id),
            reverse=True,
        )
        return evaluations[:1]

    def _is_payment_blocking(self):
        """True when money is genuinely standing between the patient and triage.

        Uses ENCOUNTER-WIDE clearance, not appointment.billing_blocked.
        hospital_billing scopes billing_blocked to the consultation charge
        alone (appointment_billing.py:82-84), which is right for gating a
        consultation but wrong here: a new patient who paid only the 300 ETB
        consultation while 1,200 ETB of card fee stood unpaid would otherwise
        flip from awaiting_payment to awaiting_triage.
        """
        self.ensure_one()
        encounter = self.encounter_id
        if encounter and encounter.emergency_bypass:
            return False
        if self.visit_type == "emergency":
            # Emergency arrivals are screened and triaged first; billing is
            # resolved afterwards.
            return False
        if encounter:
            return not encounter.reception_clearance_ok
        # No encounter yet (never confirmed): fall back to the appointment's own
        # signal, which is all that exists at that point.
        return bool(self.billing_blocked)

    # ------------------------------------------------------------------
    # Reception-managed marker
    # ------------------------------------------------------------------
    def write(self, vals):
        """The marker is set once, at registration, and never toggled by hand.

        Clearing it on a live visit would silently disable the triage gate, so
        only a system administrator may touch it after creation -- and that is
        audited.
        """
        if "reception_workflow_managed" in vals:
            if not self.env.user.has_group(G_ADMIN):
                raise AccessError(
                    "'Reception Managed' is set by the reception workflow when a "
                    "visit is registered and cannot be changed manually."
                )
            for appointment in self:
                appointment._create_audit_log(
                    "Reception-managed marker changed to %s by %s."
                    % (bool(vals["reception_workflow_managed"]), self.env.user.display_name)
                )
        return super().write(vals)

    # ------------------------------------------------------------------
    # Consultation-start authorization
    # ------------------------------------------------------------------
    def _assert_may_start_consultation(self):
        """Only the assigned doctor, a manager or a system administrator.

        Nurses, receptionists, cashiers, accountants, pharmacists, lab
        technicians and unassigned doctors are all rejected here, regardless of
        what any API or UI allows.
        """
        self.ensure_one()
        user = self.env.user

        if any(user.has_group(group) for group in CONSULTATION_OVERRIDE_GROUPS):
            return

        doctor = self.doctor_id
        if doctor and doctor.user_id and doctor.user_id.id == user.id:
            return

        if not doctor:
            raise AccessError(
                "Appointment %s has no assigned doctor. Only a Hospital Manager or "
                "Hospital System Administrator may start this consultation."
                % (self.appointment_code or self.id)
            )
        raise AccessError(
            "Only %s, the doctor assigned to appointment %s, may start this "
            "consultation. Hospital Managers and System Administrators may also "
            "do so." % (doctor.display_name, self.appointment_code or self.id)
        )

    def _assert_triage_completed(self):
        """Reception-managed visits must clear nursing triage first.

        Scoped deliberately to reception_workflow_managed: imposing this on the
        legacy appointments already in healthcare_erp_phase1_test would block
        consultations for every visit that predates triage tracking.
        """
        self.ensure_one()
        if not self.reception_workflow_managed:
            return
        evaluation = self._latest_evaluation()
        if not evaluation or evaluation.state != "done":
            raise UserError(TRIAGE_REQUIRED_MESSAGE)

    def action_start_consultation(self):
        """Authorize, gate on triage, then defer to the billing-aware parent.

        super() is hospital_billing's override, which enforces financial
        clearance and moves the encounter and consultation charge. None of that
        is reimplemented or bypassed here.
        """
        for appointment in self.filtered(lambda record: record.state == "confirmed"):
            appointment._assert_may_start_consultation()
            appointment._assert_triage_completed()
        return super().action_start_consultation()

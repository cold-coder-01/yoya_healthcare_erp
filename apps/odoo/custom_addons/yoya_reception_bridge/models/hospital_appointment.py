"""Visit metadata and consultation-start authorization.

hospital.appointment grants write access to every doctor AND every nurse, and
carries no record rules, so before this module any nurse could call
action_start_consultation() and move a patient into consultation. The guard
below closes that at model level, not in the API.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .reception_capability import has_reception_workflow_capability

G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"

CONSULTATION_OVERRIDE_GROUPS = (G_MANAGER, G_ADMIN)
DIRECT_CREATE_GROUPS = (G_MANAGER, G_ADMIN)

# Roles holding perm_create here that must still go through
# hospital.reception.workflow.create_visit(). See
# hospital_patient.WORKFLOW_ONLY_CREATE_GROUPS.
WORKFLOW_ONLY_CREATE_GROUPS = (G_RECEPTIONIST, G_FRONT_DESK_NURSE)

# Exact operator-facing wording for the triage gate.
TRIAGE_REQUIRED_MESSAGE = (
    "Nursing triage must be completed before consultation can start."
)

# THE canonical front-desk queue vocabulary, in workflow order.
#
# This is the order the hospital actually works in: the people at the entrance
# are nurses, they triage on arrival, and the cashier comes AFTER the nursing
# evaluation -- not before it.
FRONT_DESK_STAGES = (
    ("new", "New"),
    ("intake", "Intake"),
    ("triage", "Triage"),
    ("awaiting_cashier", "Awaiting Cashier"),
    ("ready_doctor", "Ready For Doctor"),
    ("in_consultation", "In Consultation"),
    ("completed", "Completed"),
    ("cancelled", "Cancelled"),
)

# The legacy clinical_queue_stage vocabulary is DERIVED from the canonical one
# above rather than computed separately, so the two can never disagree. Existing
# readers of clinical_queue_stage (Odoo views, the reception API and its Next.js
# client) keep the exact selection values they already know.
LEGACY_STAGE_BY_FRONT_DESK = {
    "new": "registered",
    "intake": "awaiting_triage",
    "triage": "in_triage",
    "awaiting_cashier": "awaiting_payment",
    "ready_doctor": "awaiting_doctor",
    "in_consultation": "in_consultation",
    "completed": "completed",
    "cancelled": "cancelled",
}


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

    front_desk_stage = fields.Selection(
        FRONT_DESK_STAGES,
        compute="_compute_front_desk_stage",
        string="Front Desk Stage",
        help="Canonical front-desk queue stage, derived from the appointment "
        "state, the nursing evaluation and encounter-wide financial clearance. "
        "Never written directly.",
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
    # Both fields go through _resolve_front_desk_stage(), the single derivation.
    # Reading either on a RECORDSET batches the compute, which is what keeps the
    # front-desk worklist off a per-row query path.
    #
    @api.depends(
        "state",
        "evaluation_ids.state",
        "evaluation_ids.started_at",
    )
    def _compute_front_desk_stage(self):
        for appointment in self:
            appointment.front_desk_stage = appointment._resolve_front_desk_stage()

    @api.depends(
        "state",
        "evaluation_ids.state",
        "evaluation_ids.started_at",
    )
    def _compute_clinical_queue_stage(self):
        for appointment in self:
            appointment.clinical_queue_stage = appointment._resolve_queue_stage()

    def _resolve_front_desk_stage(self):
        """THE derivation. Everything else maps from this.

        Order matters, and this order is the workflow change: the nursing
        evaluation is consulted BEFORE money. Triage is a clinical act performed
        by the nurse at the entrance and must never wait on the cashier. Money
        is only asked about once triage is complete, which is exactly when the
        patient physically walks to the cashier.

        No new persisted handoff state was added: 'awaiting_cashier' is derived
        from facts that already exist (evaluation.state plus encounter-wide
        clearance), so it cannot drift out of sync with the money or the triage.
        """
        self.ensure_one()

        if self.state == "cancelled":
            return "cancelled"
        if self.state == "done":
            return "completed"
        if self.state == "in_consultation":
            return "in_consultation"
        if self.state == "draft":
            return "new"

        # state == 'confirmed': the patient is checked in and physically here.
        evaluation = self._latest_evaluation()
        if not evaluation or evaluation.state == "cancelled":
            return "intake"
        if evaluation.state == "draft":
            # started_at is what distinguishes a claimed evaluation from a
            # queued one; the base model adds no extra state for it.
            return "triage" if evaluation.started_at else "intake"

        # Triage is DONE. Only now does money decide where the patient goes.
        if self._is_payment_blocking():
            return "awaiting_cashier"
        return "ready_doctor"

    def _resolve_queue_stage(self):
        """Legacy vocabulary, mapped from the canonical derivation."""
        self.ensure_one()
        return LEGACY_STAGE_BY_FRONT_DESK[self._resolve_front_desk_stage()]

    def _latest_evaluation(self):
        self.ensure_one()
        evaluations = self.evaluation_ids.sorted(
            key=lambda record: (record.evaluation_date or fields.Datetime.now(), record.id),
            reverse=True,
        )
        return evaluations[:1]

    def _is_payment_blocking(self):
        """True when money still stands between the patient and the DOCTOR.

        Not between the patient and triage -- nothing does, any more. This is
        consulted only after the nursing evaluation is complete, and it is what
        separates 'awaiting_cashier' from 'ready_doctor'.

        Uses ENCOUNTER-WIDE clearance, not appointment.billing_blocked.
        hospital_billing scopes billing_blocked to the consultation charge
        alone (appointment_billing.py:82-84), which is right for gating a
        consultation but wrong here: a new patient who paid only the 300 ETB
        consultation while 1,200 ETB of card fee stood unpaid would otherwise
        be shown as ready for the doctor with money still owed at the desk.

        Being encounter-wide makes this CONSERVATIVE with respect to the
        authoritative doctor gate: encounter-wide clearance implies the
        consultation-scoped clearance that action_start_consultation enforces,
        so 'ready_doctor' can never over-promise. The reverse is deliberate --
        a patient who owes only the card fee is kept at the cashier.
        """
        self.ensure_one()
        encounter = self.encounter_id
        if encounter and encounter.emergency_bypass:
            return False
        if encounter:
            return not encounter.reception_clearance_ok
        # No encounter yet (never confirmed): fall back to the appointment's own
        # signal, which is all that exists at that point.
        return bool(self.billing_blocked)

    # ------------------------------------------------------------------
    # Reception-workflow-only creation
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        self._assert_appointment_creation_allowed()
        return super().create(vals_list)

    @api.model
    def _assert_appointment_creation_allowed(self):
        """Receptionists open appointments through the workflow, not the ORM.

        Scoped to Receptionist specifically: hospital_management's ACL grants
        perm_create=1 on hospital.appointment to exactly receptionist,
        manager and system administrator; doctor and nurse hold read/write
        only (create=0). This guard changes nothing for doctor or nurse --
        they were already blocked at the ACL layer, and remain blocked there.

        Creating an appointment outside create_visit() skips clearance
        persistence, the reception_workflow_managed marker and encounter
        check-in, so a receptionist bypassing it here would silently produce
        a visit the triage gate and queue-stage logic do not understand.
        """
        if self.env.su:
            return
        if has_reception_workflow_capability():
            return
        user = self.env.user
        if any(user.has_group(group) for group in DIRECT_CREATE_GROUPS):
            return
        if any(user.has_group(group) for group in WORKFLOW_ONLY_CREATE_GROUPS):
            raise AccessError(
                "Appointments cannot be created directly. Use "
                "hospital.reception.workflow.create_visit(), which opens the "
                "appointment, encounter and billing together and checks the "
                "patient in correctly."
            )

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

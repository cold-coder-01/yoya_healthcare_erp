"""Visit metadata and consultation-start authorization.

hospital.appointment grants write access to every doctor AND every nurse, and
carries no record rules, so before this module any nurse could call
action_start_consultation() and move a patient into consultation. The guard
below closes that at model level, not in the API.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from odoo.addons.hospital_billing.models.charge_line import AMOUNT_TOLERANCE

from .reception_capability import has_reception_workflow_capability

G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_FRONT_DESK_NURSE = "yoya_reception_bridge.group_hospital_front_desk_nurse"

CONSULTATION_OVERRIDE_GROUPS = (G_MANAGER, G_ADMIN)
DIRECT_CREATE_GROUPS = (G_MANAGER, G_ADMIN)

# Resetting a finished or cancelled visit back to draft is a supervisory
# correction, not day-to-day work. Same tuple as CONSULTATION_OVERRIDE_GROUPS
# and stated separately on purpose: the two answer different questions, and a
# future read-only supervisor role belongs in one and not the other.
RESET_TO_DRAFT_GROUPS = (G_MANAGER, G_ADMIN)

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

# ----------------------------------------------------------------------
# THE SECOND CASHIER LANE: ACTIVE SERVICE CLEARANCE.
#
# front_desk_stage answers ONE question -- where is this patient in the
# pre-consultation handoff -- and it answers 'in_consultation' the moment care
# starts, deliberately and correctly. Its 'awaiting_cashier' arm is not even
# reached once the doctor has begun, because _resolve_front_desk_stage returns
# on the appointment state first.
#
# That is why a charge raised DURING a consultation was invisible to the cashier
# queue: laboratory today, and radiology, medication and procedures on exactly
# the same billing path. The lab request rendered AWAITING CLEARANCE to the
# doctor while the patient existed in no cashier queue at all.
#
# The fix is a SECOND LANE, not a wider stage. front_desk_stage keeps its
# meaning, its vocabulary and its early return; the appointment stays
# state='in_consultation' throughout; and no combined
# 'in_consultation_awaiting_cashier' state is invented. Clinical state and
# financial state are separate facts, and merging them is what would make one of
# them a lie.
#
# Membership is derived from BILLING TRUTH ONLY -- no queue flag is written and
# none is cleared, so a visit leaves this lane the instant the money it was
# waiting on is received.
# ----------------------------------------------------------------------
# BOTH live states, and 'done' is here because of Slice 4.
#
# Completing a consultation moves the appointment in_consultation -> done. When
# this tuple held only 'in_consultation', that transition made an unpaid patient
# VANISH from the cashier queue at the exact moment the doctor signed off: money
# still owed, no queue anywhere, and the laboratory still refusing to collect the
# specimen. The Defect A report flagged the exclusion as a deliberate scope
# decision; Slice 4 is the event that invalidates it.
#
# Widening the tuple is the whole change. The predicate below is untouched, so a
# 'done' visit with nothing outstanding never appears, and one that owes money
# leaves the lane the instant it is paid. No new lane, no new state, no
# front_desk_stage semantics touched, and the appointment stays 'done'
# throughout -- paying is not a clinical transition.
ACTIVE_SERVICE_CLEARANCE_STATES = ("in_consultation", "done")

# The charge states a live obligation can be in. Identical to the scope
# hospital.billing.engine.check_financial_clearance sums over, so a charge this
# lane names as blocking is a charge that engine really is blocking on.
# Cancelled and reversed charges are absent here for that reason, not as a
# separate policy.
LIVE_CHARGE_STATES = ("draft", "active")


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
    # Active service clearance
    # ------------------------------------------------------------------
    def _is_active_service_clearance_pending(self):
        """Care is already under way AND money blocks the next service.

        THE DISCOVERY PREDICATE FOR THE SECOND CASHIER LANE. It answers a
        different question from front_desk_stage and does not touch it: the
        appointment is and stays state='in_consultation', and nothing here is
        written, mirrored or cached.

        GENERIC BY CONSTRUCTION. It names no clinical model. The blocking
        judgement is _is_payment_blocking(), which is encounter-wide live
        clearance from hospital.billing.engine -- so a radiology, medication or
        procedure charge raised mid-consultation surfaces here on the day it is
        first raised, with no code added to this method or to the cashier API.

        Emergency bypass and fully-authorized sponsorship both resolve to False
        through _is_payment_blocking(), exactly as they do for
        'awaiting_cashier'. One predicate, two lanes.

        Covers BOTH in_consultation and done. A visit whose consultation has
        been completed can still be holding an undelivered, unpaid service --
        that is the ordinary outpatient shape, where the doctor signs off and
        the patient walks to the cashier and then to the laboratory. Dropping it
        from the lane at completion would strand exactly that patient.
        """
        self.ensure_one()
        if self.state not in ACTIVE_SERVICE_CLEARANCE_STATES:
            return False
        if not self.encounter_id:
            return False
        return self._is_payment_blocking()

    def _active_service_blocking_charges(self):
        """The live charges whose unpaid patient side is holding this visit.

        THE SAME SET the engine's cash arm sums, filtered by the SAME
        per-charge figure: amount_due_for_clearance is already mode-aware
        (patient residual under 'enforce', legacy gross otherwise) and already
        zero for a delivery-basis charge, so no formula is restated here.

        Used for presentation only -- which generic service categories the
        cashier is collecting for, and how recently they were ordered.
        _is_active_service_clearance_pending() remains the membership test; an
        empty result here never removes a visit from the lane, because the
        engine can block on grounds no single charge figure expresses (an
        unauthorized sponsor share, for one).
        """
        self.ensure_one()
        encounter = self.encounter_id
        account = encounter.billing_account_id if encounter else None
        if not account:
            return self.env["hospital.charge.line"]
        return account.charge_line_ids.filtered(
            lambda line: line.charge_state in LIVE_CHARGE_STATES
            and line.amount_due_for_clearance > AMOUNT_TOLERANCE
        )

    # ------------------------------------------------------------------
    # Completion and reset authorization
    # ------------------------------------------------------------------
    def _assert_may_complete_visit(self):
        """Who may finish a visit. SAME ANSWER as who may start one.

        hospital_management.action_done() carries no authorization at all, and
        the ACL grants write on hospital.appointment to Receptionist, Nurse and
        Doctor alike -- the receptionist's record rule being [(1,'=',1)]. Before
        Slice 4 that was merely untidy, because 'done' was a scheduling fact.
        It is not untidy now: action_done() is what freezes a clinical note,
        delivers the consultation charge and completes the encounter, and a
        front-desk clerk must not be able to do any of that to a record they
        hold no rights to read.

        Deliberately the SAME rule as _assert_may_start_consultation rather than
        a new one. A visit somebody may open and somebody else may close would
        make the clinical author of the episode ambiguous.
        """
        self.ensure_one()

        # ELEVATED CODE KEEPS ITS AUTHORITY, and this is a deliberate
        # DIFFERENCE from _assert_may_start_consultation above -- which does
        # NOT honour su, because starting a consultation is only ever a human
        # act at a desk.
        #
        # Completion is not only that. action_done() is called by workflow code
        # in other modules and by fixtures that have already established their
        # own authority, and it is the method a future cron closing stale visits
        # would use. Refusing an explicit .sudo() would not close a hole -- the
        # caller already holds superuser rights -- it would only push that code
        # into writing `state` directly and skipping the billing chain entirely.
        #
        # Same bypass, same reasoning and same one-line shape as
        # _assert_appointment_creation_allowed in this file. The API layer never
        # sudo()s, so every Doctor Desk path is still fully guarded, and
        # hospital.consultation.action_complete() deliberately calls this AS THE
        # CALLER so an authorization bug there surfaces rather than hides.
        if self.env.su:
            return

        user = self.env.user

        if any(user.has_group(group) for group in CONSULTATION_OVERRIDE_GROUPS):
            return

        doctor = self.doctor_id
        if doctor and doctor.user_id and doctor.user_id.id == user.id:
            return

        if not doctor:
            raise AccessError(
                "Visit %s has no assigned doctor. Only a Hospital Manager or "
                "Hospital System Administrator may complete it."
                % (self.appointment_code or self.id)
            )
        raise AccessError(
            "Only %s, the doctor assigned to visit %s, may complete it. "
            "Hospital Managers and Hospital System Administrators may also do "
            "so." % (doctor.display_name, self.appointment_code or self.id)
        )

    def action_done(self):
        """Authorize, then defer to the billing-aware parent.

        super() is hospital_billing's override, which marks the consultation
        charge delivered and moves the encounter active -> completed. NONE of
        that is reimplemented or bypassed here; this adds the authorization the
        vendor method never had.

        FILTERED ON THE STATES action_done ACTUALLY ACTS ON, matching
        action_start_consultation's shape. Calling it on an already-done or
        cancelled visit is a no-op in the parent, and raising AccessError at a
        caller whose no-op was previously harmless would be a behaviour change
        this slice has no business making.
        """
        for appointment in self.filtered(
            lambda record: record.state in ("confirmed", "in_consultation")
        ):
            appointment._assert_may_complete_visit()
        return super().action_done()

    def action_reset_to_draft(self):
        """Supervisory only, and NEVER over a completed consultation.

        TWO SEPARATE HAZARDS, CLOSED SEPARATELY.

        First, authorization. The vendor method is protected by nothing but the
        `groups=` attribute on its form-view button, which stops nobody reaching
        it over RPC or through a future API. The model-level check is the real
        control.

        Second, and worse: the method writes state and NOTHING else. It does not
        reset the encounter, does not unfreeze the consultation and does not
        reverse the delivered consultation charge. Run against a completed
        visit it produces appointment=draft with encounter=completed and
        consultation=completed -- a visit that looks startable and is not,
        because action_start_consultation would then try to open a SECOND
        consultation on an encounter whose unique index already refuses one.
        The doctor's clinical note stays frozen throughout (the freeze keys on
        consultation.state, not on the appointment), so nothing is silently
        editable -- but the visit is wedged.

        Reopening a completed consultation is an AMENDMENT, and no amendment
        workflow exists. Rather than half-invent one, this refuses and says so.
        """
        self._assert_may_reset_to_draft()
        return super().action_reset_to_draft()

    def _assert_may_reset_to_draft(self):
        """Two checks, and only ONE of them yields to sudo().

        The group check does, for the reason _assert_may_complete_visit
        documents: elevated code already holds the rights it is being asked
        for.

        The completed-consultation refusal below does NOT, and must not. That
        is an INTEGRITY rule, not an authorization one -- resetting a signed
        clinical visit leaves the appointment in draft with the encounter
        completed and the note locked, and it is no safer done by a migration
        than by a receptionist. Same reasoning as _assert_no_active_episode,
        which also refuses the superuser.
        """
        for appointment in self:
            if not self.env.su and not any(
                self.env.user.has_group(group) for group in RESET_TO_DRAFT_GROUPS
            ):
                raise AccessError(
                    "Only a Hospital Manager or Hospital System Administrator "
                    "may reset visit %s to draft."
                    % (appointment.appointment_code or appointment.id)
                )
            appointment._assert_no_completed_consultation()

    def _assert_no_completed_consultation(self):
        """A signed clinical record is not undone by a scheduling action.

        sudo() on the LOOKUP only: whether a completed consultation exists is a
        property of the visit, not of the acting user's clinical read rights --
        a Hospital Manager holds no ACL on hospital.consultation unless granted
        one, and the guard must refuse either way. It reads one column and
        returns nothing to the caller.
        """
        self.ensure_one()
        consultation = (
            self.env["hospital.consultation"]
            .sudo()
            .search(
                [("appointment_id", "=", self.id), ("state", "=", "completed")],
                limit=1,
            )
        )
        if not consultation:
            return
        raise UserError(
            "Visit %s cannot be reset to draft: its consultation has been "
            "completed and the clinical record is signed. Resetting would "
            "leave the visit in draft while the encounter stays completed and "
            "the note stays locked.\n\nThere is no amendment or reopen "
            "workflow for a completed consultation. Raise this with a Hospital "
            "System Administrator if the record genuinely has to be corrected."
            % (self.appointment_code or self.id)
        )

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

"""The physician's consultation record.

WHY A NEW MODEL, AND WHY IT LIVES HERE
--------------------------------------
Nothing in hospital_management documents a consultation. The nearest records
are hospital.patient.evaluation -- which is the NURSING TRIAGE record and
carries unique(appointment_id), so the triage already occupies the one slot per
visit -- and hospital.treatment.plan, which is a care-plan artefact with its own
lifecycle and no encounter anchor. Neither can host a physician note without
conflating two clinical authors, so this is a new record.

It lives in yoya_clinical_bridge rather than hospital_management because this
module already owns the encounter linkage and the clinical record rules, and
because hospital_management is treated as vendor core. No file in
hospital_management or hospital_billing is modified.

WHY THE ENCOUNTER IS THE ANCHOR
-------------------------------
The encounter IS the episode of care: it is what hospital.billing.account hangs
off, what every lab/radiology/pharmacy charge resolves to, and what
hospital.encounter._assert_no_active_episode already guarantees is unique and
live for one patient at a time. Anchoring on the appointment instead would tie
the note to a scheduling artefact and leave appointment-less encounters (the
standalone paths hospital_billing already supports) with nowhere to document.

appointment_id and patient_id are STORED RELATED fields off the encounter, not
independent columns. That is deliberate and is stronger than a constraint: a
cross-patient or cross-visit consultation is not merely rejected, it is
unrepresentable.
"""
from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

# Reopening or amending a completed consultation is a supervisory act that this
# slice deliberately does not implement. The groups are named here so the freeze
# message can point at the right people.
MANAGER_GROUPS = (
    "hospital_management.group_hospital_manager",
    "hospital_management.group_hospital_system_administrator",
)

# THE appointment state in which a consultation is meaningful.
#
# Stated once, here, and imported by the API layer. A consultation must never be
# opened for a visit still at the desk or in triage: the note would exist before
# the clinical encounter it documents, and hospital.appointment's own gates
# (assignment, triage, financial clearance) are what move a visit into this
# state in the first place. Reusing that as the precondition means this model
# adds no fourth gate that could disagree with the three that already exist.
CONSULTATION_APPOINTMENT_STATE = "in_consultation"

# Frozen once the consultation is completed.
#
# Workflow and stamp fields (state, completed_at) are deliberately absent, so
# the completion transition -- when a later slice adds it -- can stamp itself
# without needing a context bypass. Same shape, and the same reasoning, as
# patient_evaluation.LOCKED_CLINICAL_FIELDS.
LOCKED_CLINICAL_FIELDS = frozenset(
    {
        "encounter_id",
        "doctor_id",
        "started_at",
        "presenting_complaint",
        "history_of_presenting_illness",
        "review_of_systems",
        "examination_findings",
        "assessment",
        "plan",
    }
)

# The narrative a doctor may write through the Doctor Desk. The API layer
# imports this rather than restating it, so the two cannot drift.
NARRATIVE_FIELDS = (
    "presenting_complaint",
    "history_of_presenting_illness",
    "review_of_systems",
    "examination_findings",
    "assessment",
    "plan",
)


class ConsultationConflict(UserError):
    """A save was refused because the record changed since the client read it.

    A DISTINCT TYPE ON PURPOSE. In Odoo, AccessError and ValidationError both
    subclass UserError, and the Doctor API maps a bare UserError to 422
    invalid_workflow_state. A stale-write conflict is neither an authorization
    failure nor an invalid transition -- it is a concurrency outcome the client
    can recover from by re-reading -- so it needs its own type to reach its own
    409 without the controller having to inspect a message string.

    Subclasses UserError rather than Exception so that ANY caller which does not
    know about it (the Odoo backend form, an RPC client, a future service) still
    sees a clean operator-facing refusal instead of a traceback.
    """


class HospitalConsultation(models.Model):
    _name = "hospital.consultation"
    _description = "Consultation"
    _order = "started_at desc, id desc"

    name = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default="New",
        help="Immutable consultation reference, allocated from a sequence.",
    )

    # ------------------------------------------------------------------
    # Identity. Everything below the encounter is DERIVED from it.
    # ------------------------------------------------------------------
    encounter_id = fields.Many2one(
        "hospital.encounter",
        string="Encounter",
        required=True,
        index=True,
        ondelete="restrict",
        copy=False,
        help="The episode of care this consultation documents. Set once, at "
        "creation; it can never be repointed.",
    )
    appointment_id = fields.Many2one(
        "hospital.appointment",
        string="Visit",
        related="encounter_id.appointment_id",
        store=True,
        readonly=True,
        index=True,
        help="Derived from the encounter. Stored so the doctor record rule can "
        "reach the assigned clinician without a traversal at query time.",
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        string="Patient",
        related="encounter_id.patient_id",
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="encounter_id.company_id",
        store=True,
        readonly=True,
    )
    doctor_id = fields.Many2one(
        "hospital.doctor",
        string="Consulting Physician",
        readonly=True,
        ondelete="restrict",
        index=True,
        help="Stamped from the visit's authoritative assignment when the "
        "consultation is opened. Never accepted from a client.",
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("completed", "Completed"),
        ],
        default="draft",
        required=True,
        copy=False,
        index=True,
        # No tracking=: this model does not inherit mail.thread, and Odoo warns
        # about the unknown parameter on every registry load rather than
        # silently ignoring it. State changes are recorded in
        # hospital.audit.log, which is where the rest of the clinical schema
        # keeps its trail anyway.
    )
    started_at = fields.Datetime(readonly=True, copy=False)
    # Present so the completion transition a later slice adds has somewhere to
    # write. NOTHING in this slice sets it, and no completion action exists.
    completed_at = fields.Datetime(readonly=True, copy=False)

    # ------------------------------------------------------------------
    # The note
    # ------------------------------------------------------------------
    presenting_complaint = fields.Text(
        help="Seeded ONCE from the completed nursing triage, then owned by the "
        "physician. It is a copy, never a mirror.",
    )
    history_of_presenting_illness = fields.Text()
    review_of_systems = fields.Text()
    examination_findings = fields.Text()
    assessment = fields.Text()
    plan = fields.Text()

    active = fields.Boolean(default=True)

    # Inverse of hospital.patient.diagnosis.consultation_id, added by this
    # module. ondelete="restrict" on that side means a consultation carrying
    # diagnoses cannot be deleted out from under them.
    diagnosis_ids = fields.One2many(
        "hospital.patient.diagnosis",
        "consultation_id",
        string="Diagnoses",
    )
    diagnosis_count = fields.Integer(
        compute="_compute_diagnosis_count",
        string="Diagnoses",
    )

    @api.depends("diagnosis_ids")
    def _compute_diagnosis_count(self):
        for consultation in self:
            consultation.diagnosis_count = len(consultation.diagnosis_ids)

    # ------------------------------------------------------------------
    # THE cardinality invariant.
    #
    # A unique INDEX, not a search-before-create. Two concurrent requests both
    # read "no consultation yet" before either writes, so an application check
    # cannot be the guarantee -- it can only be the friendly path. The advisory
    # lock in _lock_encounter_consultation() below makes the check-and-insert
    # one critical section; this constraint is what holds when something skips
    # that path entirely (RPC, import, a direct ORM create, a second backend).
    #
    # Same two-layer construction hospital.encounter uses for its one-active-
    # episode rule, and the same constraint shape as
    # hospital.patient.evaluation's unique(appointment_id).
    # ------------------------------------------------------------------
    _sql_constraints = [
        (
            "consultation_encounter_unique",
            "unique(encounter_id)",
            "A consultation already exists for this encounter.",
        ),
        (
            "consultation_name_company_unique",
            "unique(name, company_id)",
            "A consultation with this reference already exists for this company.",
        ),
    ]

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for consultation in self:
            patient = consultation.patient_id.display_name or ""
            if consultation.name and consultation.name != "New":
                consultation.display_name = (
                    "%s - %s" % (consultation.name, patient) if patient
                    else consultation.name
                )
            else:
                consultation.display_name = patient or "New Consultation"

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("encounter_id", "doctor_id")
    def _check_doctor_matches_assignment(self):
        """The consulting physician must be one the visit actually names.

        appointment_id and patient_id are stored related fields, so they cannot
        disagree with the encounter -- but doctor_id is a real column and could
        otherwise be pointed at any physician in the hospital by an RPC or import
        that bypasses get_or_create_for_appointment().

        Deliberately permissive about ABSENCE: a legacy encounter with neither a
        primary doctor nor an appointment doctor is a real shape in this
        database, and refusing it here would make such a visit undocumentable.
        What is refused is naming a DIFFERENT physician than the visit's own.
        """
        for consultation in self:
            doctor = consultation.doctor_id
            if not doctor:
                continue
            # sudo(): this is an integrity property of the DATA, not of the
            # acting user's read rights -- the same reasoning
            # hospital.laboratory.request._check_clinical_references_patient
            # documents. It only ever refuses; it grants nothing.
            encounter = consultation.sudo().encounter_id
            permitted = encounter.primary_doctor_id | encounter.appointment_id.doctor_id
            if not permitted:
                continue
            if doctor not in permitted:
                raise ValidationError(
                    "Consultation %s names %s as the consulting physician, but "
                    "encounter %s is assigned to %s. A consultation cannot be "
                    "attributed to a physician the visit does not name."
                    % (
                        consultation.display_name,
                        doctor.display_name,
                        encounter.name,
                        ", ".join(permitted.mapped("display_name")) or "(nobody)",
                    )
                )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.consultation.sequence") or "New"
                )
            vals.setdefault("started_at", fields.Datetime.now())
        consultations = super().create(vals_list)
        for consultation in consultations:
            consultation._log_audit(
                "create", "Consultation %s opened." % consultation.name
            )
        return consultations

    def write(self, vals):
        """The completion freeze, enforced for EVERY channel.

        This is live in this slice even though nothing here can complete a
        consultation. A record that reaches state='completed' by any route --
        a later slice, an administrator, a migration, a test fixture -- must
        already be immutable, or the freeze would be a rule that only the code
        which arrives later obeys.

        The encounter guard mirrors patient_evaluation.write(): the anchor is
        set once, at creation, and repointing it would silently move a written
        note onto another episode of care.
        """
        if "encounter_id" in vals:
            for consultation in self:
                if (
                    consultation.encounter_id
                    and consultation.encounter_id.id != vals["encounter_id"]
                ):
                    raise UserError(
                        "Consultation %s belongs to encounter %s. The encounter "
                        "cannot be changed once set."
                        % (consultation.display_name, consultation.encounter_id.name)
                    )

        locked = LOCKED_CLINICAL_FIELDS.intersection(vals)
        if locked:
            for consultation in self:
                if consultation.state == "completed":
                    raise UserError(
                        "Consultation %s is completed and its clinical content "
                        "is locked.\n\nBlocked fields: %s."
                        % (consultation.display_name, ", ".join(sorted(locked)))
                    )
        return super().write(vals)

    def unlink(self):
        """Consultations are clinical records. Archive, never delete."""
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            raise UserError(
                "Consultations are sensitive health records and cannot be "
                "deleted. Archive the record instead."
            )
        return super().unlink()

    def _log_audit(self, action_type, description):
        """Reuse the hospital-wide audit table rather than a private trail."""
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for consultation in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=consultation.patient_id.id,
                model_name=consultation._name,
                record_id=consultation.id,
                action_type=action_type,
                description=description,
            )

    # ------------------------------------------------------------------
    # Opening a consultation
    # ------------------------------------------------------------------
    def _lock_encounter_consultation(self, encounter):
        """Serialize get-or-create for ONE encounter.

        A SELECT-then-INSERT cannot stop two concurrent requests: both read "no
        consultation" before either writes, and the loser then hits the unique
        constraint and returns a 500 to a doctor who did nothing wrong. The
        advisory lock makes the check and the insert one critical section, and
        it is transaction-scoped so it releases on commit or rollback with no
        cleanup.

        Same mechanism, and the same key shape, as
        hospital.encounter._lock_patient_episode.
        """
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ["hospital.consultation.encounter:%s" % encounter.id],
        )

    @api.model
    def _assert_appointment_ready(self, appointment):
        """A consultation exists only for a visit that has actually started.

        This adds NO fourth gate. Assignment, nursing triage and financial
        clearance are enforced by hospital.appointment.action_start_consultation
        -- three independent model-layer checks across two modules -- and
        reaching 'in_consultation' is the proof that all three passed. Asking
        the same questions again here could only produce a different, and
        therefore wrong, answer.
        """
        appointment.ensure_one()
        if appointment.state != CONSULTATION_APPOINTMENT_STATE:
            raise UserError(
                "A consultation cannot be opened for visit %s: the visit is "
                "'%s'. Start the consultation first."
                % (appointment.appointment_code or appointment.id, appointment.state)
            )

    @api.model
    def _seed_presenting_complaint(self, appointment):
        """The nurse's chief complaint, COPIED ONCE at creation.

        NOT a related field and NOT a mirror, and the difference matters
        clinically in both directions: a later edit to the triage record must
        not rewrite what the physician documented, and a physician's rewording
        must not overwrite what the nurse recorded the patient said. The
        evaluation stays independently authoritative for the nursing account.

        Only a COMPLETED triage is copied. A draft evaluation is still being
        edited by the nurse, so copying it would freeze a half-written sentence
        into the physician's note.

        Read as the caller, with no sudo: the doctor's own record rule on
        hospital.patient.evaluation already reaches their own visits
        (appointment_id.doctor_id.user_id = me). If a caller genuinely cannot
        read it, the search returns nothing and this seeds nothing -- an empty
        field the physician fills in, which is the correct degradation.

        SEARCHED DIRECTLY RATHER THAN VIA appointment._latest_evaluation().
        That helper is defined in yoya_reception_bridge, which DEPENDS on this
        module -- so calling it would invert the dependency. It works once
        everything is loaded, and fails during this module's own post-migration,
        where the registry only contains modules up to and including this one.
        The lookup below uses hospital.patient.evaluation, which this module
        already extends and therefore legitimately owns.

        Ordered and limited rather than assuming one row: this module adds
        unique(appointment_id) to the evaluation, but rows predating that
        constraint may exist, and the newest completed triage is the right one.
        """
        evaluation = self.env["hospital.patient.evaluation"].search(
            [
                ("appointment_id", "=", appointment.id),
                ("state", "=", "done"),
            ],
            order="evaluation_date desc, id desc",
            limit=1,
        )
        if not evaluation:
            return False
        return (evaluation.chief_complaint or "").strip() or False

    @api.model
    def _resolve_doctor(self, appointment, encounter):
        """Authoritative assignment, server-side. Never a client value."""
        return appointment.doctor_id or encounter.primary_doctor_id

    @api.model
    def find_for_appointment(self, appointment):
        """THE read path. Never creates, never locks, never writes.

        Separate from get_or_create_for_appointment because the two have
        genuinely different contracts and collapsing them is what let a GET
        open a clinical record. This one answers "is there a note for this
        visit", and an empty recordset is a legitimate answer that the caller
        decides what to do about.

        Runs as the caller, so a consultation outside their record-rule scope
        reads as absent -- the same answer the ORM gives everywhere else, and
        the reason the API maps a missing consultation to an integrity error
        rather than to "not found for you".
        """
        appointment.ensure_one()
        encounter = appointment.encounter_id
        if not encounter:
            return self.browse()
        return self.search([("encounter_id", "=", encounter.id)], limit=1)

    @api.model
    def get_or_create_for_appointment(self, appointment):
        """THE way a consultation comes into existence. Idempotent.

        Takes a RECORD, never an id, so the caller has already resolved it
        through their own record rules and clinical scope -- there is no path
        here that turns a client-supplied integer into a record this user could
        not otherwise reach.

        Every ownership field is derived from that record. patient_id and
        appointment_id are stored related fields off the encounter and are not
        assignable at all; doctor_id is stamped from the visit's own assignment.

        Calling this twice returns the SAME row. Calling it concurrently for one
        encounter produces one row, not two.
        """
        appointment.ensure_one()
        self._assert_appointment_ready(appointment)

        # encounter_id is a non-stored compute_sudo field supplied by
        # hospital_billing, so a doctor resolves it without holding rights on
        # anything financial.
        encounter = appointment.encounter_id
        if not encounter:
            raise UserError(
                "Visit %s has no encounter, so there is no episode of care to "
                "document. This visit predates encounter tracking and cannot "
                "carry a consultation."
                % (appointment.appointment_code or appointment.id)
            )

        self._lock_encounter_consultation(encounter)

        # Runs as the caller: a consultation this user may not read must not be
        # silently handed to them, and must not be duplicated either -- which is
        # what the unique constraint below guarantees if the search comes back
        # empty for a rights reason rather than an existence one.
        existing = self.search([("encounter_id", "=", encounter.id)], limit=1)
        if existing:
            return existing

        return self.create(
            {
                "encounter_id": encounter.id,
                "doctor_id": self._resolve_doctor(appointment, encounter).id or False,
                "started_at": fields.Datetime.now(),
                "presenting_complaint": self._seed_presenting_complaint(appointment),
            }
        )

    # ------------------------------------------------------------------
    # Optimistic concurrency
    # ------------------------------------------------------------------
    def version_token(self):
        """The value a client must hand back to be allowed to overwrite.

        write_date is used rather than a bespoke revision column because Odoo
        already maintains it on every write, for every channel, with no way to
        forget it -- a hand-rolled counter would be correct only for the code
        paths that remembered to bump it.

        Serialized with isoformat() and compared as a STRING, which sidesteps
        every timezone and microsecond-truncation question a parse-and-compare
        would introduce: the token the client returns is byte-for-byte the one
        this method produced.

        ONE PROPERTY WORTH KNOWING. Odoo stamps write_date from PostgreSQL's
        now(), which is the TRANSACTION timestamp and is therefore constant for
        the whole transaction. Two writes inside ONE transaction leave the token
        unchanged, so this guards concurrent REQUESTS, not concurrent writes
        within a single request. That is exactly the scope required here: every
        HTTP request is its own transaction, and a single request saves once.
        A test that writes twice in one transaction will not see a conflict --
        which is why test_the_model_refuses_a_stale_version_independently_of_the_api
        simulates the other party's write at SQL level rather than by writing
        twice.
        """
        self.ensure_one()
        return self.write_date.isoformat() if self.write_date else ""

    def _assert_version(self, version):
        """Refuse a write built on a stale read.

        THE LOCK IS PART OF THE CHECK, NOT AN OPTIMISATION. Without SELECT ...
        FOR UPDATE two concurrent saves both read the same write_date, both
        find it current, and both write -- the second silently discarding the
        first doctor's paragraph. The row lock makes compare-and-write one
        critical section, and it is the same mechanism
        hospital.laboratory.request._evaluate_completion uses to serialize
        concurrent releases.

        Free text is never merged. Two people typing into one consultation is a
        situation only a human can resolve, and a machine merge of clinical
        narrative would fabricate a sentence neither clinician wrote.
        """
        self.ensure_one()
        if not isinstance(version, str) or not version:
            raise ConsultationConflict(
                "This consultation could not be saved because the editor did "
                "not supply a version. Reload the consultation and try again."
            )

        # FLUSH BEFORE THE RAW SQL, ALWAYS. The ORM defers writes, so a
        # transaction that has already touched this consultation may still be
        # holding them in memory -- and then this SELECT would lock and compare
        # a row that does not yet reflect what this same transaction has done.
        # Anything mixing raw SQL with the ORM has to do this; it is the exact
        # ordering that made the first version of the accompanying test pass
        # for the wrong reason.
        self.flush_recordset()

        self.env.cr.execute(
            "SELECT id FROM hospital_consultation WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        # Read through the ORM AFTER the lock so the comparison sees the
        # committed row rather than a value cached before the lock was taken.
        self.invalidate_recordset(["write_date"])
        if self.version_token() != version:
            raise ConsultationConflict(
                "This consultation was changed after you opened it, so your "
                "edits were not saved. Reload the consultation to see the "
                "current note, then re-apply your changes."
            )

    def save_narrative(self, values, version):
        """THE authoritative narrative write. Version-checked and freeze-aware.

        The whole mutation lives in the model so that every channel obeys it --
        the Doctor Desk, the Odoo form, an RPC client and any later service --
        rather than in a controller that only one of them goes through. Same
        reasoning patient_evaluation._assert_triage_minimum_data documents for
        putting the triage minimum in action_done().
        """
        self.ensure_one()

        unknown = set(values) - set(NARRATIVE_FIELDS)
        if unknown:
            # Defence in depth: the API layer already rejects these by name.
            raise AccessError(
                "Only the consultation narrative may be saved. Rejected: %s."
                % ", ".join(sorted(unknown))
            )

        if self.state == "completed":
            raise UserError(
                "Consultation %s is completed and its clinical content is "
                "locked. Only a Hospital Manager or Hospital System "
                "Administrator can change a completed consultation, and no "
                "amendment workflow exists yet." % self.display_name
            )

        self._assert_version(version)

        if values:
            self.write(values)
            self._log_audit("update", "Consultation %s narrative saved." % self.name)
        return self

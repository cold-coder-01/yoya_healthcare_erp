from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# Reopening a completed evaluation is a supervisory act. group_hospital_manager
# is implied by group_hospital_system_administrator, so checking both keeps the
# intent readable even though the second is redundant today.
MANAGER_GROUPS = (
    "hospital_management.group_hospital_manager",
    "hospital_management.group_hospital_system_administrator",
)

# Frozen once the evaluation is done. Workflow and stamp fields (state,
# started_at, completed_at, active) are deliberately absent so that
# action_done can stamp the completion time and action_reopen can unwind it
# without needing a context bypass.
LOCKED_CLINICAL_FIELDS = frozenset(
    {
        "patient_id",
        "physician_id",
        "appointment_id",
        "encounter_id",
        "evaluation_date",
        "weight",
        "height",
        "temperature",
        "heart_rate",
        "respiratory_rate",
        "systolic_bp",
        "diastolic_bp",
        "spo2",
        "rbs",
        "head_circumference",
        "pain_level",
        "pain_note",
        "chief_complaint",
        "triage_priority",
        "triage_notes",
        "assigned_nurse_id",
    }
)

# (field name, label, minimum, maximum). A falsy value means "not recorded"
# rather than zero -- the base model stores these as plain Floats, which
# default to 0.0, so an unrecorded temperature must not fail a 25-45 check.
VITAL_RANGES = (
    ("temperature", "Temperature", 25.0, 45.0, "°C"),
    ("heart_rate", "Heart rate", 20.0, 300.0, "bpm"),
    ("respiratory_rate", "Respiratory rate", 4.0, 80.0, "breaths/min"),
    ("systolic_bp", "Systolic blood pressure", 40.0, 300.0, "mmHg"),
    ("diastolic_bp", "Diastolic blood pressure", 20.0, 200.0, "mmHg"),
    ("spo2", "SpO2", 0.0, 100.0, "%"),
    ("weight", "Weight", 0.0, 500.0, "kg"),
    ("height", "Height", 0.0, 250.0, "cm"),
)

VITAL_RANGE_FIELDS = tuple(entry[0] for entry in VITAL_RANGES)


class HospitalPatientEvaluation(models.Model):
    _inherit = "hospital.patient.evaluation"

    encounter_id = fields.Many2one(
        "hospital.encounter",
        string="Encounter",
        ondelete="restrict",
        index=True,
        copy=False,
        help="Encounter this evaluation belongs to. Set once, at creation; it "
        "cannot be repointed afterwards.",
    )
    chief_complaint = fields.Text(
        string="Chief Complaint",
        help="Presenting complaint in the patient's own words.",
    )
    triage_priority = fields.Selection(
        [
            ("routine", "Routine"),
            ("urgent", "Urgent"),
            ("emergency", "Emergency"),
        ],
        string="Triage Priority",
        default="routine",
        required=True,
        index=True,
        tracking=True,
    )
    triage_notes = fields.Text(
        string="Triage Notes",
        help="Assessment notes recorded at triage.",
    )
    assigned_nurse_id = fields.Many2one(
        "res.users",
        string="Assigned Nurse",
        index=True,
        tracking=True,
        # The nurse record rule only reaches an appointment-less evaluation
        # through this field, so a creator who left it empty would lose sight
        # of the record the moment it is written. Odoo applies a default only
        # when the key is absent from vals, so an explicit value -- including
        # an explicit False -- is never overwritten.
        default=lambda self: self.env.user,
        help="Nurse who owns this evaluation. Stamped by Start Evaluation when empty.",
    )
    started_at = fields.Datetime(
        string="Started At",
        readonly=True,
        copy=False,
    )
    completed_at = fields.Datetime(
        string="Completed At",
        readonly=True,
        copy=False,
    )

    # PostgreSQL does not treat NULLs as equal, so a plain unique index is
    # already "at most one evaluation per appointment WHEN appointment_id is
    # present" -- evaluations with no appointment are unconstrained. This
    # mirrors the constraint hospital.encounter already places on the same
    # column (hospital_management/models/encounter.py:105-116).
    _sql_constraints = [
        (
            "evaluation_appointment_unique",
            "unique(appointment_id)",
            "An evaluation already exists for this appointment.",
        ),
    ]

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("patient_id", "appointment_id")
    def _check_evaluation_appointment_patient(self):
        for evaluation in self:
            appointment = evaluation.appointment_id
            if appointment and appointment.patient_id != evaluation.patient_id:
                raise ValidationError(
                    "Evaluation %s is for %s but appointment %s belongs to %s."
                    % (
                        evaluation.display_name,
                        evaluation.patient_id.display_name,
                        appointment.display_name,
                        appointment.patient_id.display_name,
                    )
                )

    @api.constrains("patient_id", "appointment_id", "encounter_id")
    def _check_evaluation_encounter(self):
        for evaluation in self:
            encounter = evaluation.encounter_id
            if not encounter:
                continue
            if encounter.patient_id != evaluation.patient_id:
                raise ValidationError(
                    "Evaluation %s is for %s but encounter %s belongs to %s."
                    % (
                        evaluation.display_name,
                        evaluation.patient_id.display_name,
                        encounter.name,
                        encounter.patient_id.display_name,
                    )
                )
            # Only enforced when both sides name an appointment. An evaluation
            # with no appointment may still hang off an encounter that has one
            # (a walk-in triaged into an existing encounter).
            if (
                evaluation.appointment_id
                and encounter.appointment_id
                and encounter.appointment_id != evaluation.appointment_id
            ):
                raise ValidationError(
                    "Evaluation %s points at appointment %s but encounter %s was "
                    "opened from appointment %s."
                    % (
                        evaluation.display_name,
                        evaluation.appointment_id.display_name,
                        encounter.name,
                        encounter.appointment_id.display_name,
                    )
                )

    @api.constrains(*VITAL_RANGE_FIELDS)
    def _check_vital_sign_ranges(self):
        for evaluation in self:
            for field_name, label, minimum, maximum, unit in VITAL_RANGES:
                value = evaluation[field_name]
                # 0.0 is the Float default and means "not recorded" here, so it
                # is skipped rather than range-checked.
                if not value:
                    continue
                if not minimum <= value <= maximum:
                    raise ValidationError(
                        "%s must be between %g and %g %s. Recorded: %g %s."
                        % (label, minimum, maximum, unit, value, unit)
                    )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def write(self, vals):
        if "encounter_id" in vals:
            for evaluation in self:
                if (
                    evaluation.encounter_id
                    and evaluation.encounter_id.id != vals["encounter_id"]
                ):
                    raise UserError(
                        "Evaluation %s is already linked to encounter %s. The "
                        "encounter cannot be changed once set."
                        % (evaluation.display_name, evaluation.encounter_id.name)
                    )

        locked = LOCKED_CLINICAL_FIELDS.intersection(vals)
        if locked:
            for evaluation in self:
                if evaluation.state == "done":
                    raise UserError(
                        "Evaluation %s is completed and its clinical content is "
                        "locked. A Hospital Manager must reopen it first.\n\n"
                        "Blocked fields: %s."
                        % (evaluation.display_name, ", ".join(sorted(locked)))
                    )
        return super().write(vals)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_start_evaluation(self):
        """Claim a draft evaluation and stamp the moment triage began.

        Deliberately introduces no new state -- the base model's draft/done/
        cancelled selection is left untouched. started_at being set is what
        distinguishes a claimed evaluation from a queued one.
        """
        for evaluation in self:
            if evaluation.state != "draft":
                raise UserError(
                    "Only draft evaluations can be started. %s is %s."
                    % (evaluation.display_name, evaluation.state)
                )

        now = fields.Datetime.now()
        for evaluation in self:
            values = {}
            # Idempotent: re-starting does not reset the original timestamp or
            # steal the evaluation from the nurse who already owns it.
            if not evaluation.started_at:
                values["started_at"] = now
            if not evaluation.assigned_nurse_id:
                values["assigned_nurse_id"] = self.env.user.id
            if values:
                evaluation.write(values)
                evaluation._create_audit_log("Patient evaluation started.")
        return True

    def _assert_triage_minimum_data(self):
        """The smallest clinical record a completed triage may consist of.

        Deliberately enforced HERE, in the authoritative completion method,
        rather than in a controller: /front-desk, the legacy /triage screen, the
        Odoo form-view button, an RPC client and a future workspace all reach
        completion through action_done(), and a rule that only one of them
        obeyed would not be a rule.

        SCOPE, and why it is this narrow. A triage with no presenting complaint
        records nothing a doctor or cashier can act on, so an empty evaluation
        must not be able to move a patient to Awaiting Cashier. Vitals are
        deliberately NOT required: a measurement can be genuinely unavailable at
        the entrance (no working thermometer, an uncooperative or distressed
        patient), and blocking completion on one would push staff into entering
        a fabricated number, which is worse than recording nothing.

        Save Draft is untouched: partial work is exactly what a draft is for.
        """
        for evaluation in self:
            if not (evaluation.chief_complaint or "").strip():
                raise ValidationError(
                    "A chief complaint is required before triage can be "
                    "completed for %s. Record what the patient is presenting "
                    "with, then complete the triage."
                    % evaluation.patient_id.display_name
                )
            # DEFENCE IN DEPTH, not the enforcement.
            #
            # triage_priority is required=True with default='routine' on a NOT
            # NULL column, so a PERSISTED evaluation cannot reach here without
            # one: emptying it raises an IntegrityError at flush long before
            # completion is attempted. This branch therefore only ever fires for
            # an in-memory record (a NewId built by an onchange, or a future
            # caller that relaxes the field). It is kept because it is free and
            # states the product rule where the rule is read, but the tests
            # deliberately do NOT manufacture a NULL to reach it -- see
            # test_front_desk_triage_api, section 4b.
            if not evaluation.triage_priority:
                raise ValidationError(
                    "A triage priority is required before triage can be "
                    "completed for %s." % evaluation.patient_id.display_name
                )

    def action_done(self):
        """Preserve the base completion (state + audit log), then stamp the time."""
        to_complete = self.filtered(lambda record: record.state == "draft")
        # Before super(), so a rejected completion leaves NOTHING behind: no
        # state change, no completed_at, no audit entry claiming a triage that
        # did not happen.
        to_complete._assert_triage_minimum_data()
        result = super().action_done()
        now = fields.Datetime.now()
        for evaluation in to_complete:
            if evaluation.state == "done" and not evaluation.completed_at:
                evaluation.write({"completed_at": now})
        return result

    def action_reopen(self):
        """Manager-only unlock of a completed evaluation."""
        self._check_reopen_rights()
        for evaluation in self:
            if evaluation.state != "done":
                raise UserError(
                    "Only completed evaluations can be reopened. %s is %s."
                    % (evaluation.display_name, evaluation.state)
                )

        for evaluation in self:
            evaluation.write({"state": "draft", "completed_at": False})
            evaluation._create_audit_log(
                "Patient evaluation reopened by %s." % self.env.user.display_name
            )
        return True

    def _check_reopen_rights(self):
        if not any(self.env.user.has_group(group) for group in MANAGER_GROUPS):
            raise UserError(
                "Only a Hospital Manager or Hospital System Administrator may "
                "reopen a completed evaluation."
            )

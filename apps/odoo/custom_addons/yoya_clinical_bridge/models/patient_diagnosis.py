"""Encounter and consultation linkage for the patient's diagnosis record.

WHY THIS EXTENDS RATHER THAN REPLACES
-------------------------------------
hospital.patient.diagnosis already models everything a diagnosis IS: the
disease, the type (primary / secondary / differential / history), severity,
clinical status, a free-text note, the physician, the appointment and an
archive flag. Three other models already point at it -- laboratory requests,
prescriptions and treatment plans all carry diagnosis_id -- and it is the
patient's longitudinal diagnostic history.

A parallel "encounter diagnosis" model would fork that history in two: the
timeline a clinician reads would be split across two tables with no way to
merge them, and the three existing foreign keys would keep pointing at only
half of it. So this module adds what the model LACKS -- an episode anchor, a
consultation anchor, a certainty axis and the integrity that makes them safe --
and touches nothing that already works.

WHAT IS DELIBERATELY NOT ADDED
------------------------------
No second free-text field: `notes` already exists and is the right place. No
second disease dictionary: hospital.disease is it. No ICD validation, because
hospital.disease.code is a free Char with no coding system and claiming
validation the model does not perform would be worse than claiming nothing.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# Everything a completed consultation freezes. Effectively the whole clinical
# record plus `active`, because ARCHIVING is how a diagnosis is removed here --
# see the note on remove_from_consultation(). A freeze that allowed archiving
# would let a completed consultation's diagnoses be emptied one by one.
DIAGNOSIS_LOCKED_FIELDS = frozenset(
    {
        "disease_id",
        "diagnosis_type",
        "certainty",
        "severity",
        "status",
        "notes",
        "diagnosis_date",
        "physician_id",
        "patient_id",
        "appointment_id",
        "encounter_id",
        "consultation_id",
        "active",
    }
)

# What a doctor may set through the Doctor Desk. Ownership is never in here:
# patient, encounter, appointment, consultation and physician are all derived
# server-side from the consultation record.
DIAGNOSIS_EDITABLE_FIELDS = ("diagnosis_type", "certainty", "severity", "status", "notes")

CERTAINTY_VALUES = ("provisional", "final")
DIAGNOSIS_TYPE_VALUES = ("primary", "secondary", "differential")
SEVERITY_VALUES = ("mild", "moderate", "severe", "critical")
STATUS_VALUES = ("active", "resolved", "chronic", "suspected")


class DiagnosisPrimaryConflict(UserError):
    """Refused because the consultation already has a primary diagnosis.

    A DISTINCT TYPE so the API can answer 409 with a stable code rather than
    the generic 422 a bare UserError produces. This is a recoverable clinical
    decision -- the doctor demotes or edits the existing primary first -- not an
    invalid workflow transition and not an authorization failure.

    Subclasses UserError so the Odoo form and any RPC caller that knows nothing
    about it still see a clean operator-facing refusal.
    """


class HospitalPatientDiagnosis(models.Model):
    _inherit = "hospital.patient.diagnosis"

    # ------------------------------------------------------------------
    # Episode linkage
    # ------------------------------------------------------------------
    encounter_id = fields.Many2one(
        "hospital.encounter",
        string="Encounter",
        index=True,
        ondelete="restrict",
        help="The episode of care this diagnosis was made in. Empty on "
        "historical rows recorded before encounter tracking.",
    )
    consultation_id = fields.Many2one(
        "hospital.consultation",
        string="Consultation",
        index=True,
        ondelete="restrict",
        help="The physician consultation that recorded this diagnosis. Empty "
        "for diagnoses entered outside a consultation, including every "
        "historical row.",
    )

    # ------------------------------------------------------------------
    # Certainty
    # ------------------------------------------------------------------
    certainty = fields.Selection(
        [
            ("provisional", "Provisional"),
            ("final", "Final"),
        ],
        string="Certainty",
        help="How settled the diagnosis is. Independent of clinical status: a "
        "diagnosis can be Provisional and Active, Final and Active, or Final "
        "and Resolved.",
        # NO `default=`, DELIBERATELY. Odoo backfills a new column's default
        # into every existing row, and stamping thousands of historical
        # diagnoses as "provisional" would assert something about them that
        # nobody recorded. New rows get their certainty from the create path
        # instead; legacy rows stay honestly empty.
    )

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------
    request_token = fields.Char(
        string="Request Token",
        copy=False,
        index=True,
        help="Client-supplied token that makes a retried submission return the "
        "existing diagnosis instead of creating a second one.",
    )

    def init(self):
        """Two PARTIAL unique indexes. Odoo's _sql_constraints cannot express these.

        ONE PRIMARY PER CONSULTATION
        A Python search-before-write cannot be the guarantee: two tabs both read
        "no primary yet" before either writes, and both succeed. The advisory
        lock in _assert_single_primary() makes the normal path serial and
        produces a friendly refusal; THIS INDEX is what holds when something
        races past it or skips the method entirely.

        It is partial on three conditions, and each one matters:
          consultation_id IS NOT NULL -- historical rows carry no consultation
                                        and must never be constrained by one
          diagnosis_type = 'primary'  -- secondary and differential are
                                        legitimately repeated
          active                      -- archiving the primary must FREE the
                                        slot, or a corrected mistake would
                                        block the replacement forever

        ONE ROW PER TOKEN **PER CONSULTATION**
        Scoped to (consultation_id, request_token), not to the token alone.

        A globally unique token would make one client's opaque string collide
        with another's across unrelated episodes of care: the second submission
        would be rejected by the database for a diagnosis it had nothing to do
        with, and the matching lookup in add_to_consultation() would hand back a
        DIFFERENT PATIENT'S row as though this request had created it. The
        server does not mint these tokens and cannot assume they are unique, so
        the guarantee has to be scoped to the thing the token actually
        identifies: one submission against one consultation.

        Partial on both columns, so the countless rows with no token and no
        consultation -- every historical row, every diagnosis entered from the
        Odoo form -- do not collide with each other on NULL.
        """
        super().init()
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                hospital_patient_diagnosis_primary_uniq
            ON hospital_patient_diagnosis (consultation_id)
            WHERE consultation_id IS NOT NULL
              AND diagnosis_type = 'primary'
              AND active
            """
        )
        # Drops the earlier globally-scoped index if this database ever ran an
        # intermediate build of this module. Harmless when absent, and it must
        # happen before the replacement is created so the two cannot coexist
        # with the old one still enforcing the wider rule.
        self.env.cr.execute(
            "DROP INDEX IF EXISTS hospital_patient_diagnosis_token_uniq"
        )
        self.env.cr.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
                hospital_patient_diagnosis_consultation_token_uniq
            ON hospital_patient_diagnosis (consultation_id, request_token)
            WHERE consultation_id IS NOT NULL
              AND request_token IS NOT NULL
            """
        )

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------
    @api.constrains(
        "consultation_id", "encounter_id", "appointment_id", "patient_id"
    )
    def _check_consultation_references(self):
        """A consultation diagnosis cannot belong to anyone else's episode.

        Record rules decide who may SEE a row; this decides whether the row is
        coherent at all, which is a different question and needs answering even
        when the writer is a manager who can see everything.

        sudo() ON THE CONSULTATION, NARROWLY. Reading the consultation's own
        patient and encounter is an integrity comparison, not a grant: it only
        ever refuses, and it never returns consultation data to the caller. The
        same reasoning, and the same shape, as
        hospital.laboratory.request._check_clinical_references_patient. Without
        it a doctor writing a legitimate row could be blocked by their own
        record rule on a consultation they are allowed to write to.

        LEGACY ROWS ARE SKIPPED, NOT REJECTED. A diagnosis with no consultation
        is the normal historical shape and stays valid forever.
        """
        for diagnosis in self:
            consultation = diagnosis.consultation_id
            if not consultation:
                continue
            authoritative = consultation.sudo()

            if diagnosis.patient_id != authoritative.patient_id:
                raise ValidationError(
                    "Diagnosis '%s' is recorded for %s but consultation %s "
                    "belongs to %s. A diagnosis cannot be filed against a "
                    "different patient."
                    % (
                        diagnosis.disease_id.display_name or "new",
                        diagnosis.patient_id.display_name,
                        authoritative.name,
                        authoritative.patient_id.display_name,
                    )
                )

            if not diagnosis.encounter_id:
                raise ValidationError(
                    "Diagnosis '%s' is linked to consultation %s but has no "
                    "encounter. Every consultation diagnosis belongs to the "
                    "consultation's episode of care."
                    % (diagnosis.disease_id.display_name or "new", authoritative.name)
                )

            if diagnosis.encounter_id != authoritative.encounter_id:
                raise ValidationError(
                    "Diagnosis '%s' points at encounter %s but consultation %s "
                    "documents encounter %s."
                    % (
                        diagnosis.disease_id.display_name or "new",
                        diagnosis.encounter_id.name,
                        authoritative.name,
                        authoritative.encounter_id.name,
                    )
                )

            if diagnosis.appointment_id != authoritative.appointment_id:
                # EXACT EQUALITY, INCLUDING THE MISSING CASE.
                #
                # The previous form required both sides to be set before it
                # compared them, so a diagnosis with NO appointment passed
                # silently even when its consultation documented one. That is
                # not a harmless gap: appointment_id is what the doctor record
                # rule traverses and what every downstream report groups a
                # visit's diagnoses by, so an unlinked row is invisible to the
                # rule and absent from the visit it was actually made in.
                #
                # The symmetric case is refused too. A consultation on a
                # standalone encounter has no appointment, and a diagnosis
                # claiming one would attach the visit's clinical record to a
                # scheduling artefact it does not belong to.
                raise ValidationError(
                    "Diagnosis '%s' is linked to visit %s but consultation %s "
                    "documents visit %s. A consultation diagnosis must carry "
                    "exactly the consultation's own visit."
                    % (
                        diagnosis.disease_id.display_name or "new",
                        diagnosis.appointment_id.display_name or "(none)",
                        authoritative.name,
                        authoritative.appointment_id.display_name or "(none)",
                    )
                )

    # ------------------------------------------------------------------
    # The primary invariant
    # ------------------------------------------------------------------
    @api.model
    def _lock_consultation_primary(self, consultation):
        """Serialize primary-diagnosis changes for ONE consultation.

        Transaction-scoped, so it releases on commit or rollback with no
        cleanup. Same mechanism and key shape as
        hospital.consultation._lock_encounter_consultation.
        """
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ["hospital.patient.diagnosis.primary:%s" % consultation.id],
        )

    def _assert_single_primary(self, consultation, diagnosis_type):
        """Refuse a second primary, with a message the doctor can act on.

        NO SILENT DEMOTION. Reclassifying somebody's existing primary to make
        room for a new one would rewrite a clinical judgement the doctor never
        asked to change, and would do it invisibly. The refusal names the
        disease currently holding the slot so the doctor can decide which one
        is actually primary.
        """
        if diagnosis_type != "primary" or not consultation:
            return

        self._lock_consultation_primary(consultation)

        domain = [
            ("consultation_id", "=", consultation.id),
            ("diagnosis_type", "=", "primary"),
            ("active", "=", True),
        ]
        if self.ids:
            domain.append(("id", "not in", self.ids))

        # sudo(): the invariant is one-primary-per-CONSULTATION, not
        # one-primary-per-consultation-that-this-user-can-see. A record rule
        # hiding the existing primary must not let a second one be created.
        existing = self.sudo().search(domain, limit=1)
        if existing:
            raise DiagnosisPrimaryConflict(
                "This consultation already has a primary diagnosis (%s). "
                "Change that one to secondary or differential first, or record "
                "this diagnosis as secondary."
                % (existing.disease_id.display_name or "unnamed")
            )

    # ------------------------------------------------------------------
    # The completion freeze
    # ------------------------------------------------------------------
    def _assert_not_frozen(self, fields_touched):
        """A completed consultation's diagnoses are read-only.

        Live NOW, although nothing in this slice can complete a consultation. A
        record that reaches state='completed' by any route -- a later slice, an
        administrator, a migration -- must already be immutable, or the freeze
        would be a rule only the code arriving later obeys. Exactly the
        discipline hospital.consultation.write() already applies to the note.
        """
        locked = DIAGNOSIS_LOCKED_FIELDS.intersection(fields_touched)
        if not locked:
            return
        for diagnosis in self:
            consultation = diagnosis.consultation_id
            if consultation and consultation.sudo().state == "completed":
                raise UserError(
                    "Diagnosis '%s' belongs to completed consultation %s and "
                    "its clinical content is locked.\n\nBlocked fields: %s."
                    % (
                        diagnosis.disease_id.display_name or "unnamed",
                        consultation.sudo().name,
                        ", ".join(sorted(locked)),
                    )
                )

    def write(self, vals):
        self._assert_not_frozen(vals.keys())

        # THE INVARIANT APPLIES TO THE RESULTING STATE, NOT TO THE KEYS WRITTEN.
        # Two edits reach "an active primary on this consultation": setting the
        # type to primary, and re-activating a row that is already primary. Both
        # are checked here, BEFORE super(), so the advisory lock spans the read
        # and the write rather than closing between them.
        touches_primary = "diagnosis_type" in vals or "active" in vals
        if touches_primary:
            for diagnosis in self:
                consultation = diagnosis.consultation_id
                if not consultation:
                    continue
                target_type = vals.get("diagnosis_type", diagnosis.diagnosis_type)
                target_active = vals.get("active", diagnosis.active)
                if target_type == "primary" and target_active:
                    diagnosis._assert_single_primary(consultation, "primary")

        return super().write(vals)

    def unlink(self):
        """Deletion is refused for a completed consultation's diagnoses.

        Note that the Doctor Desk never reaches this method at all: removal
        there is an ARCHIVE, for the reasons in remove_from_consultation().
        This guard exists for the system administrator, who does hold unlink.
        """
        for diagnosis in self:
            consultation = diagnosis.consultation_id
            if consultation and consultation.sudo().state == "completed":
                raise UserError(
                    "Diagnosis '%s' belongs to completed consultation %s and "
                    "cannot be deleted."
                    % (
                        diagnosis.disease_id.display_name or "unnamed",
                        consultation.sudo().name,
                    )
                )
        return super().unlink()

    # ------------------------------------------------------------------
    # Doctor Consultation Core service methods
    # ------------------------------------------------------------------
    @api.model
    def _validate_clinical_values(self, values):
        """Selection values, checked by name. Returns a clean dict."""
        clean = {}
        for key, allowed in (
            ("diagnosis_type", DIAGNOSIS_TYPE_VALUES),
            ("certainty", CERTAINTY_VALUES),
            ("severity", SEVERITY_VALUES),
            ("status", STATUS_VALUES),
        ):
            if key not in values:
                continue
            value = values[key]
            if value in (None, False, ""):
                clean[key] = False
                continue
            if value not in allowed:
                raise ValidationError(
                    "'%s' must be one of %s." % (key, ", ".join(allowed))
                )
            clean[key] = value

        if "notes" in values:
            note = values["notes"]
            if note in (None, False):
                clean["notes"] = False
            elif isinstance(note, str):
                clean["notes"] = note
            else:
                raise ValidationError("'notes' must be text.")
        return clean

    @api.model
    def add_to_consultation(self, consultation, disease, values, request_token=None):
        """THE way a diagnosis is recorded from the Doctor Desk.

        Takes RECORDS, never ids, so the caller has already resolved both
        through their own record rules. Every ownership field below is derived
        from the consultation; none is accepted from the client.

        IDEMPOTENT ON request_token. A double-clicked Add, or a retry after a
        dropped response, returns the diagnosis the first attempt created
        instead of filing the same disease twice. The token is the client's,
        which is what makes it identify the SUBMISSION rather than the content
        -- deliberately NOT a uniqueness rule on (consultation, disease), which
        would wrongly forbid a doctor from recording the same disease twice for
        genuinely different reasons and would silently merge two distinct
        clinical entries.
        """
        consultation.ensure_one()
        disease.ensure_one()

        if consultation.state != "draft":
            raise UserError(
                "Consultation %s is completed. Diagnoses can only be recorded "
                "while the consultation is open." % consultation.name
            )

        if request_token:
            # SCOPED TO THIS CONSULTATION. A global token lookup would let a
            # token minted for one consultation resolve to another's diagnosis
            # -- returning a row belonging to a different patient's episode as
            # though this submission had created it. Client tokens are opaque
            # strings this server does not mint and cannot assume are unique;
            # scoping the lookup means a collision can only ever be a replay of
            # the SAME submission against the SAME consultation.
            existing = self.search(
                [
                    ("consultation_id", "=", consultation.id),
                    ("request_token", "=", request_token),
                ],
                limit=1,
            )
            if existing:
                return existing

        clean = self._validate_clinical_values(values)
        if not clean.get("diagnosis_type"):
            raise ValidationError("A diagnosis type is required.")

        self._assert_single_primary(consultation, clean["diagnosis_type"])

        # Ownership, derived. The browser decides none of this.
        vals = {
            "patient_id": consultation.patient_id.id,
            "encounter_id": consultation.encounter_id.id,
            "appointment_id": consultation.appointment_id.id or False,
            "consultation_id": consultation.id,
            "physician_id": consultation.doctor_id.id or False,
            "disease_id": disease.id,
            "request_token": request_token or False,
        }
        vals.update(clean)
        # A newly recorded diagnosis is active unless the doctor says otherwise;
        # certainty stays whatever they chose and is never inferred from type.
        vals.setdefault("status", "active")
        vals.setdefault("certainty", "provisional")
        return self.create(vals)

    def update_from_consultation(self, values):
        """Edit a diagnosis while its consultation is still open."""
        self.ensure_one()
        consultation = self.consultation_id
        if not consultation:
            raise UserError(
                "This diagnosis was not recorded in a consultation and cannot "
                "be edited from the Doctor Desk."
            )
        if consultation.state != "draft":
            raise UserError(
                "Consultation %s is completed and its diagnoses are locked."
                % consultation.name
            )

        clean = self._validate_clinical_values(values)
        if clean:
            self.write(clean)
        return self

    def remove_from_consultation(self):
        """Remove a diagnosis from an open consultation, by ARCHIVING it.

        WHY ARCHIVE RATHER THAN DELETE. hospital.patient.diagnosis is the
        patient's longitudinal diagnostic record: three other models carry
        diagnosis_id foreign keys into it, and its own write() already logs an
        'archive' action to hospital.audit.log. The repository's stated policy
        for sensitive clinical records is the one hospital.consultation.unlink()
        states outright -- archive, never delete -- and the shipped ACLs agree,
        giving unlink to the system administrator alone.

        Archiving satisfies what the doctor actually needs (the entry leaves
        the consultation and stops being part of the diagnosis list) while
        keeping the audit trail intact and freeing the primary slot, because
        the unique index is partial on `active`.
        """
        self.ensure_one()
        consultation = self.consultation_id
        if not consultation:
            raise UserError(
                "This diagnosis was not recorded in a consultation and cannot "
                "be removed from the Doctor Desk."
            )
        if consultation.state != "draft":
            raise UserError(
                "Consultation %s is completed and its diagnoses are locked."
                % consultation.name
            )
        self.write({"active": False})
        return self

    @api.model
    def for_consultation(self, consultation):
        """The active diagnoses of one consultation, in clinical reading order."""
        if not consultation:
            return self.browse()
        return self.search(
            [("consultation_id", "=", consultation.id)],
            order="id asc",
        )

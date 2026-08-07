from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalLaboratoryResult(models.Model):
    _name = "hospital.laboratory.result"
    _description = "Laboratory Result"
    _order = "result_date desc, id desc"

    # Request states a result may be entered against. Derived from the existing
    # workflow, not invented: hospital_billing's PRE_COLLECTION_STATES codifies
    # that in 'draft'/'requested' no sample has been taken yet, and a result
    # without a sample is impossible in this workflow; 'cancelled' orders are
    # closed. That leaves the two states in which a sample exists.
    RESULT_ELIGIBLE_REQUEST_STATES = ("sample_collected", "in_progress")

    name = fields.Char(readonly=True, copy=False, default="New")
    request_id = fields.Many2one(
        "hospital.laboratory.request",
        required=True,
        ondelete="restrict",
        # UI convenience only — integrity is enforced by the constraints below.
        # Always state-scoped; patient-scoped as soon as a patient is known
        # ('=?' is a no-op while patient_id is empty).
        domain="[('state', 'in', ('sample_collected', 'in_progress')),"
        " ('patient_id', '=?', patient_id)]",
    )
    request_locked = fields.Boolean(
        compute="_compute_request_locked",
        help="True when the result was launched from a specific laboratory "
        "request (Results smart button) or has already been saved — the "
        "request is clinical provenance and may not be re-pointed.",
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
    )
    lab_technician_id = fields.Many2one(
        "res.users",
        default=lambda self: self.env.user,
    )
    result_date = fields.Date(
        default=fields.Date.context_today,
        required=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("entered", "Entered"),
            ("validated", "Validated"),
            ("released", "Released"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
    )
    line_ids = fields.One2many(
        "hospital.laboratory.result.line",
        "result_id",
        string="Result Lines",
    )
    interpretation = fields.Text()
    remarks = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for result in self:
            if result.name and result.name != "New":
                result.display_name = result.name
            elif result.patient_id:
                result.display_name = f"New Lab Result - {result.patient_id.display_name}"
            else:
                result.display_name = "New Lab Result"

    @api.depends("request_id")
    @api.depends_context("default_request_id")
    def _compute_request_locked(self):
        launched_from_request = bool(self.env.context.get("default_request_id"))
        for result in self:
            # Locked when opened from a request's Results smart button (context
            # carries default_request_id) or once the record has been saved.
            result.request_locked = launched_from_request or bool(result._origin.id or result.id)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        request_id = values.get("request_id") or self.env.context.get("default_request_id")
        if request_id:
            request = self.env["hospital.laboratory.request"].browse(request_id)
            if request.exists():
                # The request is authoritative: patient/physician/lines come
                # from it, never from stray defaults.
                values["patient_id"] = request.patient_id.id
                values["physician_id"] = request.physician_id.id
                if "line_ids" in fields_list and not values.get("line_ids"):
                    values["line_ids"] = self._prepare_result_lines_from_request(request)
        return values

    @api.onchange("request_id")
    def _onchange_request_id(self):
        """Rebuild everything from the newly selected request.

        Lines are always cleared and repopulated — a result must never retain
        lines that belong to a previously selected request.
        """
        if not self.request_id:
            self.patient_id = False
            self.physician_id = False
            self.line_ids = [(5, 0, 0)]
            return
        self.patient_id = self.request_id.patient_id
        self.physician_id = self.request_id.physician_id
        self.line_ids = [(5, 0, 0)] + self._prepare_result_lines_from_request(
            self.request_id
        )

    @api.model
    def _prepare_result_lines_from_request(self, request):
        """One result line per REQUEST LINE, carrying its exact identity.

        request_line_id is what makes a result occurrence traceable back to the
        specific ordered test. Matching by test_id alone is ambiguous the moment a
        request legitimately lists the same test twice.
        """
        return [
            (
                0,
                0,
                {
                    "request_line_id": line.id,
                    "test_id": line.test_id.id,
                    "sample_type": line.sample_type,
                    "sequence": line.sequence,
                },
            )
            for line in request.line_ids
        ]

    # ------------------------------------------------------------------
    # Server-side integrity — independent of the UI. @api.constrains fires on
    # create AND write and is not bypassed by sudo, a forged context, RPC or
    # direct ORM writes.
    # ------------------------------------------------------------------
    @api.constrains("request_id", "patient_id")
    def _check_patient_matches_request(self):
        for result in self:
            request = result.request_id
            if request and result.patient_id != request.patient_id:
                raise ValidationError(
                    "Laboratory result patient '%s' does not match the patient "
                    "'%s' of request %s. The request's patient is authoritative "
                    "— a result cannot be linked across patients."
                    % (
                        result.patient_id.display_name,
                        request.patient_id.display_name,
                        request.display_name,
                    )
                )

    @api.constrains("request_id")
    def _check_request_state_eligible(self):
        for result in self:
            request = result.request_id
            # Only gate the link when it is being (re)established on a live
            # result; historical results are untouched because constrains only
            # fires on create/write of the listed fields.
            if request and request.state not in self.RESULT_ELIGIBLE_REQUEST_STATES:
                raise ValidationError(
                    "A laboratory result can only be recorded against a request "
                    "whose sample exists (states: Sample Collected, In Progress)."
                    " Request %s is '%s'." % (request.display_name, request.state)
                )

    def _check_lines_consistent(self, require_linkage, require_complete=False):
        """Shared integrity sweep. With require_linkage=True (validation gate)
        every line must reference an ordered request line; otherwise unlinked
        legacy lines are tolerated but must still report an ordered test.

        With require_complete=True (Enter/Validate gate) the result must also
        mirror the request exactly — one line per ordered request line, none
        missing — and every line must carry a meaningful Result Value. Unit and
        reference range stay optional: qualitative tests may have neither."""
        for result in self:
            ordered_tests = result.request_id.line_ids.mapped("test_id")
            seen_request_lines = {}
            problems = []
            for line in result.line_ids:
                req_line = line.request_line_id
                if req_line:
                    if req_line.request_id != result.request_id:
                        problems.append(
                            "line '%s' references a request line of %s"
                            % (line.test_id.display_name,
                               req_line.request_id.display_name)
                        )
                    elif req_line.test_id != line.test_id:
                        problems.append(
                            "line test '%s' disagrees with ordered test '%s'"
                            % (line.test_id.display_name,
                               req_line.test_id.display_name)
                        )
                    if req_line in seen_request_lines:
                        problems.append(
                            "ordered test '%s' is reported twice"
                            % req_line.test_id.display_name
                        )
                    seen_request_lines[req_line] = True
                else:
                    if require_linkage:
                        problems.append(
                            "line '%s' is not linked to an ordered request line"
                            % line.test_id.display_name
                        )
                    elif line.test_id not in ordered_tests:
                        problems.append(
                            "test '%s' was never ordered on %s"
                            % (line.test_id.display_name,
                               result.request_id.display_name)
                        )
            if require_complete:
                covered = result.line_ids.mapped("request_line_id")
                for req_line in result.request_id.line_ids - covered:
                    problems.append(
                        "ordered test '%s' has no result line"
                        % req_line.display_name
                    )
                missing_values = [
                    line.test_id.display_name
                    for line in result.line_ids
                    if not (line.result_value or "").strip()
                ]
                if missing_values:
                    problems.append(
                        "no result value entered for: %s"
                        % ", ".join(missing_values)
                    )
            if problems:
                # Single atomic refusal — nothing is partially accepted.
                raise ValidationError(
                    "Laboratory result %s is inconsistent with request %s:\n- %s"
                    % (result.display_name, result.request_id.display_name,
                       "\n- ".join(problems))
                )

    @api.constrains("line_ids", "request_id")
    def _check_result_lines(self):
        self._check_lines_consistent(require_linkage=False)

    @api.constrains("state")
    def _check_state_requires_completeness(self):
        """A result cannot HOLD an entered/validated/released state with an
        incomplete or malformed line structure. Because this is a constraint it
        also catches forged state changes written directly through the ORM,
        sudo, RPC or a forged context — not only the action buttons."""
        for result in self:
            if result.state in ("entered", "validated", "released") and result.request_id:
                result._check_lines_consistent(
                    require_linkage=True, require_complete=True
                )

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = sequence.next_by_code(
                    "hospital.laboratory.result.sequence"
                ) or "New"
            if vals.get("request_id") and not vals.get("patient_id"):
                request = self.env["hospital.laboratory.request"].browse(vals["request_id"])
                vals["patient_id"] = request.patient_id.id
                vals.setdefault("physician_id", request.physician_id.id)
            if vals.get("request_id") and not vals.get("line_ids"):
                request = self.env["hospital.laboratory.request"].browse(vals["request_id"])
                vals["line_ids"] = self._prepare_result_lines_from_request(request)
        results = super().create(vals_list)
        for result in results:
            result._create_audit_log(
                action_type="create",
                description="Laboratory result created.",
                new_value=result._audit_summary(
                    ["name", "request_id", "patient_id", "physician_id", "result_date", "state"]
                ),
            )
        return results

    # States in which all clinical content is frozen. Only controlled workflow
    # transitions (state) and archiving (active) remain possible.
    FROZEN_STATES = ("validated", "released")
    # Clinical header content protected once the result is validated/released.
    _FROZEN_HEADER_FIELDS = (
        "request_id",
        "patient_id",
        "physician_id",
        "lab_technician_id",
        "result_date",
        "interpretation",
        "remarks",
        "line_ids",
    )

    def _frozen_field_changes(self, vals):
        """Frozen header fields in `vals` whose value actually differs from the
        stored one (line_ids commands always count as a change)."""
        self.ensure_one()
        changed = []
        for field_name in self._FROZEN_HEADER_FIELDS:
            if field_name not in vals:
                continue
            if field_name == "line_ids":
                changed.append(field_name)
                continue
            field = self._fields[field_name]
            current = self[field_name].id if field.type == "many2one" else self[field_name]
            new = vals[field_name]
            if field.type == "date" and new:
                new = fields.Date.to_date(new)
            if new != current:
                changed.append(field_name)
        return changed

    # ------------------------------------------------------------------
    # Validation is a PERMANENT clinical boundary.
    #
    # Billing delivery happens at validation, and a released result is a
    # clinical fact that has left the laboratory. Neither may return to an
    # editable state, nor be cancelled, until a proper amendment/retraction +
    # billing-adjustment workflow exists. This whitelist is the single source of
    # truth and is enforced in write(), so direct ORM writes, sudo, RPC and a
    # forged context obey it exactly like the buttons do.
    # ------------------------------------------------------------------
    _STATE_TRANSITIONS = {
        "draft": ("entered", "cancelled"),
        "entered": ("validated", "draft", "cancelled"),
        "validated": ("released",),
        "released": (),
        "cancelled": ("draft",),
    }

    def _check_state_transition(self, new_state):
        self.ensure_one()
        current = self.state
        if new_state == current:
            return
        if new_state in self._STATE_TRANSITIONS.get(current, ()):
            return
        if current in self.FROZEN_STATES:
            if new_state == "cancelled":
                raise UserError(
                    "Laboratory result %s is %s and cannot be cancelled.\n\n"
                    "Validation is the point at which the result became a "
                    "clinical fact and its laboratory charges were delivered to "
                    "billing. Cancelling it now would retract a reported result "
                    "and leave delivered charges without a corresponding "
                    "clinical record.\n\n"
                    "A validated result can only be withdrawn through a formal "
                    "amendment/retraction workflow with the matching billing "
                    "adjustment, which does not exist yet. Issue a new "
                    "laboratory request for repeat testing instead."
                    % (self.display_name, self.state)
                )
            raise UserError(
                "Laboratory result %s is %s and can never return to '%s'.\n\n"
                "Validation is a permanent clinical boundary: once a result has "
                "been validated (and its charges delivered), its content is "
                "final. Order a new laboratory request if the patient needs "
                "repeat testing." % (self.display_name, self.state, new_state)
            )
        raise UserError(
            "Invalid laboratory result transition: '%s' cannot become '%s'."
            % (current, new_state)
        )

    def _check_frozen_content(self, vals):
        """A validated/released result's clinical content is immutable through
        EVERY channel (UI, RPC, import, sudo, forged context). Only workflow
        state transitions and archiving pass. Corrections must go through the
        documented reset-to-draft / cancellation path."""
        for result in self:
            if result.state not in self.FROZEN_STATES:
                continue
            changed = result._frozen_field_changes(vals)
            if changed:
                labels = ", ".join(
                    result._fields[f].string or f for f in changed
                )
                raise UserError(
                    "Laboratory result %s is %s and its clinical content is "
                    "frozen. The following cannot be changed: %s. Reset it to "
                    "draft (managers/administrators) or cancel and recreate to "
                    "make corrections."
                    % (result.display_name, result.state, labels)
                )

    def write(self, vals):
        # The request is clinical provenance. Once the result has been saved it
        # can never be cleared or re-pointed — corrections go through the
        # existing cancel / recreate workflow. This guard runs before super()
        # for every channel (UI, RPC, import, sudo, forged context).
        if "request_id" in vals:
            for result in self:
                if vals["request_id"] != result.request_id.id:
                    raise UserError(
                        "The laboratory request on result %s is clinical "
                        "provenance and cannot be changed or cleared. Cancel "
                        "this result and create a new one from the correct "
                        "request." % result.display_name
                    )
        # Validation boundary: only whitelisted state transitions, every channel.
        if "state" in vals:
            for result in self:
                result._check_state_transition(vals["state"])
        # Freeze clinical content once validated/released.
        self._check_frozen_content(vals)
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            result.id: result._audit_summary(tracked_vals.keys())
            for result in self
        }
        result = super().write(vals)
        if tracked_vals and not self.env.context.get("skip_laboratory_result_write_audit"):
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Laboratory result archived."
                if action_type == "archive"
                else "Laboratory result updated."
            )
            for lab_result in self:
                lab_result._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(lab_result.id),
                    new_value=lab_result._audit_summary(tracked_vals.keys()),
                )
        return result

    def unlink(self):
        # Validated/released results are frozen clinical records — undeletable by
        # anyone, including system administrators.
        frozen = self.filtered(lambda r: r.state in self.FROZEN_STATES)
        if frozen:
            for result in frozen:
                result._create_audit_log(
                    action_type="delete_attempt",
                    description="Validated/released laboratory result deletion blocked.",
                    old_value=result._audit_summary(["name", "state"]),
                )
            raise UserError(
                "Validated or released laboratory results are frozen clinical "
                "records and cannot be deleted. Reset to draft or cancel them "
                "through the workflow first."
            )
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for result in self:
                result._create_audit_log(
                    action_type="delete_attempt",
                    description="Laboratory result deletion blocked.",
                    old_value=result._audit_summary(
                        ["name", "request_id", "patient_id", "physician_id", "state"]
                    ),
                )
            raise UserError(
                "Laboratory results are sensitive health records. Cancel or archive them instead of deleting."
            )
        return super().unlink()

    def action_mark_entered(self):
        for result in self:
            if result.state != "draft":
                raise UserError("Only draft laboratory results can be marked as entered.")
            # Atomic completeness gate: the structure must mirror the request
            # (one line per ordered test, none missing or duplicated) and every
            # line must carry a meaningful result value. One combined refusal
            # names every missing test — nothing is entered partially.
            result._check_lines_consistent(require_linkage=True, require_complete=True)
            old_state = result.state
            result.with_context(skip_laboratory_result_write_audit=True).write(
                {"state": "entered"}
            )
            result._log_state_change(old_state)

    def action_validate(self):
        for result in self:
            if result.state != "entered":
                raise UserError("Only entered laboratory results can be validated.")
            # Independent atomic gate: repeats the full structure AND
            # completeness check, so bypassing Mark Entered (or forging the
            # 'entered' state) can never validate blank results.
            result._check_lines_consistent(require_linkage=True, require_complete=True)
            old_state = result.state
            result.with_context(skip_laboratory_result_write_audit=True).write(
                {"state": "validated"}
            )
            result._log_state_change(old_state)

    def action_release(self):
        for result in self:
            if result.state != "validated":
                raise UserError("Only validated laboratory results can be released.")
            old_state = result.state
            result.with_context(skip_laboratory_result_write_audit=True).write(
                {"state": "released"}
            )
            result._log_state_change(old_state)
            # Release is the clinical handoff boundary: once it happens, the
            # request may now be clinically finished. Evaluated atomically after
            # the result is released; idempotent and a no-op unless EVERY
            # ordered test is covered by a released result.
            result.request_id._evaluate_completion()

    def action_cancel(self):
        """Cancellation is a PRE-VALIDATION action only.

        A validated/released result has already delivered its laboratory charges
        to billing; the transition guard refuses it with the controlled
        amendment/retraction explanation."""
        for result in self:
            result._check_state_transition("cancelled")
            old_state = result.state
            result.with_context(skip_laboratory_result_write_audit=True).write(
                {"state": "cancelled"}
            )
            result._log_state_change(old_state)

    def action_reset_to_draft(self):
        """Reopen a result for editing — only BEFORE validation.

        Permitted from Entered (correcting an entry that was never validated)
        and from Cancelled (which is itself reachable only from draft/entered).
        Validated and Released can never return to Draft: validation is a
        permanent clinical boundary."""
        for result in self:
            result._check_state_transition("draft")
            old_state = result.state
            result.with_context(skip_laboratory_result_write_audit=True).write(
                {"state": "draft"}
            )
            result._log_state_change(old_state)

    def _log_state_change(self, old_state):
        self._create_audit_log(
            action_type="state_change",
            description="Laboratory result state changed.",
            old_value=f"State: {old_state}",
            new_value=f"State: {self.state}",
        )

    def _audit_summary(self, field_names):
        values = []
        for field_name in field_names:
            if field_name in self._fields:
                value = self[field_name]
                if hasattr(value, "mapped"):
                    value = ", ".join(value.mapped("display_name"))
                values.append(f"{field_name}: {value}")
        return "; ".join(values)

    def _create_audit_log(self, action_type, description, old_value=False, new_value=False):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for result in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=result.patient_id.id,
                model_name=result._name,
                record_id=result.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )


class HospitalLaboratoryResultLine(models.Model):
    _name = "hospital.laboratory.result.line"
    _description = "Laboratory Result Line"
    _order = "sequence, id"

    result_id = fields.Many2one(
        "hospital.laboratory.result",
        required=True,
        ondelete="cascade",
    )
    request_line_id = fields.Many2one(
        "hospital.laboratory.request.line",
        string="Request Line",
        index=True,
        copy=False,
        ondelete="restrict",
        help="The exact ordered test this result reports on. Makes a result "
        "occurrence traceable when the same test is ordered more than once.",
    )
    test_id = fields.Many2one(
        "hospital.laboratory.test",
        required=True,
    )
    sample_type = fields.Selection(
        [
            ("blood", "Blood"),
            ("urine", "Urine"),
            ("stool", "Stool"),
            ("swab", "Swab"),
            ("sputum", "Sputum"),
            ("tissue", "Tissue"),
            ("other", "Other"),
        ],
    )
    result_value = fields.Char()
    unit = fields.Char()
    reference_range = fields.Char()
    abnormal_flag = fields.Selection(
        [
            ("normal", "Normal"),
            ("low", "Low"),
            ("high", "High"),
            ("critical", "Critical"),
            ("abnormal", "Abnormal"),
        ],
        default="normal",
    )
    notes = fields.Text()
    sequence = fields.Integer(default=10)

    @api.constrains("request_line_id", "result_id")
    def _check_request_line_belongs_to_request(self):
        for line in self:
            req_line = line.request_line_id
            if not req_line:
                continue  # historical lines are allowed to be empty
            result_request = line.result_id.request_id
            if result_request and req_line.request_id != result_request:
                raise ValidationError(
                    "Result line for '%s' references request line %s, which belongs to "
                    "laboratory request %s, not %s."
                    % (line.test_id.display_name, req_line.id,
                       req_line.request_id.display_name, result_request.display_name)
                )

    @api.constrains("test_id", "request_line_id", "result_id")
    def _check_test_was_ordered(self):
        """Every reported test must belong to an ordered request line — also
        when the line is created/edited directly (bypassing the parent form)."""
        for line in self:
            if line.request_line_id:
                continue  # linked lines are validated by the constraints below
            request = line.result_id.request_id
            if request and line.test_id not in request.line_ids.mapped("test_id"):
                raise ValidationError(
                    "Test '%s' was never ordered on laboratory request %s and "
                    "cannot be reported on its result."
                    % (line.test_id.display_name, request.display_name)
                )

    @api.constrains("request_line_id", "test_id")
    def _check_request_line_test_agrees(self):
        for line in self:
            req_line = line.request_line_id
            if not req_line:
                continue
            if req_line.test_id != line.test_id:
                raise ValidationError(
                    "Result line test '%s' does not agree with the ordered test '%s' on "
                    "the linked request line."
                    % (line.test_id.display_name, req_line.test_id.display_name)
                )

    @api.constrains("request_line_id", "result_id")
    def _check_no_duplicate_request_line(self):
        """One result reports each ordered line at most once. Repeat testing is
        supported by creating a NEW result for the same request (the existing
        model allows several results per request), never by silently duplicating
        lines inside one result."""
        for line in self:
            if not line.request_line_id:
                continue
            duplicates = line.result_id.line_ids.filtered(
                lambda l: l.request_line_id == line.request_line_id
            )
            if len(duplicates) > 1:
                raise ValidationError(
                    "Ordered test '%s' is reported more than once on result %s. "
                    "Record a repeat measurement as a new laboratory result."
                    % (line.request_line_id.test_id.display_name,
                       line.result_id.display_name)
                )

    @api.onchange("request_line_id")
    def _onchange_request_line_id(self):
        """Derive the test from the ordered line -- the request line is the truth."""
        req_line = self.request_line_id
        if not req_line:
            return
        self.test_id = req_line.test_id
        if req_line.sample_type:
            self.sample_type = req_line.sample_type

    # ------------------------------------------------------------------
    # Structural immutability: the line structure comes ONLY from the request.
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Sample type is DERIVED from the ordered request line (falling back
            # to the test default). It is never taken from user input, so the
            # specimen can never be redefined on the result.
            req_line_id = vals.get("request_line_id")
            if req_line_id:
                req_line = self.env["hospital.laboratory.request.line"].browse(
                    req_line_id
                )
                derived = req_line.sample_type or req_line.test_id.sample_type
                if derived:
                    vals["sample_type"] = derived
        lines = super().create(vals_list)
        for line in lines:
            result = line.result_id
            if not result.request_id:
                continue
            if result.state != "draft":
                raise UserError(
                    "Result lines cannot be added once the result has left "
                    "Draft. The line structure comes from the laboratory "
                    "request."
                )
            if not line.request_line_id:
                raise UserError(
                    "A result line must report on an ordered request line. "
                    "Manual free-standing lines are not allowed — the structure "
                    "comes from laboratory request %s."
                    % result.request_id.display_name
                )
        return lines

    def write(self, vals):
        real_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        # (A) Full clinical freeze: once the parent result is validated/released,
        # NO field of a result line may change — through any channel.
        if real_vals:
            frozen = self.filtered(
                lambda l: l.result_id.state in self.env[
                    "hospital.laboratory.result"
                ].FROZEN_STATES
            )
            if frozen:
                raise UserError(
                    "The laboratory result is validated/released; its result "
                    "lines are frozen and cannot be modified. Reset to draft or "
                    "cancel and recreate to make corrections."
                )
        # (B) Sample type is derived and read-only at EVERY state — the specimen
        # can never be redefined on the result.
        if "sample_type" in vals:
            for line in self:
                if vals["sample_type"] != line.sample_type:
                    raise UserError(
                        "Sample type is derived from the ordered request line "
                        "and cannot be changed on the result line."
                    )
        # (C) Provenance columns are frozen: a linked line can never be
        # re-pointed to another ordered line, and its test can never be swapped.
        # Filling an EMPTY request_line_id stays allowed — the documented healing
        # path for historical lines (billing's controlled fallback).
        if "request_line_id" in vals or "test_id" in vals:
            for line in self:
                if (
                    "request_line_id" in vals
                    and line.request_line_id
                    and vals["request_line_id"] != line.request_line_id.id
                ):
                    raise UserError(
                        "The ordered request line on a result line is clinical "
                        "provenance and cannot be changed or cleared. Cancel the "
                        "result and create a new one from the request."
                    )
                if (
                    "test_id" in vals
                    and line.test_id
                    and vals["test_id"] != line.test_id.id
                ):
                    raise UserError(
                        "The test on a result line cannot be changed. The line "
                        "structure comes from the laboratory request."
                    )
        return super().write(vals)

    @api.onchange("test_id")
    def _onchange_test_id(self):
        if self.test_id and self.test_id.sample_type:
            self.sample_type = self.test_id.sample_type

    def unlink(self):
        # Validated/released results are frozen: none of their lines (structural
        # or legacy) can be deleted by anyone.
        frozen = self.filtered(
            lambda l: l.result_id.state
            in self.env["hospital.laboratory.result"].FROZEN_STATES
        )
        if frozen:
            for line in frozen:
                line.result_id._create_audit_log(
                    action_type="delete_attempt",
                    description="Frozen laboratory result line deletion blocked.",
                    old_value=f"Test: {line.test_id.display_name}",
                )
            raise UserError(
                "The laboratory result is validated/released; its result lines "
                "are frozen and cannot be deleted."
            )
        # Structural lines (linked to an ordered request line) of a live result
        # can never be deleted — by anyone. The structure mirrors the request;
        # corrections go through cancel + recreate.
        structural = self.filtered(
            lambda l: l.request_line_id and l.result_id.state != "cancelled"
        )
        if structural:
            for line in structural:
                line.result_id._create_audit_log(
                    action_type="delete_attempt",
                    description="Structural laboratory result line deletion blocked.",
                    old_value=f"Test: {line.test_id.display_name}",
                )
            raise UserError(
                "Result lines mirror the ordered tests of the laboratory "
                "request and cannot be deleted. Cancel the result and create a "
                "new one if it is wrong."
            )
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for line in self:
                if line.result_id:
                    line.result_id._create_audit_log(
                        action_type="delete_attempt",
                        description="Laboratory result line deletion blocked.",
                        old_value=f"Test: {line.test_id.display_name}",
                    )
            raise UserError(
                "Laboratory result lines are sensitive health records. Cancel or archive the result instead of deleting lines."
            )
        return super().unlink()

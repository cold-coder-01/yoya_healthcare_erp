from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalLaboratoryTest(models.Model):
    _name = "hospital.laboratory.test"
    _description = "Laboratory Test"
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(tracking=True)
    category = fields.Selection(
        [
            ("hematology", "Hematology"),
            ("chemistry", "Chemistry"),
            ("microbiology", "Microbiology"),
            ("serology", "Serology"),
            ("urinalysis", "Urinalysis"),
            ("stool", "Stool"),
            ("pathology", "Pathology"),
            ("other", "Other"),
        ],
        tracking=True,
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
        tracking=True,
    )
    description = fields.Text()
    active = fields.Boolean(default=True)
    result_count = fields.Integer(
        compute="_compute_result_count",
        string="Results",
    )

    @api.depends("name", "code")
    def _compute_display_name(self):
        for test in self:
            if test.code:
                test.display_name = f"{test.code} - {test.name}"
            else:
                test.display_name = test.name or "New Test"

    def _compute_result_count(self):
        ResultLine = self.env["hospital.laboratory.result.line"]
        for test in self:
            if not test.id:
                test.result_count = 0
                continue
            test.result_count = len(
                ResultLine.search([("test_id", "=", test.id)]).mapped("result_id")
            )

    def action_view_results(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Laboratory Results",
            "res_model": "hospital.laboratory.result",
            "view_mode": "list,form",
            "target": "current",
            "domain": [("line_ids.test_id", "=", self.id)],
            "context": {},
        }


class HospitalLaboratoryRequest(models.Model):
    _name = "hospital.laboratory.request"
    _description = "Laboratory Request"
    _order = "request_date desc, id desc"

    name = fields.Char(readonly=True, copy=False, default="New")
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        required=True,
        tracking=True,
    )
    # Every clinical reference is scoped to THIS request's patient.
    #
    # The domain is a UI convenience only -- it stops a clinician picking another
    # patient's record from a dropdown that would otherwise list the whole database.
    # Integrity is enforced by _check_clinical_references_patient() below, because a
    # domain is not a security control: RPC, data import and direct ORM writes ignore it.
    #
    # NOTE: deliberately NO state filter. A completed ('done') appointment must remain
    # selectable -- ordering follow-up laboratory work from a finished consultation is
    # normal clinical practice, and APP0082 (done) is exactly that case.
    appointment_id = fields.Many2one(
        "hospital.appointment",
        tracking=True,
        domain="[('patient_id', '=', patient_id)]",
    )
    evaluation_id = fields.Many2one(
        "hospital.patient.evaluation",
        tracking=True,
        domain="[('patient_id', '=', patient_id)]",
    )
    diagnosis_id = fields.Many2one(
        "hospital.patient.diagnosis",
        tracking=True,
        domain="[('patient_id', '=', patient_id)]",
    )
    treatment_plan_id = fields.Many2one(
        "hospital.treatment.plan",
        tracking=True,
        domain="[('patient_id', '=', patient_id)]",
    )
    request_date = fields.Date(
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    priority = fields.Selection(
        [
            ("routine", "Routine"),
            ("urgent", "Urgent"),
            ("stat", "STAT"),
        ],
        default="routine",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("requested", "Requested"),
            ("sample_collected", "Sample Collected"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    line_ids = fields.One2many(
        "hospital.laboratory.request.line",
        "request_id",
        string="Requested Tests",
    )
    result_ids = fields.One2many(
        "hospital.laboratory.result",
        "request_id",
        string="Laboratory Results",
    )
    result_count = fields.Integer(
        compute="_compute_result_count",
        string="Results",
    )
    clinical_notes = fields.Text()
    instructions = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for request in self:
            if request.name and request.name != "New":
                request.display_name = request.name
            elif request.patient_id:
                request.display_name = f"Lab Request - {request.patient_id.display_name}"
            else:
                request.display_name = "New Lab Request"

    def _compute_result_count(self):
        for request in self:
            if not request.id:
                request.result_count = 0
                continue
            request.result_count = self.env["hospital.laboratory.result"].search_count(
                [("request_id", "=", request.id)]
            )

    # ------------------------------------------------------------------
    # Cross-patient integrity
    # ------------------------------------------------------------------
    _CLINICAL_REFS = (
        ("appointment_id", "Appointment"),
        ("evaluation_id", "Evaluation"),
        ("diagnosis_id", "Diagnosis"),
        ("treatment_plan_id", "Treatment Plan"),
    )

    @api.constrains("patient_id", "appointment_id", "evaluation_id",
                    "diagnosis_id", "treatment_plan_id")
    def _check_clinical_references_patient(self):
        """The REQUEST's patient is authoritative.

        Enforced on every write path -- UI, RPC, import, direct ORM -- because the
        field domains above are only a dropdown filter and are trivially bypassed.
        We never silently re-point the request at the reference's patient: a mismatch
        is a mistake, and it is rejected.
        """
        # sudo(): an integrity invariant is a property of the DATA, not of the acting
        # user's read rights. A lab technician has no ACL on hospital.appointment, and
        # must still be able to collect a sample without this check raising AccessError.
        for request in self.sudo():
            if not request.patient_id:
                continue
            for fname, label in self._CLINICAL_REFS:
                ref = request[fname]
                if not ref:
                    continue
                if ref.patient_id != request.patient_id:
                    raise ValidationError(
                        "%s '%s' belongs to patient %s, but this laboratory request is "
                        "for %s.\n\nA clinical record from another patient cannot be "
                        "attached to this request."
                        % (label, ref.display_name,
                           ref.patient_id.display_name or "(none)",
                           request.patient_id.display_name)
                    )

    @api.onchange("patient_id")
    def _onchange_patient_id_clear_refs(self):
        """Drop any clinical reference that no longer belongs to the chosen patient.

        The patient drives the request, never the other way round.
        """
        for fname, _label in self._CLINICAL_REFS:
            ref = self[fname]
            if ref and ref.patient_id != self.patient_id:
                self[fname] = False

    def action_view_results(self):
        self.ensure_one()
        LabResult = self.env["hospital.laboratory.result"]
        latest_result = LabResult.search(
            [("request_id", "=", self.id)],
            order="result_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Laboratory Result",
            "res_model": "hospital.laboratory.result",
            "view_mode": "form",
            "target": "current",
            "domain": [("request_id", "=", self.id)],
            "context": {
                "default_request_id": self.id,
                "default_patient_id": self.patient_id.id,
                "default_physician_id": self.physician_id.id,
            },
        }
        if latest_result:
            action["res_id"] = latest_result.id
        return action

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = sequence.next_by_code(
                    "hospital.laboratory.request.sequence"
                ) or "New"
        requests = super().create(vals_list)
        for request in requests:
            request._create_audit_log(
                action_type="create",
                description="Laboratory request created.",
                new_value=request._audit_summary(
                    ["name", "patient_id", "physician_id", "request_date", "priority", "state"]
                ),
            )
        return requests

    def write(self, vals):
        # Terminal-state integrity: only whitelisted transitions, and 'completed'
        # only when the completion rule actually passes. Runs before super() so
        # UI, RPC, import, sudo and forged contexts are all bound by it.
        if "state" in vals:
            for request in self:
                request._check_state_transition(vals["state"])
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            request.id: request._audit_summary(tracked_vals.keys())
            for request in self
        }
        result = super().write(vals)
        if tracked_vals and not self.env.context.get("skip_laboratory_request_write_audit"):
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Laboratory request archived."
                if action_type == "archive"
                else "Laboratory request updated."
            )
            for request in self:
                request._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(request.id),
                    new_value=request._audit_summary(tracked_vals.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for request in self:
                request._create_audit_log(
                    action_type="delete_attempt",
                    description="Laboratory request deletion blocked.",
                    old_value=request._audit_summary(
                        ["name", "patient_id", "physician_id", "priority", "state"]
                    ),
                )
            raise UserError(
                "Laboratory requests are sensitive health records. Cancel or archive them instead of deleting."
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # Completion — the terminal successful outcome (Task 31G)
    #
    # A request is clinically finished when every ordered test has been RELEASED
    # (release, not validation, is the clinical handoff boundary). 'completed'
    # is terminal and separate from 'cancelled', the exceptional outcome.
    # ------------------------------------------------------------------
    TERMINAL_STATES = ("completed", "cancelled")
    _STATE_TRANSITIONS = {
        "draft": ("requested", "cancelled"),
        "requested": ("sample_collected", "cancelled"),
        "sample_collected": ("in_progress",),
        "in_progress": ("completed",),
        "completed": (),
        "cancelled": ("draft",),
    }

    def _completion_blockers(self):
        """Reasons this request may NOT be completed; empty list == completable.

        Request lines have no archive flag, so every ordered line counts. Each
        must be covered EXACTLY ONCE by a result line of an active, released
        result belonging to this same request and patient.
        """
        self.ensure_one()
        reasons = []
        if self.state == "cancelled":
            reasons.append("the request is cancelled")
        req_lines = self.line_ids
        if not req_lines:
            reasons.append("the request has no ordered tests")
            return reasons

        # Search by request_line_id so a result line of ANOTHER request that
        # points at our ordered line is also caught.
        covering_lines = self.env["hospital.laboratory.result.line"].search(
            [("request_line_id", "in", req_lines.ids)]
        )
        for req_line in req_lines:
            covering = covering_lines.filtered(
                lambda l, rl=req_line: l.request_line_id == rl
            ).filtered(lambda l: l.result_id.active)
            if not covering:
                reasons.append("'%s' has no released result" % req_line.display_name)
                continue
            if len(covering) > 1:
                reasons.append(
                    "'%s' is covered by %d result lines (expected exactly one)"
                    % (req_line.display_name, len(covering))
                )
                continue
            result = covering.result_id
            if result.request_id != self:
                reasons.append(
                    "'%s' is covered by result %s of a different request"
                    % (req_line.display_name, result.display_name)
                )
                continue
            if result.patient_id != self.patient_id:
                reasons.append(
                    "'%s' is covered by a result of a different patient"
                    % req_line.display_name
                )
                continue
            if result.state != "released":
                reasons.append(
                    "'%s' result %s is %s, not released"
                    % (req_line.display_name, result.display_name, result.state)
                )
        return reasons

    def _evaluate_completion(self):
        """Complete the request when the rule passes. Idempotent + concurrency safe.

        Completion is a pure clinical state transition: it delivers no charges,
        creates no receipt/allocation/invoice/journal/fiscal record, no stock
        consumption or movement, and never touches released clinical values.
        """
        for request in self:
            if not request.id:
                continue
            # Serialize concurrent releases of sibling results on this request.
            self.env.cr.execute(
                "SELECT state FROM hospital_laboratory_request WHERE id = %s "
                "FOR UPDATE",
                (request.id,),
            )
            row = self.env.cr.fetchone()
            current = row[0] if row else False
            if current in self.TERMINAL_STATES:
                continue  # already terminal — idempotent no-op
            request.invalidate_recordset(["state"])
            if request._completion_blockers():
                continue
            old_state = request.state
            request.with_context(
                skip_laboratory_request_write_audit=True
            ).write({"state": "completed"})
            request._log_state_change(old_state)

    def _check_state_transition(self, new_state):
        """Whitelist-driven, enforced in write() for EVERY channel."""
        self.ensure_one()
        current = self.state
        if new_state == current:
            return
        allowed = self._STATE_TRANSITIONS.get(current, ())
        if new_state not in allowed:
            if current == "completed":
                raise UserError(
                    "Laboratory request %s is Completed. All of its ordered "
                    "tests have released results and their charges have been "
                    "delivered, so it cannot be reopened or cancelled. A "
                    "correction requires a formal amendment/retraction workflow "
                    "(not yet available); order a new request for repeat "
                    "testing." % self.display_name
                )
            raise UserError(
                "Invalid laboratory request transition: '%s' cannot become '%s'."
                % (current, new_state)
            )
        if new_state == "completed":
            # Completion can never be forced — the rule is re-checked here, so
            # ORM/RPC/sudo/forged-context writes must satisfy it too.
            blockers = self._completion_blockers()
            if blockers:
                raise UserError(
                    "Laboratory request %s cannot be completed yet:\n- %s"
                    % (self.display_name, "\n- ".join(blockers))
                )
        if new_state == "cancelled":
            committed = self.env["hospital.laboratory.result"].search(
                [
                    ("request_id", "=", self.id),
                    ("state", "in", ("validated", "released")),
                ],
                limit=1,
            )
            if committed:
                raise UserError(
                    "Laboratory request %s cannot be cancelled: result %s is "
                    "already %s and its charges have been delivered. Cancelling "
                    "would retract a reported clinical result. A formal "
                    "amendment/retraction workflow is required."
                    % (self.display_name, committed.display_name, committed.state)
                )

    def action_confirm_request(self):
        """Confirm the lab request and move to requested state."""
        for request in self:
            if request.state != "draft":
                raise UserError("Only draft requests can be confirmed.")
            old_state = request.state
            request.with_context(skip_laboratory_request_write_audit=True).write(
                {"state": "requested"}
            )
            request._log_state_change(old_state)

    def action_mark_sample_collected(self):
        """Mark sample as collected."""
        for request in self:
            if request.state != "requested":
                raise UserError("Only requested lab requests can be marked as sample collected.")
            old_state = request.state
            request.with_context(skip_laboratory_request_write_audit=True).write(
                {"state": "sample_collected"}
            )
            request._log_state_change(old_state)

    def action_mark_in_progress(self):
        """Mark request as in progress."""
        for request in self:
            if request.state != "sample_collected":
                raise UserError(
                    "Only lab requests with samples collected can be marked as in progress."
                )
            old_state = request.state
            request.with_context(skip_laboratory_request_write_audit=True).write(
                {"state": "in_progress"}
            )
            request._log_state_change(old_state)

    def action_cancel(self):
        """Cancel the lab request — pre-result outcomes only.

        The transition guard additionally refuses cancellation of a Completed
        request, or of any request that already carries a validated/released
        result whose charges have been delivered."""
        for request in self:
            request._check_state_transition("cancelled")
            old_state = request.state
            request.with_context(skip_laboratory_request_write_audit=True).write(
                {"state": "cancelled"}
            )
            request._log_state_change(old_state)

    def action_reset_to_draft(self):
        """Reset a cancelled request back to draft.

        Routed through the transition guard so a Completed request reports the
        terminal-state explanation rather than a generic message."""
        for request in self:
            request._check_state_transition("draft")
            old_state = request.state
            request.with_context(skip_laboratory_request_write_audit=True).write(
                {"state": "draft"}
            )
            request._log_state_change(old_state)

    def _log_state_change(self, old_state):
        self._create_audit_log(
            action_type="state_change",
            description="Laboratory request state changed.",
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
        for request in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=request.patient_id.id,
                model_name=request._name,
                record_id=request.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )


class HospitalLaboratoryRequestLine(models.Model):
    _name = "hospital.laboratory.request.line"
    _description = "Laboratory Request Line"
    _order = "sequence, id"

    request_id = fields.Many2one(
        "hospital.laboratory.request",
        required=True,
        ondelete="cascade",
    )
    test_id = fields.Many2one(
        "hospital.laboratory.test",
        required=True,
        tracking=True,
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
        tracking=True,
    )
    special_instruction = fields.Char()
    sequence = fields.Integer(default=10)

    @api.depends("test_id.code", "test_id.name")
    def _compute_display_name(self):
        """Human-readable identity ('CBC - Complete Blood Count'), never the
        technical ORM reference. Shown wherever a request line is referenced
        (result lines, billing messages, dropdowns)."""
        for line in self:
            test = line.test_id
            if test and test.code:
                line.display_name = f"{test.code} - {test.name}"
            elif test:
                line.display_name = test.name
            else:
                line.display_name = "New Ordered Test"

    @api.onchange("test_id")
    def _onchange_test_id(self):
        """Set sample_type from test when test is selected."""
        if self.test_id and self.test_id.sample_type:
            self.sample_type = self.test_id.sample_type

    # ------------------------------------------------------------------
    # Structural integrity of the ordered set (Task 31G)
    #
    # The ordered tests are frozen once the request leaves Draft (consistent
    # with the established billing rule that charges are raised at confirmation)
    # or as soon as any result exists. Otherwise the ordered set and the results
    # covering it — and the charges raised for it — could silently diverge.
    # ------------------------------------------------------------------
    def _assert_structure_editable(self, action):
        for line in self:
            request = line.request_id
            if not request:
                continue
            if request.state != "draft":
                raise UserError(
                    "The ordered tests of laboratory request %s can no longer be "
                    "%s: the request is '%s' and its charges have been raised. "
                    "Cancel it and create a new request if the order is wrong."
                    % (request.display_name, action, request.state)
                )
            if request.result_ids:
                raise UserError(
                    "The ordered tests of laboratory request %s can no longer be "
                    "%s: laboratory results already exist for it."
                    % (request.display_name, action)
                )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._assert_structure_editable("added")
        return lines

    def write(self, vals):
        if "test_id" in vals or "request_id" in vals:
            changed = self.filtered(
                lambda l: (
                    "test_id" in vals and vals["test_id"] != l.test_id.id
                )
                or ("request_id" in vals and vals["request_id"] != l.request_id.id)
            )
            changed._assert_structure_editable("changed")
        return super().write(vals)

    def unlink(self):
        # Structural freeze applies to everyone once the request left draft or
        # results exist — deleting an ordered test would strand its charge and
        # its result line.
        self._assert_structure_editable("deleted")
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for line in self:
                if line.request_id:
                    line.request_id._create_audit_log(
                        action_type="delete_attempt",
                        description="Laboratory request line deletion blocked.",
                        old_value=f"Test: {line.test_id.display_name}",
                    )
            raise UserError(
                "Laboratory request lines are sensitive health records. Archive or cancel the request instead of deleting lines."
            )
        return super().unlink()

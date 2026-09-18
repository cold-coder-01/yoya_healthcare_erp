import contextvars
from contextlib import contextmanager

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

# ---------------------------------------------------------------------------
# RESULT WORKFLOW AUTHORITY (Radiology Slice 3)
# ---------------------------------------------------------------------------
# A radiology report moves, and is attributed, ONLY through this model's own
# workflow methods. Before this guard anyone holding write access -- a desk
# role, an RPC client, sudo code -- could write {"state": "released"} on an
# empty draft, re-point a report at another request or patient, name any user
# as the reporting radiologist, or pass skip_radiology_result_write_audit in
# the context to write into a released report.
#
# THE SAME DESIGN AS THE REQUEST'S STATE AUTHORITY (radiology_request.py): a
# ContextVar, which no RPC payload can set, rather than a context flag, which
# every RPC payload can. The capability is raised around ONE workflow write and
# always released in a `finally`.
#
# WHAT IT OPENS, AND WHAT IT DOES NOT. It admits the writes only a workflow
# method may make: `state`, `radiologist_id`, and billing's healing of an empty
# `request_line_id` at release. It never opens the provenance fields
# (request, patient, ordering physician) and never opens the clinical content
# of a report that has left draft: nothing in the workflow needs to change
# either, so nothing may.
_result_workflow_capability_var = contextvars.ContextVar(
    "hospital_radiology_result_workflow_capability", default=False
)


@contextmanager
def _result_workflow_capability():
    token = _result_workflow_capability_var.set(True)
    try:
        yield
    finally:
        _result_workflow_capability_var.reset(token)


def has_result_workflow_capability():
    return _result_workflow_capability_var.get()


# The roles that author a report: write its findings, impression and
# recommendations, and mark it entered. The Radiology Technician is NOT among
# them -- Slice 0A already withheld result-line write from that role on the
# ground that "editing the per-exam findings is reporting" -- and keeps result
# write only so imaging can be attached to the report.
REPORT_AUTHOR_GROUPS = (
    "hospital_radiology.group_hospital_radiologist",
    "hospital_management.group_hospital_manager",
    "hospital_management.group_hospital_system_administrator",
)

# A report is started only for a study that has begun.
RESULT_ENTRY_REQUEST_STATES = ("in_progress",)

# The clinical narrative. Written only by a report author.
CLINICAL_TEXT_FIELDS = ("findings", "impression", "recommendations")

# Frozen once the report leaves draft (entered, validated, released,
# cancelled). A cancelled report returns to draft only through
# action_reset_to_draft().
FROZEN_CONTENT_FIELDS = CLINICAL_TEXT_FIELDS + ("result_date", "line_ids")

# Provenance: which request, which patient, which ordering physician. Set at
# creation from the request and never changed afterwards, by anyone.
PROVENANCE_FIELDS = ("request_id", "patient_id", "physician_id")

STATE_WRITE_REFUSED_MESSAGE = (
    "Radiology report %s cannot be moved from '%s' to '%s' by editing its "
    "state. Use the report's workflow actions, which run the checks that "
    "transition requires."
)


def _blank(value):
    return not (value or "").strip()


class HospitalRadiologyResult(models.Model):
    _name = "hospital.radiology.result"
    _description = "Radiology Result"
    _order = "result_date desc, id desc"

    name = fields.Char(readonly=True, copy=False, default="New")
    request_id = fields.Many2one(
        "hospital.radiology.request",
        required=True,
        ondelete="restrict",
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
    # NO DEFAULT (Radiology Slice 3). It used to default to whoever created the
    # record, so a technician opening the report container was recorded as the
    # interpreting radiologist. It is now set by action_mark_entered() to the
    # report author who enters it, and to nothing before that.
    radiologist_id = fields.Many2one(
        "res.users",
        string="Radiologist / Reporter",
        copy=False,
        readonly=True,
        help="The report author who marked this report entered. Set by the "
        "workflow; it cannot be chosen.",
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
        copy=False,
    )
    line_ids = fields.One2many(
        "hospital.radiology.result.line",
        "result_id",
        string="Result Lines",
    )
    image_ids = fields.One2many(
        "hospital.radiology.image",
        "result_id",
        string="Imaging",
        help="Clinical files attached to this report: JPEG, PNG or PDF. The "
        "set is frozen once the result is validated.",
    )
    image_count = fields.Integer(compute="_compute_image_count", string="Images")
    findings = fields.Text()
    impression = fields.Text()
    recommendations = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("image_ids")
    def _compute_image_count(self):
        for result in self:
            result.image_count = len(result.image_ids)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for result in self:
            if result.name and result.name != "New":
                result.display_name = result.name
            elif result.patient_id:
                result.display_name = f"Radiology Result - {result.patient_id.display_name}"
            else:
                result.display_name = "New Radiology Result"

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        request_id = values.get("request_id") or self.env.context.get("default_request_id")
        if request_id:
            request = self.env["hospital.radiology.request"].browse(request_id)
            if request.exists():
                values.setdefault("patient_id", request.patient_id.id)
                values.setdefault("physician_id", request.physician_id.id)
                if "line_ids" in fields_list and not values.get("line_ids"):
                    values["line_ids"] = self._prepare_result_lines_from_request(request)
        return values

    @api.onchange("request_id")
    def _onchange_request_id(self):
        if not self.request_id:
            return
        self.patient_id = self.request_id.patient_id
        self.physician_id = self.request_id.physician_id
        if not self.line_ids:
            self.line_ids = self._prepare_result_lines_from_request(self.request_id)

    @api.model
    def _prepare_result_lines_from_request(self, request):
        """One line per ACTIVE study, carrying its exact request line.

        A cancelled study is not reported on (Slice 3): it was never imaged, and
        hospital_billing's completion rule already ignores it.
        """
        return [
            (
                0,
                0,
                {
                    "exam_id": line.exam_id.id,
                    "body_part": line.body_part,
                    "contrast_used": line.contrast_required,
                    "sequence": line.sequence,
                    "request_line_id": line.id,
                },
            )
            for line in request.line_ids.filtered(lambda l: l.state != "cancelled")
        ]

    # ------------------------------------------------------------------
    # Integrity that holds on every channel
    # ------------------------------------------------------------------
    @api.constrains("request_id", "patient_id")
    def _check_patient_matches_request(self):
        """A report is about its request's patient. Not bypassed by sudo, a
        context key, RPC or the workflow capability."""
        for result in self:
            request = result.request_id
            if request and result.patient_id != request.patient_id:
                raise ValidationError(
                    "Radiology report %s names patient '%s', but request %s is "
                    "for '%s'. A report cannot be linked across patients."
                    % (
                        result.display_name,
                        result.patient_id.display_name,
                        request.display_name,
                        request.patient_id.display_name,
                    )
                )

    def _is_report_author(self):
        """Server code running as superuser is trusted; a user must hold a
        report-author role. has_group() reads the real user under sudo(), so
        a technician's sudo'd call is still judged as the technician unless it
        is genuinely superuser code."""
        if self.env.su:
            return True
        user = self.env.user
        return any(user.has_group(group) for group in REPORT_AUTHOR_GROUPS)

    def _assert_may_author(self, vals):
        """Only a report author writes the clinical narrative."""
        if self._is_report_author():
            return
        if any(not _blank(vals.get(name)) for name in CLINICAL_TEXT_FIELDS if name in vals):
            raise UserError(
                "Only a Radiologist, Hospital Manager or Hospital System "
                "Administrator can write a radiology report's findings, "
                "impression or recommendations."
            )

    def _operational_siblings(self):
        """Every active, non-cancelled report on this report's request."""
        self.ensure_one()
        return self.with_context(active_test=True).sudo().search(
            [("request_id", "=", self.request_id.id), ("state", "!=", "cancelled")]
        )

    def _assert_single_operational(self):
        """ONE OPERATIONAL REPORT PER REQUEST. Two active reports on one study
        leave every later action guessing which one it acts on, and billing's
        completion rule would count both. Checked whenever a report BECOMES
        operational: on create, on unarchive and on reset from cancelled."""
        for result in self:
            if not result.active or result.state == "cancelled":
                continue
            if len(result._operational_siblings()) > 1:
                raise UserError(
                    "Request %s already has an operational radiology report. "
                    "A second one cannot be started; open the existing report "
                    "instead." % result.request_id.display_name
                )

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        Request = self.env["hospital.radiology.request"]
        capability = has_result_workflow_capability()
        # ACCESS FIRST: a role with no create right hears "access denied", not a
        # workflow sentence about a record it could never have made.
        self.browse().check_access("create")
        for vals in vals_list:
            if not capability:
                # A report is BORN draft and unattributed.
                if vals.get("state", "draft") != "draft":
                    raise UserError(
                        "A radiology report starts as a draft. It cannot be "
                        "created as '%s'." % vals["state"]
                    )
                if vals.get("radiologist_id"):
                    raise UserError(
                        "The reporting radiologist is recorded when the report "
                        "is marked entered. It cannot be chosen."
                    )
                self._assert_may_author(vals)
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = sequence.next_by_code(
                    "hospital.radiology.result.sequence"
                ) or "New"
            if vals.get("request_id"):
                request = Request.browse(vals["request_id"])
                if not capability:
                    if request.state not in RESULT_ENTRY_REQUEST_STATES:
                        raise UserError(
                            "A radiology report can only be started for a request "
                            "that is In Progress. Request %s is '%s'."
                            % (request.display_name, request.state)
                        )
                    # The ordering physician is the REQUEST's, never input.
                    if vals.get("physician_id") and vals["physician_id"] != request.physician_id.id:
                        raise UserError(
                            "The ordering physician on a radiology report comes "
                            "from its request and cannot be chosen."
                        )
                    vals["physician_id"] = request.physician_id.id
                if not vals.get("patient_id"):
                    vals["patient_id"] = request.patient_id.id
                vals.setdefault("physician_id", request.physician_id.id)
                if not vals.get("line_ids"):
                    vals["line_ids"] = self._prepare_result_lines_from_request(request)
        results = super().create(vals_list)
        if not capability:
            results._assert_single_operational()
        for result in results:
            result._create_audit_log(
                action_type="create",
                description="Radiology result created.",
                new_value=result._audit_summary(
                    ["name", "request_id", "patient_id", "physician_id", "result_date", "state"]
                ),
            )
        return results

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def _changed_fields(self, vals, field_names):
        """The fields of `vals` among `field_names` whose value actually
        differs. One2many commands always count as a change."""
        self.ensure_one()
        changed = []
        for field_name in field_names:
            if field_name not in vals:
                continue
            field = self._fields[field_name]
            if field.type in ("one2many", "many2many"):
                if vals[field_name]:
                    changed.append(field_name)
                continue
            current = self[field_name]
            new = vals[field_name]
            if field.type == "many2one":
                current = current.id
                new = new or False
                if isinstance(new, models.BaseModel):
                    new = new.id
            elif field.type == "date":
                new = fields.Date.to_date(new) if new else False
            elif field.type in ("text", "char"):
                current = current or False
                new = new or False
            if new != current:
                changed.append(field_name)
        return changed

    def write(self, vals):
        capability = has_result_workflow_capability()
        # ACCESS FIRST, for the reason create() gives.
        self.check_access("write")
        for result in self:
            # PROVENANCE. Absolute: no capability, no sudo, no context key.
            changed = result._changed_fields(vals, PROVENANCE_FIELDS)
            if changed:
                raise UserError(
                    "Radiology report %s belongs to the request, patient and "
                    "ordering physician it was created for. %s cannot be "
                    "changed. Cancel this report and start one from the correct "
                    "request." % (result.display_name, ", ".join(changed))
                )
            if not capability:
                # STATE AUTHORITY.
                if "state" in vals and vals["state"] != result.state:
                    raise UserError(
                        STATE_WRITE_REFUSED_MESSAGE
                        % (result.display_name, result.state, vals["state"])
                    )
                if result._changed_fields(vals, ("radiologist_id",)):
                    raise UserError(
                        "The reporting radiologist on %s is recorded by the "
                        "workflow when the report is entered. It cannot be "
                        "changed." % result.display_name
                    )
            # THE FREEZE. Absolute: the workflow never edits a report's content.
            if result.state != "draft":
                frozen = result._changed_fields(vals, FROZEN_CONTENT_FIELDS)
                if frozen:
                    raise UserError(
                        "Radiology report %s is %s and its content is frozen. "
                        "The following cannot be changed: %s."
                        % (
                            result.display_name,
                            result.state,
                            ", ".join(result._fields[name].string or name for name in frozen),
                        )
                    )
        self._assert_may_author(vals)
        unarchiving = self.filtered(lambda r: not r.active) if vals.get("active") is True else self.browse()
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            result.id: result._audit_summary(tracked_vals.keys()) for result in self
        }
        result = super().write(vals)
        if unarchiving:
            unarchiving._assert_single_operational()
        # Only the workflow, which records its own state_change entry, may
        # skip this audit. A context key no longer can.
        if tracked_vals and not capability:
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Radiology result archived."
                if action_type == "archive"
                else "Radiology result updated."
            )
            for radiology_result in self:
                radiology_result._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(radiology_result.id),
                    new_value=radiology_result._audit_summary(tracked_vals.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for result in self:
                result._create_audit_log(
                    action_type="delete_attempt",
                    description="Radiology result deletion blocked.",
                    old_value=result._audit_summary(
                        ["name", "request_id", "patient_id", "physician_id", "state"]
                    ),
                )
            raise UserError(
                "Radiology results are sensitive health records. Cancel or archive them instead of deleting."
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # Completeness
    # ------------------------------------------------------------------
    def _entry_problems(self):
        """Why this report cannot be entered, as sentences. Empty when it can.

        THE RULE (Radiology Slice 3):
          * the narrative says something: findings or impression is non-blank
            after trimming. Recommendations alone are not a report.
          * the lines are sound: at least one, every one linked to an ordered
            study of THIS request, and no study reported twice.
        A per-study summary is NOT required: the radiologist reports in the
        narrative, and every report in the live data covers a single study.
        COVERAGE of every active study is not required here either: the desk
        creates one line per active study, and hospital_billing's completion
        rule already refuses to complete a request until every active study is
        covered by a released report.
        """
        self.ensure_one()
        problems = []
        if _blank(self.findings) and _blank(self.impression):
            problems.append("enter the findings or the impression")
        if not self.line_ids:
            problems.append("the report has no study lines")
        seen = self.env["hospital.radiology.request.line"]
        for line in self.line_ids:
            req_line = line.request_line_id
            if not req_line:
                problems.append(
                    "the line for %s is not linked to an ordered study"
                    % line.exam_id.display_name
                )
            elif req_line.request_id != self.request_id:
                problems.append(
                    "the line for %s does not match a study on this request"
                    % line.exam_id.display_name
                )
            elif req_line in seen:
                problems.append(
                    "the study %s is reported twice" % line.exam_id.display_name
                )
            seen |= req_line
        return problems

    def _assert_entry_complete(self):
        for result in self:
            problems = result._entry_problems()
            if problems:
                raise ValidationError(
                    "Radiology report %s is not complete: %s."
                    % (result.display_name, "; ".join(problems))
                )

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_mark_entered(self):
        for result in self:
            if result.state != "draft":
                raise UserError("Only draft radiology results can be marked as entered.")
            if not result._is_report_author():
                raise UserError(
                    "Only a Radiologist, Hospital Manager or Hospital System "
                    "Administrator can mark a radiology report entered."
                )
            result._assert_entry_complete()
            vals = {"state": "entered"}
            # PROVENANCE: the author who enters the report. Superuser code
            # (fixtures, migrations) records nobody rather than a system user.
            if not self.env.su or any(
                self.env.user.has_group(group) for group in REPORT_AUTHOR_GROUPS
            ):
                vals["radiologist_id"] = self.env.user.id
            result._write_state("entered", extra_vals=vals)
            if vals.get("radiologist_id"):
                result._create_audit_log(
                    action_type="update",
                    description="Radiology report entered; reporting radiologist recorded.",
                    new_value="radiologist_id: %s" % result.radiologist_id.display_name,
                )

    def _assert_may_sign(self, act):
        """Validation and release belong to the report authors (Radiology
        Slice 5). The technician holds result write only so imaging can be
        attached; that is not a licence to sign or publish the report."""
        if not self._is_report_author():
            raise UserError(
                "Only a Radiologist, Hospital Manager or Hospital System "
                "Administrator can %s a radiology report." % act
            )

    def action_validate(self):
        for result in self:
            if result.state != "entered":
                raise UserError("Only entered radiology results can be validated.")
            result._assert_may_sign("validate")
            # Independent gate: an entered report is re-checked, so nothing
            # reaches validation blank.
            result._assert_entry_complete()
            result._write_state("validated")

    def action_release(self):
        for result in self:
            if result.state != "validated":
                raise UserError("Only validated radiology results can be released.")
            result._assert_may_sign("release")
            result._write_state("released")

    def action_cancel(self):
        for result in self:
            if result.state not in ("draft", "entered"):
                raise UserError("Only draft or entered radiology results can be cancelled.")
            result._write_state("cancelled")

    def action_reset_to_draft(self):
        for result in self:
            if result.state != "cancelled":
                raise UserError("Only cancelled radiology results can reset to draft. Released reports are frozen.")
            result._write_state("draft")
            # Back in draft, it is operational again.
            result._assert_single_operational()

    def _workflow_write(self, vals):
        """THE controlled path for a workflow write. Private, so not callable
        over RPC. Used by this model's actions and by hospital_billing."""
        with _result_workflow_capability():
            return self.write(vals)

    def _write_state(self, new_state, extra_vals=None):
        old_state = self.state
        vals = dict(extra_vals or {}, state=new_state)
        self._workflow_write(vals)
        self._create_audit_log(
            action_type="state_change",
            description="Radiology result state changed.",
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


class HospitalRadiologyResultLine(models.Model):
    _name = "hospital.radiology.result.line"
    _description = "Radiology Result Line"
    _order = "sequence, id"

    # Structure: which report, which ordered study, which exam. Never changed
    # once set -- except that an EMPTY request line on a legacy row may be
    # filled by the workflow (hospital_billing's release healing).
    STRUCTURE_FIELDS = ("result_id", "exam_id", "request_line_id")

    result_id = fields.Many2one(
        "hospital.radiology.result",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    exam_id = fields.Many2one(
        "hospital.radiology.exam",
        required=True,
    )
    modality = fields.Selection(related="exam_id.modality", readonly=True)
    body_part = fields.Char()
    contrast_used = fields.Boolean()
    result_summary = fields.Char()
    notes = fields.Text()
    request_line_id = fields.Many2one(
        "hospital.radiology.request.line",
        string="Request Line",
        readonly=True,
        copy=False,
        ondelete="restrict",
    )

    @api.onchange("exam_id")
    def _onchange_exam_id(self):
        if self.exam_id:
            self.body_part = self.exam_id.body_part
            self.contrast_used = self.exam_id.contrast_required

    @api.constrains("request_line_id", "result_id", "exam_id")
    def _check_request_line_matches(self):
        """A linked line reports on a study of ITS OWN report's request, for
        the exam that study ordered, once. Every channel."""
        for line in self:
            req_line = line.request_line_id
            if not req_line:
                continue
            request = line.result_id.request_id
            if request and req_line.request_id != request:
                raise ValidationError(
                    "A radiology report line cannot report on a study of another "
                    "request (%s)." % req_line.request_id.display_name
                )
            if req_line.exam_id != line.exam_id:
                raise ValidationError(
                    "A radiology report line's exam must be the exam its study "
                    "ordered (%s)." % req_line.exam_id.display_name
                )
            twins = line.result_id.line_ids.filtered(
                lambda l: l.request_line_id == req_line
            )
            if len(twins) > 1:
                raise ValidationError(
                    "The study %s is reported more than once on %s."
                    % (req_line.exam_id.display_name, line.result_id.display_name)
                )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if has_result_workflow_capability():
            return lines
        for line in lines:
            result = line.result_id
            if result.state != "draft":
                raise UserError(
                    "Report lines cannot be added to %s: it is %s and its content "
                    "is frozen." % (result.display_name, result.state)
                )
            if result.request_id and not line.request_line_id:
                raise UserError(
                    "A radiology report line must report on an ordered study of "
                    "request %s." % result.request_id.display_name
                )
            if line.request_line_id.state == "cancelled":
                raise UserError(
                    "The study %s was cancelled and is not reported on."
                    % line.exam_id.display_name
                )
        return lines

    def write(self, vals):
        real_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        capability = has_result_workflow_capability()
        # ACCESS FIRST, for the reason the report's create() gives.
        self.check_access("write")
        for line in self:
            for field_name in self.STRUCTURE_FIELDS:
                if field_name not in real_vals:
                    continue
                current = line[field_name].id
                new = real_vals[field_name] or False
                if new == current:
                    continue
                if field_name == "request_line_id" and not current and capability:
                    continue  # the workflow's healing of a legacy row
                raise UserError(
                    "The structure of a radiology report line -- its report, "
                    "exam and ordered study -- cannot be changed."
                )
            if line.result_id.state != "draft" and real_vals:
                healing = (
                    capability
                    and set(real_vals) == {"request_line_id"}
                    and not line.request_line_id
                )
                changed = [
                    name for name in real_vals
                    if name in line._fields
                    and (line[name].id if line._fields[name].type == "many2one" else line[name] or False)
                    != (real_vals[name] or False)
                ]
                if changed and not healing:
                    raise UserError(
                        "Radiology report %s is %s and its lines are frozen."
                        % (line.result_id.display_name, line.result_id.state)
                    )
        return super().write(vals)

    def unlink(self):
        frozen = self.filtered(lambda l: l.result_id.state != "draft")
        if frozen:
            raise UserError(
                "Radiology report lines cannot be deleted once the report has "
                "left draft."
            )
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for line in self:
                if line.result_id:
                    line.result_id._create_audit_log(
                        action_type="delete_attempt",
                        description="Radiology result line deletion blocked.",
                        old_value=f"Exam: {line.exam_id.display_name}",
                    )
            raise UserError(
                "Radiology result lines are sensitive health records. Cancel or archive the result instead of deleting lines."
            )
        return super().unlink()

from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalSurgeryRequest(models.Model):
    _name = "hospital.surgery.request"
    _description = "Surgery Request / Operation Case"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "request_date desc, id desc"

    # Clinical / scheduling fields that must not be edited once the case is
    # completed or cancelled. Workflow/system fields and chatter are excluded.
    PROTECTED_FIELDS = {
        "patient_id",
        "admission_id",
        "surgery_type_id",
        "theatre_id",
        "department_id",
        "planned_start",
        "planned_end",
        "actual_start",
        "actual_end",
        "urgency",
        "surgical_risk",
        "diagnosis_summary",
        "indication_for_surgery",
        "notes",
        "active",
    }

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
        tracking=True,
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        string="Patient",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    admission_id = fields.Many2one(
        "hospital.admission",
        string="Admission",
        ondelete="set null",
        domain="[('patient_id', '=', patient_id)]",
        tracking=True,
    )
    surgery_type_id = fields.Many2one(
        "hospital.surgery.type",
        string="Surgery Type",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    theatre_id = fields.Many2one(
        "hospital.operation.theatre",
        string="Operation Theatre",
        ondelete="set null",
        tracking=True,
    )
    department_id = fields.Many2one(
        "hospital.department",
        string="Department",
        ondelete="set null",
    )
    requested_by = fields.Many2one(
        "res.users",
        string="Requested By",
        default=lambda self: self.env.user,
    )
    request_date = fields.Datetime(string="Request Date", default=fields.Datetime.now)
    planned_start = fields.Datetime(string="Planned Start", tracking=True)
    planned_end = fields.Datetime(string="Planned End")
    actual_start = fields.Datetime(string="Actual Start")
    actual_end = fields.Datetime(string="Actual End")

    urgency = fields.Selection(
        [
            ("elective", "Elective"),
            ("urgent", "Urgent"),
            ("emergency", "Emergency"),
        ],
        string="Urgency",
        default="elective",
        tracking=True,
    )
    surgical_risk = fields.Selection(
        [
            ("low", "Low"),
            ("moderate", "Moderate"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        string="Surgical Risk",
        default="low",
    )
    diagnosis_summary = fields.Text(string="Diagnosis Summary")
    indication_for_surgery = fields.Text(string="Indication for Surgery")

    # Sub-status indicators
    preop_status = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
            ("blocked", "Blocked"),
        ],
        string="Pre-op Status",
        default="not_started",
        tracking=True,
    )
    anesthesia_status = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("pending", "Pending"),
            ("documented", "Documented"),
        ],
        string="Anesthesia Status",
        default="pending",
        tracking=True,
    )
    operation_status = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
        ],
        string="Operation Status",
        default="not_started",
        tracking=True,
    )
    postop_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("documented", "Documented"),
        ],
        string="Post-op Status",
        default="pending",
        tracking=True,
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("requested", "Requested"),
            ("scheduled", "Scheduled"),
            ("preop_ready", "Pre-op Ready"),
            ("in_theatre", "In Theatre"),
            ("operation_done", "Operation Done"),
            ("postop_done", "Post-op Done"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )

    # Clinical documents
    team_member_ids = fields.One2many(
        "hospital.surgery.team.member",
        "surgery_id",
        string="Surgical Team",
    )
    preop_checklist_id = fields.Many2one(
        "hospital.preop.checklist",
        string="Pre-op Checklist",
        copy=False,
    )
    anesthesia_note_id = fields.Many2one(
        "hospital.anesthesia.note",
        string="Anesthesia Note",
        copy=False,
    )
    operation_report_id = fields.Many2one(
        "hospital.operation.report",
        string="Operation Report",
        copy=False,
    )
    postop_note_ids = fields.One2many(
        "hospital.postop.note",
        "surgery_id",
        string="Post-op Notes",
    )

    team_member_count = fields.Integer(
        compute="_compute_document_counts", string="Team Members"
    )
    postop_note_count = fields.Integer(
        compute="_compute_document_counts", string="Post-op Notes"
    )

    # Billing readiness (no bill is created in this module)
    default_price = fields.Monetary(
        string="Surgery Price",
        related="surgery_type_id.default_price",
        currency_field="currency_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
    )
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        string="Patient Bill",
        readonly=True,
        copy=False,
    )
    bill_count = fields.Integer(compute="_compute_bill_count", string="Patient Bill")
    # billing_state is defined as a computed field in models/surgery_billing.py
    # (Task 32A) so it stays in sync with the linked patient bill.

    notes = fields.Text(string="Notes")
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # Display name
    # ------------------------------------------------------------------
    @api.depends("name", "patient_id", "surgery_type_id")
    def _compute_display_name(self):
        for rec in self:
            if rec.name and rec.name != "New":
                rec.display_name = rec.name
            elif rec.patient_id:
                rec.display_name = f"New - {rec.patient_id.display_name}"
            else:
                rec.display_name = "New"

    def _compute_document_counts(self):
        for rec in self:
            rec.team_member_count = len(rec.team_member_ids)
            rec.postop_note_count = len(rec.postop_note_ids)

    def _compute_bill_count(self):
        for rec in self:
            rec.bill_count = 1 if rec.bill_id else 0

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("surgery_type_id")
    def _onchange_surgery_type_id(self):
        if self.surgery_type_id:
            if self.surgery_type_id.requires_anesthesia:
                if self.anesthesia_status == "not_required":
                    self.anesthesia_status = "pending"
            else:
                self.anesthesia_status = "not_required"

    @api.onchange("theatre_id")
    def _onchange_theatre_id(self):
        if self.theatre_id and self.theatre_id.department_id and not self.department_id:
            self.department_id = self.theatre_id.department_id

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def _set_state(self, new_state, description, extra_vals=None):
        for rec in self:
            old_state = rec.state
            vals = {"state": new_state}
            if extra_vals:
                vals.update(extra_vals)
            rec.write(vals)
            rec._create_audit_log(
                action_type="state_change",
                description=description,
                old_value=f"State: {old_state}",
                new_value=f"State: {new_state}",
            )

    def action_submit_request(self):
        self.filtered(lambda r: r.state == "draft")._set_state(
            "requested", "Surgery request submitted."
        )

    def action_schedule(self):
        for rec in self.filtered(lambda r: r.state == "requested"):
            rec._set_state("scheduled", "Surgery scheduled.")

    def action_mark_preop_ready(self):
        for rec in self.filtered(lambda r: r.state == "scheduled"):
            if not rec.preop_checklist_id:
                raise UserError(
                    "A pre-op checklist must be created and completed before "
                    "the case can be marked pre-op ready."
                )
            if rec.preop_checklist_id.state != "completed":
                raise UserError(
                    "The pre-op checklist must be completed before the case "
                    "can be marked pre-op ready."
                )
            rec._set_state(
                "preop_ready",
                "Surgery marked pre-op ready.",
                {"preop_status": "completed"},
            )

    def action_start_operation(self):
        for rec in self.filtered(lambda r: r.state == "preop_ready"):
            if not rec.theatre_id:
                raise UserError(
                    "An operation theatre must be set before starting the operation."
                )
            if not rec.planned_start:
                raise UserError(
                    "A planned start time must be set before starting the operation."
                )
            extra = {"operation_status": "in_progress"}
            if not rec.actual_start:
                extra["actual_start"] = fields.Datetime.now()
            rec._set_state("in_theatre", "Operation started.", extra)

    def action_complete_operation(self):
        for rec in self.filtered(lambda r: r.state == "in_theatre"):
            if rec.surgery_type_id.requires_anesthesia:
                has_note = bool(rec.anesthesia_note_id)
                documented = rec.anesthesia_status == "documented"
                if not (has_note or documented):
                    raise UserError(
                        "This surgery type requires anesthesia. Document an "
                        "anesthesia note (or set the anesthesia status to "
                        "Documented) before completing the operation."
                    )
            extra = {"operation_status": "completed"}
            if not rec.actual_end:
                extra["actual_end"] = fields.Datetime.now()
            rec._set_state("operation_done", "Operation completed.", extra)

    def action_mark_postop_done(self):
        for rec in self.filtered(lambda r: r.state == "operation_done"):
            rec._set_state(
                "postop_done",
                "Post-op marked done.",
                {"postop_status": "documented"},
            )

    def action_complete_case(self):
        for rec in self.filtered(lambda r: r.state == "postop_done"):
            if not rec.operation_report_id:
                raise UserError(
                    "An operation report must exist before the case can be completed."
                )
            rec._set_state("completed", "Surgery case completed.")

    def action_cancel(self):
        for rec in self.filtered(
            lambda r: r.state in ("draft", "requested", "scheduled", "preop_ready")
        ):
            rec._set_state("cancelled", "Surgery case cancelled.")

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state == "cancelled"):
            rec._set_state("draft", "Surgery case reset to draft.")

    # ------------------------------------------------------------------
    # Clinical document smart buttons (create-or-open)
    # ------------------------------------------------------------------
    def _open_record_action(self, name, model, res_id):
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": model,
            "res_id": res_id,
            "view_mode": "form",
            "target": "current",
        }

    def action_open_preop_checklist(self):
        self.ensure_one()
        if not self.preop_checklist_id:
            checklist = self.env["hospital.preop.checklist"].create({
                "surgery_id": self.id,
            })
            self.preop_checklist_id = checklist.id
        return self._open_record_action(
            "Pre-op Checklist", "hospital.preop.checklist", self.preop_checklist_id.id
        )

    def action_open_anesthesia_note(self):
        self.ensure_one()
        if not self.anesthesia_note_id:
            note = self.env["hospital.anesthesia.note"].create({
                "surgery_id": self.id,
            })
            self.anesthesia_note_id = note.id
        return self._open_record_action(
            "Anesthesia Note", "hospital.anesthesia.note", self.anesthesia_note_id.id
        )

    def action_open_operation_report(self):
        self.ensure_one()
        if not self.operation_report_id:
            report = self.env["hospital.operation.report"].create({
                "surgery_id": self.id,
            })
            self.operation_report_id = report.id
        return self._open_record_action(
            "Operation Report", "hospital.operation.report", self.operation_report_id.id
        )

    def action_view_postop_notes(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Post-op Notes",
            "res_model": "hospital.postop.note",
            "view_mode": "list,form",
            "domain": [("surgery_id", "=", self.id)],
            "context": {
                "default_surgery_id": self.id,
                "default_patient_id": self.patient_id.id,
            },
        }

    def action_view_bill(self):
        self.ensure_one()
        if not self.bill_id:
            raise UserError("No patient bill is linked to this surgery yet.")
        return self._open_record_action(
            "Patient Bill", "hospital.patient.bill", self.bill_id.id
        )

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code(
                        "hospital.surgery.request.sequence"
                    )
                    or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Surgery request created.",
                new_value=record._audit_summary(
                    ["name", "patient_id", "surgery_type_id", "request_date"]
                ),
            )
        return records

    def write(self, vals):
        protected_changed = self.PROTECTED_FIELDS.intersection(vals.keys())
        if protected_changed:
            for rec in self:
                if rec.state in ("completed", "cancelled"):
                    rec._create_audit_log(
                        action_type="update",
                        description="Locked edit attempt on completed/cancelled surgery.",
                        old_value=rec._audit_summary(["name", "state"]),
                    )
                    raise UserError(
                        "Completed or cancelled surgery cases are locked. "
                        "Reset to Draft before making corrections."
                    )
        tracked = {
            k: v
            for k, v in vals.items()
            if k not in ("write_date", "write_uid", "display_name")
        }
        old_values = {rec.id: rec._audit_summary(tracked.keys()) for rec in self}
        result = super().write(vals)
        if tracked and "state" not in tracked:
            for rec in self:
                rec._create_audit_log(
                    action_type="update",
                    description="Surgery request updated.",
                    old_value=old_values.get(rec.id),
                    new_value=rec._audit_summary(tracked.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for rec in self:
                rec._create_audit_log(
                    action_type="delete_attempt",
                    description="Deletion of surgery record blocked.",
                    old_value=rec._audit_summary(["name", "state"]),
                )
            raise UserError(
                "Surgery records are sensitive clinical data. "
                "Cancel or archive instead of deleting."
            )
        return super().unlink()

    # ------------------------------------------------------------------
    # Audit helpers
    # ------------------------------------------------------------------
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
        for rec in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=rec.patient_id.id if rec.patient_id else False,
                model_name=rec._name,
                record_id=rec.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )

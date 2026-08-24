from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalRadiologyExam(models.Model):
    _name = "hospital.radiology.exam"
    _description = "Radiology Exam"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char()
    modality = fields.Selection(
        [
            ("xray", "X-Ray"),
            ("ultrasound", "Ultrasound"),
            ("ct", "CT Scan"),
            ("mri", "MRI"),
            ("fluoroscopy", "Fluoroscopy"),
            ("mammography", "Mammography"),
            ("other", "Other"),
        ],
        default="xray",
        required=True,
    )
    body_part = fields.Char()
    contrast_required = fields.Boolean()
    description = fields.Text()
    active = fields.Boolean(default=True)
    request_count = fields.Integer(compute="_compute_related_counts", string="Requests")
    result_count = fields.Integer(compute="_compute_related_counts", string="Results")

    @api.depends("name", "code")
    def _compute_display_name(self):
        for exam in self:
            exam.display_name = (
                f"{exam.code} - {exam.name}" if exam.code else exam.name or "New Exam"
            )

    def _compute_related_counts(self):
        RequestLine = self.env["hospital.radiology.request.line"]
        ResultLine = self.env["hospital.radiology.result.line"]
        for exam in self:
            if not exam.id:
                exam.request_count = 0
                exam.result_count = 0
                continue
            exam.request_count = len(
                RequestLine.search([("exam_id", "=", exam.id)]).mapped("request_id")
            )
            exam.result_count = len(
                ResultLine.search([("exam_id", "=", exam.id)]).mapped("result_id")
            )

    def action_view_requests(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Radiology Requests",
            "res_model": "hospital.radiology.request",
            "view_mode": "list,form",
            "domain": [("line_ids.exam_id", "=", self.id)],
        }

    def action_view_results(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Radiology Results",
            "res_model": "hospital.radiology.result",
            "view_mode": "list,form",
            "domain": [("line_ids.exam_id", "=", self.id)],
        }


class HospitalRadiologyRequest(models.Model):
    _name = "hospital.radiology.request"
    _description = "Radiology Request"
    _order = "request_date desc, id desc"

    name = fields.Char(readonly=True, copy=False, default="New")
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        ondelete="restrict",
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        required=True,
    )
    appointment_id = fields.Many2one("hospital.appointment")
    evaluation_id = fields.Many2one("hospital.patient.evaluation")
    diagnosis_id = fields.Many2one("hospital.patient.diagnosis")
    request_date = fields.Date(
        default=fields.Date.context_today,
        required=True,
    )
    priority = fields.Selection(
        [
            ("routine", "Routine"),
            ("urgent", "Urgent"),
            ("stat", "STAT"),
        ],
        default="routine",
        required=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("requested", "Requested"),
            ("scheduled", "Scheduled"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
    )
    line_ids = fields.One2many(
        "hospital.radiology.request.line",
        "request_id",
        string="Requested Exams",
    )
    result_ids = fields.One2many(
        "hospital.radiology.result",
        "request_id",
        string="Radiology Results",
    )
    result_count = fields.Integer(compute="_compute_result_count", string="Results")
    clinical_indication = fields.Text()
    instructions = fields.Text()
    completed_at = fields.Datetime(readonly=True, copy=False)
    completed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    active = fields.Boolean(default=True)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for request in self:
            if request.name and request.name != "New":
                request.display_name = request.name
            elif request.patient_id:
                request.display_name = f"Radiology Request - {request.patient_id.display_name}"
            else:
                request.display_name = "New Radiology Request"

    def _compute_result_count(self):
        Result = self.env["hospital.radiology.result"]
        for request in self:
            request.result_count = (
                Result.search_count([("request_id", "=", request.id)])
                if request.id
                else 0
            )

    @api.onchange("appointment_id")
    def _onchange_appointment_id(self):
        if not self.appointment_id:
            return
        if self.appointment_id.patient_id:
            self.patient_id = self.appointment_id.patient_id
        if self.appointment_id.doctor_id:
            self.physician_id = self.appointment_id.doctor_id

    @api.onchange("evaluation_id")
    def _onchange_evaluation_id(self):
        if not self.evaluation_id:
            return
        if self.evaluation_id.patient_id:
            self.patient_id = self.evaluation_id.patient_id
        if self.evaluation_id.physician_id:
            self.physician_id = self.evaluation_id.physician_id
        if self.evaluation_id.appointment_id and not self.appointment_id:
            self.appointment_id = self.evaluation_id.appointment_id

    @api.onchange("diagnosis_id")
    def _onchange_diagnosis_id(self):
        if not self.diagnosis_id:
            return
        if self.diagnosis_id.patient_id:
            self.patient_id = self.diagnosis_id.patient_id
        if self.diagnosis_id.physician_id:
            self.physician_id = self.diagnosis_id.physician_id
        if self.diagnosis_id.appointment_id and not self.appointment_id:
            self.appointment_id = self.diagnosis_id.appointment_id

    def action_view_results(self):
        self.ensure_one()
        Result = self.env["hospital.radiology.result"]
        latest_result = Result.search(
            [("request_id", "=", self.id)],
            order="result_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Radiology Result",
            "res_model": "hospital.radiology.result",
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
                    "hospital.radiology.request.sequence"
                ) or "New"
        requests = super().create(vals_list)
        for request in requests:
            request._create_audit_log(
                action_type="create",
                description="Radiology request created.",
                new_value=request._audit_summary(
                    ["name", "patient_id", "physician_id", "request_date", "priority", "state"]
                ),
            )
        return requests

    def write(self, vals):
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            request.id: request._audit_summary(tracked_vals.keys()) for request in self
        }
        result = super().write(vals)
        if tracked_vals and not self.env.context.get("skip_radiology_request_write_audit"):
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Radiology request archived."
                if action_type == "archive"
                else "Radiology request updated."
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
                    description="Radiology request deletion blocked.",
                    old_value=request._audit_summary(
                        ["name", "patient_id", "physician_id", "priority", "state"]
                    ),
                )
            raise UserError(
                "Radiology requests are sensitive health records. Cancel or archive them instead of deleting."
            )
        return super().unlink()

    def action_confirm_request(self):
        for request in self:
            if request.state != "draft":
                raise UserError("Only draft radiology requests can be confirmed.")
            request._write_state("requested")

    def action_schedule(self):
        for request in self:
            if request.state != "requested":
                raise UserError("Only requested radiology requests can be scheduled.")
            request._write_state("scheduled")

    def action_mark_in_progress(self):
        for request in self:
            if request.state != "scheduled":
                raise UserError("Only scheduled radiology requests can be marked in progress.")
            request._write_state("in_progress")

    def action_cancel(self):
        for request in self:
            if request.state not in ("draft", "requested", "scheduled"):
                raise UserError("Only draft, requested, or scheduled requests can be cancelled.")
            request._write_state("cancelled")

    def action_reset_to_draft(self):
        for request in self:
            if request.state != "cancelled":
                raise UserError("Only cancelled radiology requests can be reset to draft.")
            request._write_state("draft")

    def _write_state(self, new_state):
        old_state = self.state
        self.with_context(skip_radiology_request_write_audit=True).write(
            {"state": new_state}
        )
        self._create_audit_log(
            action_type="state_change",
            description="Radiology request state changed.",
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


class HospitalRadiologyRequestLine(models.Model):
    _name = "hospital.radiology.request.line"
    _description = "Radiology Request Line"
    _order = "sequence, id"

    request_id = fields.Many2one(
        "hospital.radiology.request",
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
    contrast_required = fields.Boolean()
    special_instruction = fields.Char()
    state = fields.Selection(
        [("active", "Active"), ("cancelled", "Cancelled")],
        default="active",
        required=True,
        index=True,
    )

    def init(self):
        """Keep the system-owned line state safe at database level too.

        Odoo does not normally create PostgreSQL defaults for Python field
        defaults. During rolling upgrades or a stale worker, the database may
        know about the required ``state`` column before the serving registry
        includes it in request-line create values. A DB default keeps valid
        radiology request lines creatable without relaxing the legitimate
        NOT NULL constraint.
        """
        super().init()
        self.env.cr.execute(
            """
            ALTER TABLE hospital_radiology_request_line
            ALTER COLUMN state SET DEFAULT 'active'
            """
        )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.setdefault("state", "active")
            if not vals.get("exam_id"):
                raise UserError("Radiology request line requires an Exam before saving.")
        return super().create(vals_list)

    @api.onchange("exam_id")
    def _onchange_exam_id(self):
        if self.exam_id:
            self.body_part = self.exam_id.body_part
            self.contrast_required = self.exam_id.contrast_required

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for line in self:
                if line.request_id:
                    line.request_id._create_audit_log(
                        action_type="delete_attempt",
                        description="Radiology request line deletion blocked.",
                        old_value=f"Exam: {line.exam_id.display_name}",
                    )
            raise UserError(
                "Radiology request lines are sensitive health records. Cancel or archive the request instead of deleting lines."
            )
        return super().unlink()

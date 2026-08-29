from odoo import api, fields, models
from odoo.exceptions import UserError


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
    radiologist_id = fields.Many2one(
        "res.users",
        string="Radiologist / Reporter",
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
            for line in request.line_ids
        ]

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = sequence.next_by_code(
                    "hospital.radiology.result.sequence"
                ) or "New"
            if vals.get("request_id") and not vals.get("patient_id"):
                request = self.env["hospital.radiology.request"].browse(vals["request_id"])
                vals["patient_id"] = request.patient_id.id
                vals.setdefault("physician_id", request.physician_id.id)
            if vals.get("request_id") and not vals.get("line_ids"):
                request = self.env["hospital.radiology.request"].browse(vals["request_id"])
                vals["line_ids"] = self._prepare_result_lines_from_request(request)
        results = super().create(vals_list)
        for result in results:
            result._create_audit_log(
                action_type="create",
                description="Radiology result created.",
                new_value=result._audit_summary(
                    ["name", "request_id", "patient_id", "physician_id", "result_date", "state"]
                ),
            )
        return results

    def write(self, vals):
        protected = set(vals) - {"active"}
        if protected:
            released = self.filtered(lambda r: r.state == "released")
            if released and not self.env.context.get("skip_radiology_result_write_audit"):
                raise UserError("Released radiology reports are frozen and cannot be changed.")
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            result.id: result._audit_summary(tracked_vals.keys()) for result in self
        }
        result = super().write(vals)
        if tracked_vals and not self.env.context.get("skip_radiology_result_write_audit"):
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

    def action_mark_entered(self):
        for result in self:
            if result.state != "draft":
                raise UserError("Only draft radiology results can be marked as entered.")
            result._write_state("entered")

    def action_validate(self):
        for result in self:
            if result.state != "entered":
                raise UserError("Only entered radiology results can be validated.")
            result._write_state("validated")

    def action_release(self):
        for result in self:
            if result.state != "validated":
                raise UserError("Only validated radiology results can be released.")
            result._write_state("released")

    def action_cancel(self):
        for result in self:
            if result.state not in ("draft", "entered"):
                raise UserError("Only draft or entered radiology results can be cancelled.")
            result._write_state("cancelled")

    def action_reset_to_draft(self):
        manager_or_admin = self.env.user.has_group(
            "hospital_management.group_hospital_manager"
        ) or self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        )
        for result in self:
            if result.state != "cancelled":
                raise UserError("Only cancelled radiology results can reset to draft. Released reports are frozen.")
            result._write_state("draft")

    def _write_state(self, new_state):
        old_state = self.state
        self.with_context(skip_radiology_result_write_audit=True).write(
            {"state": new_state}
        )
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

    def unlink(self):
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

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class HospitalTreatmentPlan(models.Model):
    _name = "hospital.treatment.plan"
    _description = "Treatment Plan"
    _order = "plan_date desc, id desc"

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
    prescription_id = fields.Many2one("hospital.prescription")
    plan_date = fields.Date(
        default=fields.Date.context_today,
        required=True,
    )
    start_date = fields.Date()
    end_date = fields.Date()
    priority = fields.Selection(
        [
            ("routine", "Routine"),
            ("urgent", "Urgent"),
            ("critical", "Critical"),
        ],
        default="routine",
        required=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
    )
    line_ids = fields.One2many(
        "hospital.treatment.plan.line",
        "treatment_plan_id",
        string="Treatment Lines",
    )
    treatment_goal = fields.Text()
    doctor_instructions = fields.Text()
    patient_instructions = fields.Text()
    follow_up_required = fields.Boolean()
    follow_up_date = fields.Date()
    notes = fields.Text()
    active = fields.Boolean(default=True)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for plan in self:
            if plan.name and plan.name != "New":
                plan.display_name = plan.name
            elif plan.patient_id:
                plan.display_name = f"New Treatment Plan - {plan.patient_id.display_name}"
            else:
                plan.display_name = "New Treatment Plan"

    @api.constrains("start_date", "end_date", "follow_up_date", "follow_up_required")
    def _check_treatment_plan_dates(self):
        for plan in self:
            if plan.start_date and plan.end_date and plan.end_date < plan.start_date:
                raise ValidationError("Treatment plan end date cannot be before start date.")
            if plan.follow_up_required and not plan.follow_up_date:
                raise ValidationError("Follow-up date is required when follow-up is required.")

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

    @api.onchange("prescription_id")
    def _onchange_prescription_id(self):
        if not self.prescription_id:
            return
        if self.prescription_id.patient_id:
            self.patient_id = self.prescription_id.patient_id
        if self.prescription_id.physician_id:
            self.physician_id = self.prescription_id.physician_id
        if self.prescription_id.appointment_id and not self.appointment_id:
            self.appointment_id = self.prescription_id.appointment_id
        if self.prescription_id.diagnosis_id and not self.diagnosis_id:
            self.diagnosis_id = self.prescription_id.diagnosis_id

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = sequence.next_by_code(
                    "hospital.treatment.plan.sequence"
                ) or "New"
        treatment_plans = super().create(vals_list)
        for plan in treatment_plans:
            plan._create_audit_log(
                action_type="create",
                description="Treatment plan created.",
                new_value=plan._audit_summary(
                    ["name", "patient_id", "physician_id", "plan_date", "priority", "state"]
                ),
            )
        return treatment_plans

    def write(self, vals):
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            plan.id: plan._audit_summary(tracked_vals.keys())
            for plan in self
        }
        result = super().write(vals)
        if tracked_vals and not self.env.context.get("skip_treatment_plan_write_audit"):
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Treatment plan archived."
                if action_type == "archive"
                else "Treatment plan updated."
            )
            for plan in self:
                plan._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(plan.id),
                    new_value=plan._audit_summary(tracked_vals.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for plan in self:
                plan._create_audit_log(
                    action_type="delete_attempt",
                    description="Treatment plan deletion blocked.",
                    old_value=plan._audit_summary(
                        ["name", "patient_id", "physician_id", "priority", "state"]
                    ),
                )
            raise UserError(
                "Treatment plans are sensitive health records. Cancel or archive them instead of deleting."
            )
        return super().unlink()

    def action_activate(self):
        for plan in self.filtered(lambda record: record.state == "draft"):
            old_state = plan.state
            plan.with_context(skip_treatment_plan_write_audit=True).write(
                {"state": "active"}
            )
            plan._log_state_change(old_state)

    def action_mark_completed(self):
        for plan in self.filtered(lambda record: record.state == "active"):
            old_state = plan.state
            plan.with_context(skip_treatment_plan_write_audit=True).write(
                {"state": "completed"}
            )
            plan._log_state_change(old_state)

    def action_cancel(self):
        for plan in self.filtered(lambda record: record.state in ("draft", "active")):
            old_state = plan.state
            plan.with_context(skip_treatment_plan_write_audit=True).write(
                {"state": "cancelled"}
            )
            plan._log_state_change(old_state)

    def action_reset_to_draft(self):
        manager_or_admin = self.env.user.has_group(
            "hospital_management.group_hospital_manager"
        ) or self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        )
        for plan in self.filtered(lambda record: record.state in ("cancelled", "completed")):
            if plan.state == "completed" and not manager_or_admin:
                raise UserError(
                    "Only Hospital Managers or System Administrators can reset completed treatment plans."
                )
            old_state = plan.state
            plan.with_context(skip_treatment_plan_write_audit=True).write(
                {"state": "draft"}
            )
            plan._log_state_change(old_state)

    def _log_state_change(self, old_state):
        self._create_audit_log(
            action_type="state_change",
            description="Treatment plan state changed.",
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
        for plan in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=plan.patient_id.id,
                model_name=plan._name,
                record_id=plan.id,
                action_type=action_type,
                description=description,
                old_value=old_value,
                new_value=new_value,
            )


class HospitalTreatmentPlanLine(models.Model):
    _name = "hospital.treatment.plan.line"
    _description = "Treatment Plan Line"
    _order = "sequence, id"

    treatment_plan_id = fields.Many2one(
        "hospital.treatment.plan",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    treatment_type = fields.Selection(
        [
            ("medication", "Medication"),
            ("procedure", "Procedure"),
            ("lifestyle", "Lifestyle"),
            ("follow_up", "Follow-up"),
            ("therapy", "Therapy"),
            ("nursing_care", "Nursing Care"),
            ("referral", "Referral"),
            ("other", "Other"),
        ],
    )
    description = fields.Char(required=True)
    frequency = fields.Char()
    duration = fields.Char()
    responsible_person = fields.Selection(
        [
            ("doctor", "Doctor"),
            ("nurse", "Nurse"),
            ("patient", "Patient"),
            ("caregiver", "Caregiver"),
            ("other", "Other"),
        ],
    )
    instructions = fields.Text()
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("in_progress", "In Progress"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="pending",
        required=True,
    )

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            for line in self:
                if line.treatment_plan_id:
                    line.treatment_plan_id._create_audit_log(
                        action_type="delete_attempt",
                        description="Treatment plan line deletion blocked.",
                        old_value=f"Line: {line.description}",
                    )
            raise UserError(
                "Treatment plan lines are sensitive health records. Cancel the line instead of deleting it."
            )
        return super().unlink()

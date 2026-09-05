from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalNursingCarePlan(models.Model):
    _name = "hospital.nursing.care.plan"
    _description = "Nursing Care Plan"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "plan_date desc, id desc"

    # Task 27A-2 — clinical fields locked once a care plan is completed or
    # cancelled. Workflow/system fields (state, completed_date, chatter,
    # write_uid/write_date) are intentionally excluded.
    PROTECTED_CARE_PLAN_FIELDS = {
        "patient_id",
        "admission_id",
        "nurse_id",
        "physician_id",
        "plan_date",
        "nursing_diagnosis",
        "goals",
        "interventions",
        "evaluation",
        "priority",
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
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    admission_id = fields.Many2one(
        "hospital.admission",
        required=True,
        ondelete="restrict",
        domain="[('patient_id', '=', patient_id)]",
        tracking=True,
    )
    nurse_id = fields.Many2one(
        "res.users",
        string="Nurse",
        default=lambda self: self.env.user,
        tracking=True,
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        tracking=True,
    )
    plan_date = fields.Date(
        string="Plan Date",
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    nursing_diagnosis = fields.Text(string="Nursing Diagnosis", tracking=True)
    goals = fields.Text(string="Goals")
    interventions = fields.Text(string="Interventions")
    evaluation = fields.Text(string="Evaluation")
    priority = fields.Selection(
        [
            ("low", "Low"),
            ("normal", "Normal"),
            ("high", "High"),
            ("urgent", "Urgent"),
        ],
        string="Priority",
        default="normal",
        tracking=True,
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
        tracking=True,
    )
    active = fields.Boolean(default=True)
    completed_date = fields.Datetime(string="Completed Date", readonly=True)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for rec in self:
            if rec.name and rec.name != "New":
                rec.display_name = rec.name
            elif rec.patient_id:
                rec.display_name = f"New - {rec.patient_id.display_name}"
            else:
                rec.display_name = "New"

    def action_activate(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            old_state = rec.state
            rec.write({"state": "active"})
            rec._create_audit_log(
                action_type="state_change",
                description="Care plan activated.",
                old_value=f"State: {old_state}",
                new_value="State: active",
            )

    def action_complete(self):
        for rec in self.filtered(lambda r: r.state == "active"):
            old_state = rec.state
            rec.write({
                "state": "completed",
                "completed_date": fields.Datetime.now(),
            })
            rec._create_audit_log(
                action_type="state_change",
                description="Care plan completed.",
                old_value=f"State: {old_state}",
                new_value="State: completed",
            )

    def action_cancel(self):
        for rec in self.filtered(lambda r: r.state in ("draft", "active")):
            old_state = rec.state
            rec.write({"state": "cancelled"})
            rec._create_audit_log(
                action_type="state_change",
                description="Care plan cancelled.",
                old_value=f"State: {old_state}",
                new_value="State: cancelled",
            )

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state == "cancelled"):
            rec.write({"state": "draft"})
            rec._create_audit_log(
                action_type="state_change",
                description="Care plan reset to draft.",
                old_value="State: cancelled",
                new_value="State: draft",
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.nursing.care.plan.sequence") or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Nursing care plan created.",
                new_value=record._audit_summary(["name", "patient_id", "admission_id", "priority"]),
            )
        return records

    def write(self, vals):
        protected_changed = self.PROTECTED_CARE_PLAN_FIELDS.intersection(vals.keys())
        if protected_changed:
            for rec in self:
                if rec.state in ("completed", "cancelled"):
                    raise UserError(
                        "Completed or cancelled care plans are locked. "
                        "Reset to Draft before making clinical corrections."
                    )
        tracked = {k: v for k, v in vals.items() if k not in ("write_date", "write_uid", "display_name")}
        old_values = {rec.id: rec._audit_summary(tracked.keys()) for rec in self}
        result = super().write(vals)
        if tracked and "state" not in tracked:
            for rec in self:
                rec._create_audit_log(
                    action_type="update",
                    description="Care plan updated.",
                    old_value=old_values.get(rec.id),
                    new_value=rec._audit_summary(tracked.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                rec._create_audit_log(
                    action_type="delete_attempt",
                    description="Deletion of care plan blocked.",
                    old_value=rec._audit_summary(["name", "state"]),
                )
            raise UserError(
                "Nursing records are sensitive clinical data. Cancel or archive instead of deleting."
            )
        return super().unlink()

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

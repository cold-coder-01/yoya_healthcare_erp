from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalNursingRound(models.Model):
    _name = "hospital.nursing.round"
    _description = "Nursing Round"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "round_datetime desc, id desc"

    # Task 27A-1 — clinical fields that must not be modified once a round is
    # reviewed or cancelled. Workflow/system fields (state, reviewed_by,
    # reviewed_date, chatter, write_uid/write_date) are intentionally excluded.
    PROTECTED_CLINICAL_FIELDS = {
        "patient_id",
        "admission_id",
        "nurse_id",
        "round_datetime",
        "consciousness_level",
        "temperature",
        "pulse_rate",
        "respiratory_rate",
        "systolic_bp",
        "diastolic_bp",
        "oxygen_saturation",
        "blood_sugar",
        "pain_level",
        "intake_notes",
        "output_notes",
        "nursing_observation",
        "action_taken",
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
    round_datetime = fields.Datetime(
        string="Round Date/Time",
        default=fields.Datetime.now,
        required=True,
        tracking=True,
    )
    consciousness_level = fields.Selection(
        [
            ("alert", "Alert"),
            ("voice_response", "Voice Response"),
            ("pain_response", "Pain Response"),
            ("unresponsive", "Unresponsive"),
        ],
        string="Consciousness Level",
        tracking=True,
    )
    temperature = fields.Float(string="Temperature (°C)", tracking=True)
    pulse_rate = fields.Float(string="Pulse Rate (bpm)", tracking=True)
    respiratory_rate = fields.Float(string="Respiratory Rate (breaths/min)", tracking=True)
    systolic_bp = fields.Float(string="Systolic BP (mmHg)", tracking=True)
    diastolic_bp = fields.Float(string="Diastolic BP (mmHg)", tracking=True)
    oxygen_saturation = fields.Float(string="Oxygen Saturation (%)", tracking=True)
    blood_sugar = fields.Float(string="Blood Sugar (mg/dL)", tracking=True)
    pain_level = fields.Selection(
        [(str(i), str(i)) for i in range(11)],
        string="Pain Level (0-10)",
        tracking=True,
    )
    intake_notes = fields.Text(string="Intake Notes")
    output_notes = fields.Text(string="Output Notes")
    nursing_observation = fields.Text(string="Nursing Observation", tracking=True)
    action_taken = fields.Text(string="Action Taken")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("recorded", "Recorded"),
            ("reviewed", "Reviewed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    active = fields.Boolean(default=True)
    reviewed_by = fields.Many2one("res.users", string="Reviewed By", readonly=True)
    reviewed_date = fields.Datetime(string="Reviewed Date", readonly=True)

    @api.depends("name", "patient_id")
    def _compute_display_name(self):
        for rec in self:
            if rec.name and rec.name != "New":
                rec.display_name = rec.name
            elif rec.patient_id:
                rec.display_name = f"New - {rec.patient_id.display_name}"
            else:
                rec.display_name = "New"

    @api.constrains("oxygen_saturation")
    def _check_oxygen_saturation(self):
        for rec in self:
            if rec.oxygen_saturation and not (0 <= rec.oxygen_saturation <= 100):
                raise UserError("Oxygen saturation must be between 0 and 100.")

    @api.constrains("systolic_bp", "diastolic_bp")
    def _check_bp(self):
        for rec in self:
            if rec.systolic_bp < 0:
                raise UserError("Systolic BP cannot be negative.")
            if rec.diastolic_bp < 0:
                raise UserError("Diastolic BP cannot be negative.")

    @api.constrains("pulse_rate", "temperature", "respiratory_rate")
    def _check_vitals_positive(self):
        for rec in self:
            if rec.pulse_rate < 0:
                raise UserError("Pulse rate cannot be negative.")
            if rec.temperature < 0:
                raise UserError("Temperature cannot be negative.")
            if rec.respiratory_rate < 0:
                raise UserError("Respiratory rate cannot be negative.")

    def action_record_round(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            old_state = rec.state
            rec.write({"state": "recorded"})
            rec._create_audit_log(
                action_type="state_change",
                description="Nursing round recorded.",
                old_value=f"State: {old_state}",
                new_value="State: recorded",
            )

    def action_review(self):
        for rec in self.filtered(lambda r: r.state == "recorded"):
            old_state = rec.state
            rec.write({
                "state": "reviewed",
                "reviewed_by": self.env.user.id,
                "reviewed_date": fields.Datetime.now(),
            })
            rec._create_audit_log(
                action_type="state_change",
                description="Nursing round reviewed.",
                old_value=f"State: {old_state}",
                new_value="State: reviewed",
            )

    def action_cancel(self):
        for rec in self.filtered(lambda r: r.state in ("draft", "recorded")):
            old_state = rec.state
            rec.write({"state": "cancelled"})
            rec._create_audit_log(
                action_type="state_change",
                description="Nursing round cancelled.",
                old_value=f"State: {old_state}",
                new_value="State: cancelled",
            )

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state == "cancelled"):
            rec.write({"state": "draft"})
            rec._create_audit_log(
                action_type="state_change",
                description="Nursing round reset to draft.",
                old_value="State: cancelled",
                new_value="State: draft",
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.nursing.round.sequence") or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Nursing round created.",
                new_value=record._audit_summary(["name", "patient_id", "admission_id", "round_datetime"]),
            )
        return records

    def write(self, vals):
        protected_changed = self.PROTECTED_CLINICAL_FIELDS.intersection(vals.keys())
        if protected_changed:
            for rec in self:
                if rec.state in ("reviewed", "cancelled"):
                    raise UserError(
                        "Reviewed or cancelled nursing rounds are locked. "
                        "Reset to Draft before making clinical corrections."
                    )
        tracked = {k: v for k, v in vals.items() if k not in ("write_date", "write_uid", "display_name")}
        old_values = {
            rec.id: rec._audit_summary(tracked.keys())
            for rec in self
        }
        result = super().write(vals)
        if tracked and "state" not in tracked:
            for rec in self:
                rec._create_audit_log(
                    action_type="update",
                    description="Nursing round updated.",
                    old_value=old_values.get(rec.id),
                    new_value=rec._audit_summary(tracked.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                rec._create_audit_log(
                    action_type="delete_attempt",
                    description="Deletion of nursing round blocked.",
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

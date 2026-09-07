from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalNursingNote(models.Model):
    _name = "hospital.nursing.note"
    _description = "Nursing Note"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "note_datetime desc, id desc"

    # Task 27A-2 — clinical fields locked once a note is reviewed or cancelled.
    # Workflow/system fields (state, reviewed_by, reviewed_date, chatter,
    # write_uid/write_date) are intentionally excluded.
    PROTECTED_NOTE_FIELDS = {
        "patient_id",
        "admission_id",
        "nurse_id",
        "note_datetime",
        "note_type",
        "note",
        "action_required",
        "action_required_note",
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
    note_datetime = fields.Datetime(
        string="Note Date/Time",
        default=fields.Datetime.now,
        required=True,
        tracking=True,
    )
    note_type = fields.Selection(
        [
            ("general", "General"),
            ("observation", "Observation"),
            ("intervention", "Intervention"),
            ("incident", "Incident"),
            ("patient_complaint", "Patient Complaint"),
            ("family_communication", "Family Communication"),
            ("discharge_preparation", "Discharge Preparation"),
        ],
        string="Note Type",
        default="general",
        tracking=True,
    )
    note = fields.Text(required=True, string="Note", tracking=True)
    action_required = fields.Boolean(string="Action Required", tracking=True)
    action_required_note = fields.Text(string="Action Details")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
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

    def action_submit(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            old_state = rec.state
            rec.write({"state": "submitted"})
            rec._create_audit_log(
                action_type="state_change",
                description="Nursing note submitted.",
                old_value=f"State: {old_state}",
                new_value="State: submitted",
            )

    def action_review(self):
        for rec in self.filtered(lambda r: r.state == "submitted"):
            old_state = rec.state
            rec.write({
                "state": "reviewed",
                "reviewed_by": self.env.user.id,
                "reviewed_date": fields.Datetime.now(),
            })
            rec._create_audit_log(
                action_type="state_change",
                description="Nursing note reviewed.",
                old_value=f"State: {old_state}",
                new_value="State: reviewed",
            )

    def action_cancel(self):
        for rec in self.filtered(lambda r: r.state in ("draft", "submitted")):
            old_state = rec.state
            rec.write({"state": "cancelled"})
            rec._create_audit_log(
                action_type="state_change",
                description="Nursing note cancelled.",
                old_value=f"State: {old_state}",
                new_value="State: cancelled",
            )

    def action_reset_to_draft(self):
        for rec in self.filtered(lambda r: r.state == "cancelled"):
            rec.write({"state": "draft"})
            rec._create_audit_log(
                action_type="state_change",
                description="Nursing note reset to draft.",
                old_value="State: cancelled",
                new_value="State: draft",
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.nursing.note.sequence") or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Nursing note created.",
                new_value=record._audit_summary(["name", "patient_id", "admission_id", "note_type"]),
            )
        return records

    def write(self, vals):
        protected_changed = self.PROTECTED_NOTE_FIELDS.intersection(vals.keys())
        if protected_changed:
            for rec in self:
                if rec.state in ("reviewed", "cancelled"):
                    raise UserError(
                        "Reviewed or cancelled nursing notes are locked. "
                        "Reset to Draft before making clinical corrections."
                    )
        tracked = {k: v for k, v in vals.items() if k not in ("write_date", "write_uid", "display_name")}
        old_values = {rec.id: rec._audit_summary(tracked.keys()) for rec in self}
        result = super().write(vals)
        if tracked and "state" not in tracked:
            for rec in self:
                rec._create_audit_log(
                    action_type="update",
                    description="Nursing note updated.",
                    old_value=old_values.get(rec.id),
                    new_value=rec._audit_summary(tracked.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                rec._create_audit_log(
                    action_type="delete_attempt",
                    description="Deletion of nursing note blocked.",
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

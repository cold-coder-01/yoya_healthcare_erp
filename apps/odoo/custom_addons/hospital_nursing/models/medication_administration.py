from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalMedicationAdministration(models.Model):
    _name = "hospital.medication.administration"
    _description = "Medication Administration Record"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "scheduled_datetime desc, id desc"

    # Task 27A-2 — clinical fields locked once a MAR record reaches a final
    # status. "held" is intentionally NOT final: medication may later be
    # resumed, cancelled, or corrected. The administration_status workflow
    # field is intentionally excluded.
    PROTECTED_MAR_FIELDS = {
        "patient_id",
        "admission_id",
        "prescription_id",
        "medication_name",
        "dose",
        "route",
        "scheduled_datetime",
        "administered_datetime",
        "nurse_id",
        "reason_not_administered",
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
    prescription_id = fields.Many2one(
        "hospital.prescription",
        string="Prescription",
        domain="[('patient_id', '=', patient_id)]",
        tracking=True,
    )
    medication_name = fields.Char(required=True, string="Medication", tracking=True)
    dose = fields.Char(string="Dose", tracking=True)
    route = fields.Selection(
        [
            ("oral", "Oral"),
            ("iv", "Intravenous (IV)"),
            ("im", "Intramuscular (IM)"),
            ("sc", "Subcutaneous (SC)"),
            ("topical", "Topical"),
            ("inhalation", "Inhalation"),
            ("other", "Other"),
        ],
        string="Route",
        tracking=True,
    )
    scheduled_datetime = fields.Datetime(string="Scheduled Date/Time", tracking=True)
    administered_datetime = fields.Datetime(string="Administered Date/Time", tracking=True)
    nurse_id = fields.Many2one(
        "res.users",
        string="Nurse",
        default=lambda self: self.env.user,
        tracking=True,
    )
    administration_status = fields.Selection(
        [
            ("scheduled", "Scheduled"),
            ("administered", "Administered"),
            ("missed", "Missed"),
            ("refused", "Refused"),
            ("held", "Held"),
            ("cancelled", "Cancelled"),
        ],
        string="Status",
        default="scheduled",
        required=True,
        tracking=True,
    )
    reason_not_administered = fields.Text(string="Reason Not Administered")
    notes = fields.Text(string="Notes")
    active = fields.Boolean(default=True)

    @api.depends("name", "patient_id", "medication_name")
    def _compute_display_name(self):
        for rec in self:
            if rec.name and rec.name != "New":
                rec.display_name = rec.name
            elif rec.patient_id and rec.medication_name:
                rec.display_name = f"New - {rec.patient_id.display_name} / {rec.medication_name}"
            elif rec.patient_id:
                rec.display_name = f"New - {rec.patient_id.display_name}"
            else:
                rec.display_name = "New"

    def action_mark_administered(self):
        for rec in self.filtered(lambda r: r.administration_status == "scheduled"):
            old_status = rec.administration_status
            rec.write({
                "administration_status": "administered",
                "administered_datetime": rec.administered_datetime or fields.Datetime.now(),
            })
            rec._create_audit_log(
                action_type="state_change",
                description="Medication marked as administered.",
                old_value=f"Status: {old_status}",
                new_value="Status: administered",
            )

    def action_mark_missed(self):
        for rec in self.filtered(lambda r: r.administration_status == "scheduled"):
            if not rec.reason_not_administered:
                raise UserError("Please provide a reason why this medication was missed.")
            old_status = rec.administration_status
            rec.write({"administration_status": "missed"})
            rec._create_audit_log(
                action_type="state_change",
                description="Medication marked as missed.",
                old_value=f"Status: {old_status}",
                new_value="Status: missed",
            )

    def action_mark_refused(self):
        for rec in self.filtered(lambda r: r.administration_status == "scheduled"):
            if not rec.reason_not_administered:
                raise UserError("Please provide a reason why this medication was refused.")
            old_status = rec.administration_status
            rec.write({"administration_status": "refused"})
            rec._create_audit_log(
                action_type="state_change",
                description="Medication marked as refused.",
                old_value=f"Status: {old_status}",
                new_value="Status: refused",
            )

    def action_hold(self):
        for rec in self.filtered(lambda r: r.administration_status == "scheduled"):
            if not rec.reason_not_administered:
                raise UserError("Please provide a reason for holding this medication.")
            old_status = rec.administration_status
            rec.write({"administration_status": "held"})
            rec._create_audit_log(
                action_type="state_change",
                description="Medication held.",
                old_value=f"Status: {old_status}",
                new_value="Status: held",
            )

    def action_cancel(self):
        for rec in self.filtered(lambda r: r.administration_status in ("scheduled", "held")):
            old_status = rec.administration_status
            rec.write({"administration_status": "cancelled"})
            rec._create_audit_log(
                action_type="state_change",
                description="Medication administration cancelled.",
                old_value=f"Status: {old_status}",
                new_value="Status: cancelled",
            )

    def action_reset_to_scheduled(self):
        for rec in self.filtered(lambda r: r.administration_status in ("missed", "refused", "held", "cancelled")):
            old_status = rec.administration_status
            rec.write({"administration_status": "scheduled"})
            rec._create_audit_log(
                action_type="state_change",
                description="Medication administration reset to scheduled.",
                old_value=f"Status: {old_status}",
                new_value="Status: scheduled",
            )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("hospital.medication.administration.sequence") or "New"
                )
        records = super().create(vals_list)
        for record in records:
            record._create_audit_log(
                action_type="create",
                description="Medication administration record created.",
                new_value=record._audit_summary(["name", "patient_id", "admission_id", "medication_name", "route"]),
            )
        return records

    def write(self, vals):
        protected_changed = self.PROTECTED_MAR_FIELDS.intersection(vals.keys())
        if protected_changed:
            for rec in self:
                if rec.administration_status in ("administered", "missed", "refused", "cancelled"):
                    raise UserError(
                        "Finalized medication administration records are locked. "
                        "Reset to Scheduled before making corrections."
                    )
        tracked = {k: v for k, v in vals.items() if k not in ("write_date", "write_uid", "display_name")}
        old_values = {rec.id: rec._audit_summary(tracked.keys()) for rec in self}
        result = super().write(vals)
        if tracked and "administration_status" not in tracked:
            for rec in self:
                rec._create_audit_log(
                    action_type="update",
                    description="Medication administration record updated.",
                    old_value=old_values.get(rec.id),
                    new_value=rec._audit_summary(tracked.keys()),
                )
        return result

    def unlink(self):
        if not self.env.user.has_group("hospital_management.group_hospital_system_administrator"):
            for rec in self:
                rec._create_audit_log(
                    action_type="delete_attempt",
                    description="Deletion of MAR record blocked.",
                    old_value=rec._audit_summary(["name", "administration_status"]),
                )
            raise UserError(
                "Medication administration records are sensitive clinical data. Cancel or archive instead."
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

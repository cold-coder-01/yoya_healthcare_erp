from odoo import api, fields, models


class HospitalPostopNote(models.Model):
    _name = "hospital.postop.note"
    _description = "Post-operative Note"
    _inherit = ["mail.thread"]
    _order = "note_date desc, id desc"

    name = fields.Char(readonly=True, copy=False, default="New", tracking=True)
    surgery_id = fields.Many2one(
        "hospital.surgery.request",
        string="Surgery",
        required=True,
        ondelete="cascade",
    )
    patient_id = fields.Many2one(
        "hospital.patient",
        string="Patient",
        related="surgery_id.patient_id",
        store=True,
        readonly=True,
    )
    note_date = fields.Datetime(string="Note Date", default=fields.Datetime.now)
    recorded_by = fields.Many2one(
        "res.users",
        string="Recorded By",
        default=lambda self: self.env.user,
    )
    recovery_status = fields.Selection(
        [
            ("stable", "Stable"),
            ("observation_needed", "Observation Needed"),
            ("critical", "Critical"),
            ("transferred_to_ward", "Transferred to Ward"),
            ("transferred_to_icu", "Transferred to ICU"),
        ],
        string="Recovery Status",
        default="stable",
    )
    pain_score = fields.Integer(string="Pain Score (0-10)")
    vitals_summary = fields.Text(string="Vitals Summary")
    wound_condition = fields.Text(string="Wound Condition")
    bleeding_status = fields.Text(string="Bleeding Status")
    medication_orders = fields.Text(string="Medication Orders")
    nursing_instructions = fields.Text(string="Nursing Instructions")
    surgeon_instructions = fields.Text(string="Surgeon Instructions")
    follow_up_plan = fields.Text(string="Follow-up Plan")
    notes = fields.Text()

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

    @api.constrains("pain_score")
    def _check_pain_score(self):
        from odoo.exceptions import ValidationError

        for rec in self:
            if rec.pain_score < 0 or rec.pain_score > 10:
                raise ValidationError("Pain score must be between 0 and 10.")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code(
                        "hospital.postop.note.sequence"
                    )
                    or "New"
                )
        return super().create(vals_list)

    def action_submit(self):
        self.filtered(lambda r: r.state == "draft").write({"state": "submitted"})

    def action_review(self):
        self.filtered(lambda r: r.state == "submitted").write({"state": "reviewed"})

    def action_cancel(self):
        self.filtered(lambda r: r.state != "cancelled").write({"state": "cancelled"})

    def action_reset_to_draft(self):
        self.filtered(lambda r: r.state == "cancelled").write({"state": "draft"})

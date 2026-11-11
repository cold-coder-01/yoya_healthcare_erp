from odoo import api, fields, models


class HospitalPreopChecklist(models.Model):
    _name = "hospital.preop.checklist"
    _description = "Pre-operative Checklist"
    _inherit = ["mail.thread"]
    _order = "checklist_date desc, id desc"

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
    checklist_date = fields.Datetime(string="Checklist Date", default=fields.Datetime.now)
    completed_by = fields.Many2one("res.users", string="Completed By")

    identity_confirmed = fields.Boolean(string="Patient Identity Confirmed")
    consent_verified = fields.Boolean(string="Consent Verified")
    fasting_confirmed = fields.Boolean(string="Fasting Confirmed")
    allergies_checked = fields.Boolean(string="Allergies Checked")
    medication_reviewed = fields.Boolean(string="Medication Reviewed")
    vitals_checked = fields.Boolean(string="Vitals Checked")
    lab_results_checked = fields.Boolean(string="Lab Results Checked")
    blood_prepared = fields.Boolean(string="Blood Prepared")
    site_marked = fields.Boolean(string="Surgical Site Marked")
    anesthesia_assessment_done = fields.Boolean(string="Anesthesia Assessment Done")
    equipment_ready = fields.Boolean(string="Equipment Ready")
    surgical_instruments_ready = fields.Boolean(string="Surgical Instruments Ready")
    infection_prevention_done = fields.Boolean(string="Infection Prevention Done")
    checklist_notes = fields.Text(string="Checklist Notes")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code(
                        "hospital.preop.checklist.sequence"
                    )
                    or "New"
                )
        return super().create(vals_list)

    def action_complete(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            rec.write(
                {
                    "state": "completed",
                    "completed_by": self.env.user.id,
                }
            )
            if rec.surgery_id and rec.surgery_id.preop_status != "completed":
                rec.surgery_id.preop_status = "completed"

    def action_cancel(self):
        self.filtered(lambda r: r.state != "cancelled").write({"state": "cancelled"})

    def action_reset_to_draft(self):
        self.filtered(lambda r: r.state == "cancelled").write({"state": "draft"})

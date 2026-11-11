from odoo import api, fields, models


class HospitalAnesthesiaNote(models.Model):
    _name = "hospital.anesthesia.note"
    _description = "Anesthesia Note"
    _inherit = ["mail.thread"]
    _order = "id desc"

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
    anesthesia_type = fields.Selection(
        [
            ("general", "General"),
            ("spinal", "Spinal"),
            ("epidural", "Epidural"),
            ("local", "Local"),
            ("sedation", "Sedation"),
            ("regional", "Regional"),
            ("none", "None"),
            ("other", "Other"),
        ],
        string="Anesthesia Type",
        default="general",
    )
    anesthetist_id = fields.Many2one(
        "hospital.doctor",
        string="Anesthetist",
        ondelete="set null",
    )
    start_time = fields.Datetime(string="Start Time")
    end_time = fields.Datetime(string="End Time")
    pre_anesthesia_assessment = fields.Text(string="Pre-anesthesia Assessment")
    airway_assessment = fields.Text(string="Airway Assessment")
    medications_given = fields.Text(string="Medications Given")
    intraoperative_monitoring = fields.Text(string="Intraoperative Monitoring")
    complications = fields.Text(string="Complications")
    recovery_condition = fields.Text(string="Recovery Condition")
    notes = fields.Text()

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("documented", "Documented"),
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
                        "hospital.anesthesia.note.sequence"
                    )
                    or "New"
                )
        return super().create(vals_list)

    def action_document(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            rec.write({"state": "documented"})
            if rec.surgery_id and rec.surgery_id.anesthesia_status != "documented":
                rec.surgery_id.anesthesia_status = "documented"

    def action_cancel(self):
        self.filtered(lambda r: r.state != "cancelled").write({"state": "cancelled"})

    def action_reset_to_draft(self):
        self.filtered(lambda r: r.state == "cancelled").write({"state": "draft"})

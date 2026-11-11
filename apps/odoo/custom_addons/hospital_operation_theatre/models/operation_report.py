from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalOperationReport(models.Model):
    _name = "hospital.operation.report"
    _description = "Operation Report"
    _inherit = ["mail.thread"]
    _order = "operation_date desc, id desc"

    # Clinical fields locked once the report is finalized or cancelled.
    PROTECTED_FIELDS = {
        "surgeon_id",
        "operation_date",
        "preoperative_diagnosis",
        "postoperative_diagnosis",
        "procedure_performed",
        "incision",
        "operative_findings",
        "surgical_steps",
        "specimens_removed",
        "estimated_blood_loss",
        "complications",
        "implants_used",
        "drains",
        "closure_method",
        "immediate_postop_condition",
        "recommendations",
    }

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
    surgeon_id = fields.Many2one(
        "hospital.doctor",
        string="Surgeon",
        ondelete="set null",
    )
    operation_date = fields.Datetime(string="Operation Date", default=fields.Datetime.now)
    preoperative_diagnosis = fields.Text(string="Pre-operative Diagnosis")
    postoperative_diagnosis = fields.Text(string="Post-operative Diagnosis")
    procedure_performed = fields.Text(string="Procedure Performed")
    incision = fields.Text(string="Incision")
    operative_findings = fields.Text(string="Operative Findings")
    surgical_steps = fields.Text(string="Surgical Steps")
    specimens_removed = fields.Text(string="Specimens Removed")
    estimated_blood_loss = fields.Float(string="Estimated Blood Loss (ml)")
    complications = fields.Text(string="Complications")
    implants_used = fields.Text(string="Implants Used")
    drains = fields.Text(string="Drains")
    closure_method = fields.Text(string="Closure Method")
    immediate_postop_condition = fields.Text(string="Immediate Post-op Condition")
    recommendations = fields.Text(string="Recommendations")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("finalized", "Finalized"),
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
                        "hospital.operation.report.sequence"
                    )
                    or "New"
                )
        return super().create(vals_list)

    def write(self, vals):
        protected_changed = self.PROTECTED_FIELDS.intersection(vals.keys())
        if protected_changed:
            for rec in self:
                if rec.state in ("finalized", "cancelled"):
                    raise UserError(
                        "Finalized or cancelled operation reports are locked. "
                        "Reset to Draft before making corrections."
                    )
        return super().write(vals)

    def action_finalize(self):
        self.filtered(lambda r: r.state == "draft").write({"state": "finalized"})

    def action_cancel(self):
        self.filtered(lambda r: r.state != "cancelled").write({"state": "cancelled"})

    def action_reset_to_draft(self):
        self.filtered(lambda r: r.state in ("finalized", "cancelled")).write(
            {"state": "draft"}
        )

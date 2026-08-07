from odoo import api, fields, models


class HospitalPatientEvaluation(models.Model):
    _name = "hospital.patient.evaluation"
    _description = "Patient Evaluation"
    _order = "evaluation_date desc, id desc"

    name = fields.Char(readonly=True, copy=False, default="New")
    patient_id = fields.Many2one(
        "hospital.patient",
        required=True,
        tracking=True,
        ondelete="restrict",
    )
    physician_id = fields.Many2one(
        "hospital.doctor",
        string="Physician",
        tracking=True,
    )
    appointment_id = fields.Many2one(
        "hospital.appointment",
        string="Appointment",
    )
    evaluation_date = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
        tracking=True,
    )
    age = fields.Integer(related="patient_id.age", readonly=True)
    patient_image_1920 = fields.Image(
        related="patient_id.image_1920",
        readonly=True,
    )
    weight = fields.Float(string="Weight")
    height = fields.Float(string="Height")
    temperature = fields.Float(string="Temp")
    heart_rate = fields.Float(string="HR")
    respiratory_rate = fields.Float(string="RR")
    systolic_bp = fields.Float(string="Systolic BP")
    diastolic_bp = fields.Float(string="Diastolic BP")
    spo2 = fields.Float(string="SpO2")
    rbs = fields.Float(string="RBS")
    head_circumference = fields.Float(string="Head Circumference")
    bmi = fields.Float(
        compute="_compute_bmi",
        store=True,
        readonly=True,
        string="Body Mass Index",
    )
    bmi_state = fields.Selection(
        [
            ("underweight", "Underweight"),
            ("normal", "Normal"),
            ("overweight", "Overweight"),
            ("obese", "Obese"),
        ],
        compute="_compute_bmi_state",
        store=True,
        readonly=True,
        string="BMI State",
    )
    pain_level = fields.Selection(
        [(str(level), str(level)) for level in range(11)],
        string="Pain Level",
        help=(
            "0 - No pain; 1-2 - Mild pain; 3-4 - Moderate pain; "
            "5-6 - Moderate to severe pain; 7-8 - Intense pain; "
            "9 - Excruciating pain; 10 - Worst possible pain."
        ),
    )
    pain_note = fields.Text()
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("done", "Done"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
    )
    active = fields.Boolean(default=True)

    @api.depends("weight", "height")
    def _compute_bmi(self):
        for evaluation in self:
            if evaluation.weight and evaluation.height:
                height_m = evaluation.height / 100
                evaluation.bmi = evaluation.weight / (height_m * height_m)
            else:
                evaluation.bmi = 0

    @api.depends("bmi")
    def _compute_bmi_state(self):
        for evaluation in self:
            if not evaluation.bmi:
                evaluation.bmi_state = False
            elif evaluation.bmi < 18.5:
                evaluation.bmi_state = "underweight"
            elif evaluation.bmi < 25:
                evaluation.bmi_state = "normal"
            elif evaluation.bmi < 30:
                evaluation.bmi_state = "overweight"
            else:
                evaluation.bmi_state = "obese"

    @api.model_create_multi
    def create(self, vals_list):
        evaluations = super().create(vals_list)
        for evaluation in evaluations:
            if evaluation.name == "New":
                evaluation.name = f"EVAL{evaluation.id:05d}"
            evaluation._create_audit_log("Patient evaluation created.")
        return evaluations

    def action_done(self):
        for evaluation in self.filtered(lambda record: record.state == "draft"):
            evaluation.state = "done"
            evaluation._create_audit_log("Patient evaluation marked done.")

    def action_cancel(self):
        for evaluation in self.filtered(lambda record: record.state != "cancelled"):
            evaluation.state = "cancelled"

    def action_reset_to_draft(self):
        for evaluation in self.filtered(lambda record: record.state in ("done", "cancelled")):
            evaluation.state = "draft"

    def action_open_pain_level_guide(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Pain Level",
            "res_model": "hospital.pain.level.guide",
            "view_mode": "form",
            "target": "new",
            "context": {},
        }

    def _create_audit_log(self, description):
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        for evaluation in self:
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=evaluation.patient_id.id,
                model_name=evaluation._name,
                record_id=evaluation.id,
                action_type="update",
                description=description,
                new_value=f"Evaluation: {evaluation.display_name}",
            )

from odoo import api, fields, models


class HospitalPatient(models.Model):
    _name = "hospital.patient"
    _description = "Patient"
    _order = "identification_code desc, name"

    identification_code = fields.Char(
        required=True,
        readonly=True,
        copy=False,
        default="New",
    )
    image_1920 = fields.Image(
        
        max_width=1920,
        max_height=1920,
    )
    is_favorite = fields.Boolean(string="Favorite")
    title = fields.Char(string="Title")
    name = fields.Char(required=True, tracking=True)
    date_of_birth = fields.Date(tracking=True)
    age = fields.Integer(compute="_compute_age", store=True, readonly=True)
    gender = fields.Selection(
        [
            ("male", "Male"),
            ("female", "Female"),
            
        ],
        tracking=True,
    )
    phone = fields.Char()
    mobile = fields.Char()
    email = fields.Char()
    address = fields.Text()
    city = fields.Char(string="City")
    state = fields.Char(string="State")
    zip_code = fields.Char(string="ZIP")
    country = fields.Char(string="Country")
    nationality = fields.Char()
    government_identity = fields.Char(
        help="National ID, passport number, or other government-issued identity reference."
    )
    passport_number = fields.Char(string="Passport Number")
    marital_status = fields.Selection(
        [
            ("single", "Single"),
            ("married", "Married"),
            ("divorced", "Divorced"),
            ("widowed", "Widowed"),
        ]
    )
    occupation = fields.Char()
    patient_education = fields.Char(string="Patient Education")
    religion = fields.Char(string="Religion")
    primary_care_doctor_id = fields.Many2one(
        "hospital.doctor",
        string="Primary Care Doctor",
        tracking=True,
    )
    department_ids = fields.Many2many(
        "hospital.department",
        string="Departments",
    )
    blood_group = fields.Selection(
        [
            ("a_positive", "A+"),
            ("a_negative", "A-"),
            ("b_positive", "B+"),
            ("b_negative", "B-"),
            ("ab_positive", "AB+"),
            ("ab_negative", "AB-"),
            ("o_positive", "O+"),
            ("o_negative", "O-"),
            ("unknown", "Unknown"),
        ],
        default="unknown",
    )
    medical_alert_ids = fields.Many2many(
        "hospital.medical.alert",
        string="Medical Alerts",
        tracking=True,
    )
    patient_tag_ids = fields.Many2many(
        "hospital.patient.tag",
        string="Patient Tags",
    )
    family_member_ids = fields.One2many(
        "hospital.patient.family",
        "patient_id",
        string="Family Members",
    )
    family_member_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Family Members",
    )
    document_ids = fields.One2many(
        "hospital.patient.document",
        "patient_id",
        string="Documents",
    )
    document_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Documents",
    )
    diagnosis_ids = fields.One2many(
        "hospital.patient.diagnosis",
        "patient_id",
        string="Diagnoses",
    )
    diagnosis_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Diagnoses",
    )
    disease_history = fields.Text()
    past_medical_history = fields.Text()
    family_history = fields.Text()
    emergency_contact_name = fields.Char(string="Emergency Contact Name")
    emergency_contact_phone = fields.Char(string="Emergency Contact Phone")
    notes = fields.Text()
    active = fields.Boolean(default=True)
    prescription_ids = fields.One2many(
        "hospital.prescription",
        "patient_id",
        string="Prescriptions",
    )
    treatment_plan_ids = fields.One2many(
        "hospital.treatment.plan",
        "patient_id",
        string="Treatment Plans",
    )
    laboratory_request_ids = fields.One2many(
        "hospital.laboratory.request",
        "patient_id",
        string="Laboratory Requests",
    )
    laboratory_result_ids = fields.One2many(
        "hospital.laboratory.result",
        "patient_id",
        string="Laboratory Results",
    )
    appointment_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Appointments",
    )
    consent_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Consents",
    )
    evaluation_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Patient Evaluations",
    )
    prescription_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Prescriptions",
    )
    treatment_plan_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Treatment Plans",
    )
    laboratory_request_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Laboratory Requests",
    )
    laboratory_result_count = fields.Integer(
        compute="_compute_related_record_counts",
        string="Laboratory Results",
    )
    latest_evaluation_id = fields.Many2one(
        "hospital.patient.evaluation",
        compute="_compute_latest_evaluation",
        string="Latest Evaluation",
        readonly=True,
    )
    latest_weight = fields.Float(
        compute="_compute_latest_evaluation",
        string="Weight",
        readonly=True,
    )
    latest_height = fields.Float(
        compute="_compute_latest_evaluation",
        string="Height",
        readonly=True,
    )
    latest_temperature = fields.Float(
        compute="_compute_latest_evaluation",
        string="Temp",
        readonly=True,
    )
    latest_heart_rate = fields.Float(
        compute="_compute_latest_evaluation",
        string="HR",
        readonly=True,
    )
    latest_respiratory_rate = fields.Float(
        compute="_compute_latest_evaluation",
        string="RR",
        readonly=True,
    )
    latest_systolic_bp = fields.Float(
        compute="_compute_latest_evaluation",
        string="Systolic BP",
        readonly=True,
    )
    latest_diastolic_bp = fields.Float(
        compute="_compute_latest_evaluation",
        string="Diastolic BP",
        readonly=True,
    )
    latest_spo2 = fields.Float(
        compute="_compute_latest_evaluation",
        string="SpO2",
        readonly=True,
    )
    latest_rbs = fields.Float(
        compute="_compute_latest_evaluation",
        string="RBS",
        readonly=True,
    )
    latest_head_circumference = fields.Float(
        compute="_compute_latest_evaluation",
        string="Head Circumference",
        readonly=True,
    )
    latest_bmi = fields.Float(
        compute="_compute_latest_evaluation",
        string="Body Mass Index",
        readonly=True,
    )
    latest_bmi_state = fields.Selection(
        [
            ("underweight", "Underweight"),
            ("normal", "Normal"),
            ("overweight", "Overweight"),
            ("obese", "Obese"),
        ],
        compute="_compute_latest_evaluation",
        string="BMI State",
        readonly=True,
    )
    latest_pain_level = fields.Selection(
        [(str(level), str(level)) for level in range(11)],
        compute="_compute_latest_evaluation",
        string="Pain Level",
        readonly=True,
    )
    next_appointment_date = fields.Datetime(
        compute="_compute_patient_overview",
        string="Next Appointment",
        readonly=True,
    )
    open_evaluation_count = fields.Integer(
        compute="_compute_patient_overview",
        string="Open Evaluations",
        readonly=True,
    )
    active_medication_count = fields.Integer(
        compute="_compute_patient_overview",
        string="Active Medications",
        readonly=True,
    )
    medical_alert_count = fields.Integer(
        compute="_compute_patient_overview",
        string="Medical Alert Count",
        readonly=True,
    )
    last_visit_date = fields.Datetime(
        compute="_compute_patient_overview",
        string="Last Visit",
        readonly=True,
    )
    last_visit_doctor_id = fields.Many2one(
        "hospital.doctor",
        compute="_compute_patient_overview",
        string="Last Visit Doctor",
        readonly=True,
    )

    @api.depends("identification_code", "name")
    def _compute_display_name(self):
        for patient in self:
            if patient.identification_code and patient.identification_code != "New":
                patient.display_name = f"{patient.identification_code} - {patient.name}"
            else:
                patient.display_name = patient.name or "New Patient"

    @api.depends("date_of_birth")
    def _compute_age(self):
        today = fields.Date.context_today(self)
        for patient in self:
            if not patient.date_of_birth:
                patient.age = 0
                continue
            birth_date = patient.date_of_birth
            patient.age = today.year - birth_date.year - (
                (today.month, today.day) < (birth_date.month, birth_date.day)
            )

    def _compute_related_record_counts(self):
        for patient in self:
            if not patient.id:
                patient.appointment_count = 0
                patient.consent_count = 0
                patient.evaluation_count = 0
                patient.diagnosis_count = 0
                patient.family_member_count = 0
                patient.document_count = 0
                patient.prescription_count = 0
                patient.treatment_plan_count = 0
                patient.laboratory_request_count = 0
                patient.laboratory_result_count = 0
                continue
            patient.appointment_count = self.env["hospital.appointment"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.consent_count = self.env["hospital.patient.consent"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.evaluation_count = self.env["hospital.patient.evaluation"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.diagnosis_count = self.env["hospital.patient.diagnosis"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.family_member_count = self.env["hospital.patient.family"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.document_count = self.env["hospital.patient.document"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.prescription_count = self.env["hospital.prescription"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.treatment_plan_count = self.env["hospital.treatment.plan"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.laboratory_request_count = self.env["hospital.laboratory.request"].search_count(
                [("patient_id", "=", patient.id)]
            )
            patient.laboratory_result_count = self.env["hospital.laboratory.result"].search_count(
                [("patient_id", "=", patient.id)]
            )

    def _compute_latest_evaluation(self):
        fields_to_reset = (
            "latest_weight",
            "latest_height",
            "latest_temperature",
            "latest_heart_rate",
            "latest_respiratory_rate",
            "latest_systolic_bp",
            "latest_diastolic_bp",
            "latest_spo2",
            "latest_rbs",
            "latest_head_circumference",
            "latest_bmi",
            "latest_bmi_state",
            "latest_pain_level",
        )
        for patient in self:
            latest_evaluation = self.env["hospital.patient.evaluation"].search(
                [
                    ("patient_id", "=", patient.id),
                    ("state", "=", "done"),
                ],
                order="evaluation_date desc, id desc",
                limit=1,
            )
            patient.latest_evaluation_id = latest_evaluation
            if not latest_evaluation:
                for field_name in fields_to_reset:
                    patient[field_name] = False
                continue
            patient.latest_weight = latest_evaluation.weight
            patient.latest_height = latest_evaluation.height
            patient.latest_temperature = latest_evaluation.temperature
            patient.latest_heart_rate = latest_evaluation.heart_rate
            patient.latest_respiratory_rate = latest_evaluation.respiratory_rate
            patient.latest_systolic_bp = latest_evaluation.systolic_bp
            patient.latest_diastolic_bp = latest_evaluation.diastolic_bp
            patient.latest_spo2 = latest_evaluation.spo2
            patient.latest_rbs = latest_evaluation.rbs
            patient.latest_head_circumference = latest_evaluation.head_circumference
            patient.latest_bmi = latest_evaluation.bmi
            patient.latest_bmi_state = latest_evaluation.bmi_state
            patient.latest_pain_level = latest_evaluation.pain_level

    @api.depends("medical_alert_ids")
    def _compute_patient_overview(self):
        now = fields.Datetime.now()
        for patient in self:
            patient.medical_alert_count = len(patient.medical_alert_ids)
            if not patient.id:
                patient.next_appointment_date = False
                patient.open_evaluation_count = 0
                patient.active_medication_count = 0
                patient.last_visit_date = False
                patient.last_visit_doctor_id = False
                continue
            next_appointment = self.env["hospital.appointment"].search(
                [
                    ("patient_id", "=", patient.id),
                    ("state", "in", ("draft", "confirmed", "in_consultation")),
                    ("appointment_date", ">=", now),
                ],
                order="appointment_date asc, id asc",
                limit=1,
            )
            patient.next_appointment_date = next_appointment.appointment_date
            patient.open_evaluation_count = self.env[
                "hospital.patient.evaluation"
            ].search_count(
                [("patient_id", "=", patient.id), ("state", "=", "draft")]
            )
            patient.active_medication_count = self.env[
                "hospital.prescription"
            ].search_count(
                [
                    ("patient_id", "=", patient.id),
                    ("state", "in", ("confirmed", "dispensed")),
                ]
            )
            last_visit = self.env["hospital.appointment"].search(
                [
                    ("patient_id", "=", patient.id),
                    ("state", "=", "done"),
                ],
                order="appointment_date desc, id desc",
                limit=1,
            )
            patient.last_visit_date = last_visit.appointment_date
            patient.last_visit_doctor_id = last_visit.doctor_id

    def action_view_appointments(self):
        self.ensure_one()
        Appointment = self.env["hospital.appointment"]
        latest_appointment = Appointment.search(
            [("patient_id", "=", self.id)],
            order="appointment_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Appointment",
            "res_model": "hospital.appointment",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_appointment:
            action["res_id"] = latest_appointment.id
        return action

    def action_view_consents(self):
        self.ensure_one()
        Consent = self.env["hospital.patient.consent"]
        latest_consent = Consent.search(
            [("patient_id", "=", self.id)],
            order="date_given desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Consent",
            "res_model": "hospital.patient.consent",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_consent:
            action["res_id"] = latest_consent.id
        return action

    def action_view_evaluations(self):
        self.ensure_one()
        Evaluation = self.env["hospital.patient.evaluation"]
        latest_evaluation = Evaluation.search(
            [("patient_id", "=", self.id)],
            order="evaluation_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Evaluation",
            "res_model": "hospital.patient.evaluation",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_evaluation:
            action["res_id"] = latest_evaluation.id
        return action

    def action_view_diagnoses(self):
        self.ensure_one()
        Diagnosis = self.env["hospital.patient.diagnosis"]
        latest_diagnosis = Diagnosis.search(
            [("patient_id", "=", self.id)],
            order="diagnosis_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Diagnosis",
            "res_model": "hospital.patient.diagnosis",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_diagnosis:
            action["res_id"] = latest_diagnosis.id
        return action

    def action_view_prescriptions(self):
        self.ensure_one()
        Prescription = self.env["hospital.prescription"]
        latest_prescription = Prescription.search(
            [("patient_id", "=", self.id)],
            order="prescription_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Prescription",
            "res_model": "hospital.prescription",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_prescription:
            action["res_id"] = latest_prescription.id
        return action

    def action_view_treatment_plans(self):
        self.ensure_one()
        TreatmentPlan = self.env["hospital.treatment.plan"]
        latest_treatment_plan = TreatmentPlan.search(
            [("patient_id", "=", self.id)],
            order="plan_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Treatment Plan",
            "res_model": "hospital.treatment.plan",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_treatment_plan:
            action["res_id"] = latest_treatment_plan.id
        return action

    def action_view_laboratory_requests(self):
        self.ensure_one()
        LabRequest = self.env["hospital.laboratory.request"]
        latest_lab_request = LabRequest.search(
            [("patient_id", "=", self.id)],
            order="request_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Laboratory Request",
            "res_model": "hospital.laboratory.request",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_lab_request:
            action["res_id"] = latest_lab_request.id
        return action

    def action_view_laboratory_results(self):
        self.ensure_one()
        LabResult = self.env["hospital.laboratory.result"]
        latest_lab_result = LabResult.search(
            [("patient_id", "=", self.id)],
            order="result_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Laboratory Result",
            "res_model": "hospital.laboratory.result",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_lab_result:
            action["res_id"] = latest_lab_result.id
        return action

    def action_view_family_members(self):
        self.ensure_one()
        FamilyMember = self.env["hospital.patient.family"]
        latest_family_member = FamilyMember.search(
            [("patient_id", "=", self.id)],
            order="id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Family Member",
            "res_model": "hospital.patient.family",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_family_member:
            action["res_id"] = latest_family_member.id
        return action

    def action_view_documents(self):
        self.ensure_one()
        Document = self.env["hospital.patient.document"]
        latest_document = Document.search(
            [("patient_id", "=", self.id)],
            order="document_date desc, id desc",
            limit=1,
        )
        action = {
            "type": "ir.actions.act_window",
            "name": "Patient Document",
            "res_model": "hospital.patient.document",
            "view_mode": "form",
            "target": "current",
            "domain": [("patient_id", "=", self.id)],
            "context": {"default_patient_id": self.id},
        }
        if latest_document:
            action["res_id"] = latest_document.id
        return action

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("identification_code") or vals.get("identification_code") == "New":
                vals["identification_code"] = sequence.next_by_code(
                    "hospital.patient.sequence"
                ) or "New"
        patients = super().create(vals_list)
        for patient in patients:
            patient._create_audit_log(
                action_type="create",
                description="Patient record created.",
                new_value=f"Patient: {patient.display_name}",
            )
        return patients

    def write(self, vals):
        tracked_vals = {
            key: value
            for key, value in vals.items()
            if key not in ("write_date", "write_uid", "display_name")
        }
        old_values = {
            patient.id: patient._audit_summary(tracked_vals.keys())
            for patient in self
        }
        result = super().write(vals)
        if tracked_vals:
            action_type = "archive" if vals.get("active") is False else "update"
            description = (
                "Patient record archived."
                if action_type == "archive"
                else "Patient record updated."
            )
            for patient in self:
                patient._create_audit_log(
                    action_type=action_type,
                    description=description,
                    old_value=old_values.get(patient.id),
                    new_value=patient._audit_summary(tracked_vals.keys()),
                )
        return result

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
        audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
            patient_id=self.id,
            model_name=self._name,
            record_id=self.id,
            action_type=action_type,
            description=description,
            old_value=old_value,
            new_value=new_value,
        )

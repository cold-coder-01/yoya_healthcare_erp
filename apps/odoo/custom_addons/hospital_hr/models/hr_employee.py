from datetime import date

from odoo import _, api, fields, models

STAFF_TYPE_BY_ROLE_TYPE = {
    "medical": "clinical",
    "nursing": "clinical",
    "allied_health": "clinical_support",
    "pharmacy": "clinical_support",
    "administrative": "administrative",
    "support": "support",
}

# Fallback ranking when picking the employee's primary credential.
CREDENTIAL_TYPE_PRIORITY = [
    "medical_license",
    "nursing_license",
    "pharmacy_license",
    "radiology_license",
    "lab_certification",
    "professional_registration",
    "specialty_certification",
    "bls_acls",
    "employment_clearance",
    "other",
]

# Preferred credential type per hospital role code (seeded in Task 38B).
ROLE_PRIMARY_CREDENTIAL_TYPE = {
    "DOC": "medical_license",
    "SURGEON": "medical_license",
    "ANESTH": "medical_license",
    "NURSE": "nursing_license",
    "OT_NURSE": "nursing_license",
    "PHARM": "pharmacy_license",
    "RAD_TECH": "radiology_license",
    "LAB_TECH": "lab_certification",
}


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    hospital_role_id = fields.Many2one(
        "hospital.hr.role",
        string="Hospital Role",
        tracking=True,
        help="Hospital staff category (Doctor, Nurse, Pharmacist, ...). "
        "Complements the Odoo Job Position, which remains the contractual position.",
    )
    hospital_staff_type = fields.Selection(
        [
            ("clinical", "Clinical"),
            ("clinical_support", "Clinical Support"),
            ("administrative", "Administrative"),
            ("support", "Support"),
        ],
        string="Staff Type",
        tracking=True,
    )
    is_clinical_staff = fields.Boolean(
        string="Clinical Staff",
        compute="_compute_is_clinical_staff",
        store=True,
        readonly=False,
        help="Follows the hospital role by default; HR may override.",
    )
    hospital_department_id = fields.Many2one(
        "hospital.department",
        string="Clinical Department",
        tracking=True,
        help="Clinical/service unit (hospital.department). The Odoo Department "
        "(department_id) remains the organizational/payroll department.",
    )
    can_be_assigned_to_duty = fields.Boolean(
        string="Duty Eligible",
        help="Employee can be placed on hospital duty rosters.",
    )
    on_call_eligible = fields.Boolean(string="On-Call Eligible")
    can_perform_clinical_services = fields.Boolean(
        string="Can Perform Clinical Services",
    )
    can_approve_clinical_work = fields.Boolean(
        string="Can Approve Clinical Work",
        help="Senior clinical permission (e.g. validating results or care plans). "
        "Never set automatically from the role.",
    )
    primary_specialty = fields.Char(string="Primary Specialty")
    secondary_specialty = fields.Char(string="Secondary Specialty")
    hospital_notes = fields.Text(
        string="Hospital Notes",
        groups="hr.group_hr_user",
    )
    credential_ids = fields.One2many(
        "hospital.hr.credential",
        "employee_id",
        string="Credentials",
    )
    credential_count = fields.Integer(
        compute="_compute_credential_count",
        compute_sudo=True,
    )
    license_number = fields.Char(
        string="License Number",
        compute="_compute_license_summary",
        compute_sudo=True,
        help="From the employee's primary credential (see credential priority "
        "logic in the module).",
    )
    license_expiry_date = fields.Date(
        string="License Expiry",
        compute="_compute_license_summary",
        compute_sudo=True,
    )
    license_status = fields.Selection(
        [
            ("missing", "Missing"),
            ("pending_verification", "Pending Verification"),
            ("valid", "Valid"),
            ("expiring_soon", "Expiring Soon"),
            ("expired", "Expired"),
            ("suspended", "Suspended"),
            ("no_expiry", "No Expiry"),
        ],
        string="License Status",
        compute="_compute_license_summary",
        compute_sudo=True,
    )
    credential_warning = fields.Char(
        string="Credential Warning",
        compute="_compute_license_summary",
        compute_sudo=True,
    )
    duty_line_ids = fields.One2many(
        "hospital.hr.duty.line",
        "employee_id",
        string="Duty Lines",
    )
    duty_line_count = fields.Integer(
        compute="_compute_duty_line_count",
        compute_sudo=True,
    )
    attendance_log_ids = fields.One2many(
        "hospital.hr.attendance.log",
        "employee_id",
        string="Duty Logs",
    )
    attendance_log_count = fields.Integer(
        compute="_compute_attendance_log_count",
        compute_sudo=True,
    )

    @api.depends("hospital_role_id", "hospital_role_id.clinical_role")
    def _compute_is_clinical_staff(self):
        for employee in self:
            employee.is_clinical_staff = employee.hospital_role_id.clinical_role

    @api.depends("credential_ids.active")
    def _compute_credential_count(self):
        for employee in self:
            employee.credential_count = len(
                employee.credential_ids.filtered("active")
            )

    def _get_primary_credential(self):
        """Pick the credential shown in the employee's license summary.

        Order: (1) credentials matching the role's preferred type
        (ROLE_PRIMARY_CREDENTIAL_TYPE), then (2) CREDENTIAL_TYPE_PRIORITY
        ranking, then (3) earliest expiry (non-expiring last).
        """
        self.ensure_one()
        credentials = self.credential_ids.filtered("active")
        if not credentials:
            return credentials
        preferred = ROLE_PRIMARY_CREDENTIAL_TYPE.get(self.hospital_role_id.code)

        def sort_key(credential):
            try:
                type_rank = CREDENTIAL_TYPE_PRIORITY.index(credential.credential_type)
            except ValueError:
                type_rank = len(CREDENTIAL_TYPE_PRIORITY)
            return (
                0 if preferred and credential.credential_type == preferred else 1,
                type_rank,
                credential.expiry_date or date.max,
            )

        return credentials.sorted(key=sort_key)[:1]

    @api.depends(
        "credential_ids.active",
        "credential_ids.credential_type",
        "credential_ids.license_number",
        "credential_ids.expiry_date",
        "credential_ids.status",
        "hospital_role_id",
    )
    def _compute_license_summary(self):
        today = fields.Date.context_today(self)
        for employee in self:
            credential = employee._get_primary_credential()
            if not credential:
                employee.license_number = False
                employee.license_expiry_date = False
                if employee.hospital_role_id.requires_license:
                    employee.license_status = "missing"
                    employee.credential_warning = _("No credential on file.")
                else:
                    employee.license_status = False
                    employee.credential_warning = False
                continue
            employee.license_number = credential.license_number
            employee.license_expiry_date = credential.expiry_date
            employee.license_status = credential.status
            if credential.status == "expiring_soon":
                days = (credential.expiry_date - today).days
                employee.credential_warning = _(
                    "License expires in %(days)s days.", days=days
                )
            elif credential.status == "expired":
                employee.credential_warning = _("License expired.")
            elif credential.status == "suspended":
                employee.credential_warning = _("Credential suspended.")
            elif credential.status == "pending_verification":
                employee.credential_warning = _("Credential pending verification.")
            else:
                employee.credential_warning = False

    @api.depends("duty_line_ids.state")
    def _compute_duty_line_count(self):
        for employee in self:
            employee.duty_line_count = len(
                employee.duty_line_ids.filtered(
                    lambda line: line.state != "cancelled"
                )
            )

    @api.depends("attendance_log_ids.status")
    def _compute_attendance_log_count(self):
        for employee in self:
            employee.attendance_log_count = len(
                employee.attendance_log_ids.filtered(
                    lambda log: log.status != "cancelled"
                )
            )

    def action_view_hospital_attendance_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Duty Logs"),
            "res_model": "hospital.hr.attendance.log",
            "view_mode": "list,pivot,form",
            "domain": [("employee_id", "=", self.id)],
            "context": {"default_employee_id": self.id},
        }

    def action_view_hospital_duty_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Duty Lines"),
            "res_model": "hospital.hr.duty.line",
            "view_mode": "list,calendar,form",
            "domain": [("employee_id", "=", self.id)],
            "context": {"default_employee_id": self.id},
        }

    def action_view_hospital_credentials(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Credentials"),
            "res_model": "hospital.hr.credential",
            "view_mode": "list,form",
            "domain": [("employee_id", "=", self.id)],
            "context": {"default_employee_id": self.id},
        }

    @api.onchange("hospital_role_id")
    def _onchange_hospital_role_id(self):
        role = self.hospital_role_id
        if not role:
            return
        self.can_be_assigned_to_duty = role.can_be_scheduled
        self.on_call_eligible = role.can_be_on_call
        self.can_perform_clinical_services = role.clinical_role
        staff_type = STAFF_TYPE_BY_ROLE_TYPE.get(role.role_type)
        if staff_type:
            self.hospital_staff_type = staff_type

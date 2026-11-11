from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

DEFAULT_EXPIRY_HORIZON_DAYS = 60

CREDENTIAL_STATUSES = [
    ("pending_verification", "Pending Verification"),
    ("valid", "Valid"),
    ("expiring_soon", "Expiring Soon"),
    ("expired", "Expired"),
    ("suspended", "Suspended"),
    ("no_expiry", "No Expiry"),
]


class HospitalHrCredential(models.Model):
    _name = "hospital.hr.credential"
    _description = "Hospital Staff Credential / License"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "expiry_date asc, employee_id, credential_name"

    name = fields.Char(
        compute="_compute_name",
        store=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        string="Employee",
        required=True,
        index=True,
        ondelete="restrict",
        tracking=True,
    )
    hospital_role_id = fields.Many2one(
        related="employee_id.hospital_role_id",
        store=True,
        string="Hospital Role",
    )
    credential_type = fields.Selection(
        [
            ("medical_license", "Medical License"),
            ("nursing_license", "Nursing License"),
            ("pharmacy_license", "Pharmacy License"),
            ("radiology_license", "Radiology License"),
            ("lab_certification", "Laboratory Certification"),
            ("specialty_certification", "Specialty Certification"),
            ("bls_acls", "BLS / ACLS"),
            ("professional_registration", "Professional Registration"),
            ("employment_clearance", "Employment Clearance"),
            ("other", "Other"),
        ],
        string="Credential Type",
        required=True,
        default="other",
        tracking=True,
    )
    credential_name = fields.Char(required=True, tracking=True)
    license_number = fields.Char(tracking=True)
    issuing_authority = fields.Char(
        help="e.g. Ethiopian Ministry of Health, Ethiopian Health Professionals "
        "Competency Authority, professional association, training institution.",
    )
    issue_date = fields.Date()
    expiry_date = fields.Date(
        tracking=True,
        help="Leave empty for credentials that never expire.",
    )
    manual_status = fields.Selection(
        [
            ("none", "None"),
            ("pending_verification", "Pending Verification"),
            ("suspended", "Suspended"),
        ],
        string="Manual Status",
        default="none",
        required=True,
        tracking=True,
        help="HR-controlled override. Suspended and Pending Verification take "
        "precedence over the date-based status.",
    )
    status = fields.Selection(
        CREDENTIAL_STATUSES,
        compute="_compute_status",
        store=True,
        help="Date-based statuses are refreshed daily by the "
        "'Hospital HR: Check Credential Expiry' scheduled action.",
    )
    days_to_expiry = fields.Integer(
        compute="_compute_days_to_expiry",
        help="Days until expiry (negative if already expired, 0 if no expiry date).",
    )
    last_notified_status = fields.Char(
        readonly=True,
        copy=False,
        help="Last status for which an expiry notification was posted; "
        "prevents the daily cron from re-posting the same warning.",
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "hospital_hr_credential_attachment_rel",
        "credential_id",
        "attachment_id",
        string="Documents",
        help="Scanned license, certificate PDF, renewal letter, ...",
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.company,
    )

    @api.model
    def _get_expiry_horizon_days(self):
        param = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(
                "hospital_hr.credential_expiry_horizon_days",
                DEFAULT_EXPIRY_HORIZON_DAYS,
            )
        )
        try:
            return int(param)
        except (TypeError, ValueError):
            return DEFAULT_EXPIRY_HORIZON_DAYS

    @api.depends("employee_id.name", "credential_name")
    def _compute_name(self):
        for credential in self:
            parts = [credential.employee_id.name, credential.credential_name]
            credential.name = " - ".join(part for part in parts if part)

    def _get_status_for_date(self, today, horizon_days):
        self.ensure_one()
        if self.manual_status == "suspended":
            return "suspended"
        if self.manual_status == "pending_verification":
            return "pending_verification"
        if not self.expiry_date:
            return "no_expiry"
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + timedelta(days=horizon_days):
            return "expiring_soon"
        return "valid"

    @api.depends("manual_status", "expiry_date")
    def _compute_status(self):
        today = fields.Date.context_today(self)
        horizon = self._get_expiry_horizon_days()
        for credential in self:
            credential.status = credential._get_status_for_date(today, horizon)

    @api.depends("expiry_date")
    def _compute_days_to_expiry(self):
        today = fields.Date.context_today(self)
        for credential in self:
            if credential.expiry_date:
                credential.days_to_expiry = (credential.expiry_date - today).days
            else:
                credential.days_to_expiry = 0

    @api.constrains("issue_date", "expiry_date")
    def _check_dates(self):
        for credential in self:
            if (
                credential.issue_date
                and credential.expiry_date
                and credential.expiry_date < credential.issue_date
            ):
                raise ValidationError(
                    _("The expiry date cannot be earlier than the issue date.")
                )

    @api.model
    def _cron_check_credential_expiry(self):
        """Daily refresh of date-based statuses + one-time expiry warnings.

        Stored computed statuses do not move with the calendar on their own,
        so this cron re-evaluates every active credential with an expiry date.
        A warning is posted in the chatter (and an activity scheduled for the
        first Hospital HR Manager) only when the status *changes* to
        expiring_soon/expired — last_notified_status prevents daily re-posts.
        """
        today = fields.Date.context_today(self)
        horizon = self._get_expiry_horizon_days()
        manager_group = self.env.ref(
            "hospital_hr.group_hospital_hr_manager", raise_if_not_found=False
        )
        manager_user = manager_group.users[:1] if manager_group else None
        credentials = self.search([("expiry_date", "!=", False)])
        for credential in credentials:
            new_status = credential._get_status_for_date(today, horizon)
            if credential.status != new_status:
                credential.status = new_status
            if (
                new_status in ("expiring_soon", "expired")
                and credential.last_notified_status != new_status
            ):
                if new_status == "expired":
                    body = _(
                        "Credential %(name)s has expired on %(date)s.",
                        name=credential.display_name,
                        date=credential.expiry_date,
                    )
                else:
                    body = _(
                        "Credential %(name)s expires in %(days)s days (%(date)s).",
                        name=credential.display_name,
                        days=credential.days_to_expiry,
                        date=credential.expiry_date,
                    )
                credential.message_post(body=body)
                if manager_user:
                    credential.activity_schedule(
                        "mail.mail_activity_data_todo",
                        user_id=manager_user.id,
                        summary=_("Credential renewal required"),
                        note=body,
                    )
                credential.last_notified_status = new_status
        return True

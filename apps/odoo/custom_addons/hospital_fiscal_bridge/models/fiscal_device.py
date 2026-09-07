import hmac
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

# A terminal is shown as "online" when it called the API within this window.
ONLINE_WINDOW_MINUTES = 15


class HospitalFiscalDevice(models.Model):
    _name = "hospital.fiscal.device"
    _description = "Fiscal POS Device / Terminal"
    _order = "name"

    name = fields.Char(required=True)
    device_code = fields.Char(
        required=True,
        copy=False,
        help="Unique terminal code sent by the device on every API call, e.g. SUNMI-001.",
    )
    serial_number = fields.Char(copy=False)
    terminal_type = fields.Selection(
        [
            ("sunmi", "SUNMI"),
            ("zoorya", "Zoorya"),
            ("other", "Other"),
        ],
        default="sunmi",
    )
    location_name = fields.Char()
    branch_name = fields.Char(
        help="Generic branch label only. Not linked to any clinical record."
    )
    allowed_ip = fields.Char(
        string="Allowed IP",
        help="Optional IP allowlist. Comma-separated list of exact IP addresses. "
        "Leave empty to accept calls from any IP (not recommended in production).",
    )
    # Foundation simplicity: plain shared secret. Production should replace this
    # with a hashed key or HMAC request signing (see README hardening checklist).
    api_key = fields.Char(
        copy=False,
        groups="hospital_management.group_hospital_manager,"
        "hospital_management.group_hospital_system_administrator",
        help="Shared secret the terminal must send on every API call.",
    )
    active = fields.Boolean(default=True)
    last_seen = fields.Datetime(readonly=True, copy=False)
    notes = fields.Text()
    # UI statistics (non-stored, computed from fiscal transactions)
    transaction_count = fields.Integer(
        compute="_compute_activity_stats",
        string="Total Transactions",
    )
    successful_payment_count = fields.Integer(
        compute="_compute_activity_stats",
        string="Successful Payments",
    )
    failed_exception_count = fields.Integer(
        compute="_compute_activity_stats",
        string="Failed / Exception",
    )
    last_payment_datetime = fields.Datetime(
        compute="_compute_activity_stats",
        string="Last Payment",
    )
    is_online = fields.Boolean(
        compute="_compute_is_online",
        string="Online",
        help="True when the terminal called the fiscal API within the last "
        "%d minutes." % ONLINE_WINDOW_MINUTES,
    )

    def _compute_activity_stats(self):
        transaction_model = self.env["hospital.fiscal.transaction"]
        for device in self:
            transactions = transaction_model.with_context(active_test=False).search(
                [("device_id", "=", device.id)]
            )
            paid = transactions.filtered(lambda t: t.state == "paid")
            device.transaction_count = len(transactions)
            device.successful_payment_count = len(paid)
            device.failed_exception_count = len(
                transactions.filtered(lambda t: t.state in ("failed", "exception"))
            )
            device.last_payment_datetime = max(
                paid.mapped("fiscal_paid_at"), default=False
            )

    @api.depends("last_seen")
    def _compute_is_online(self):
        threshold = fields.Datetime.now() - timedelta(minutes=ONLINE_WINDOW_MINUTES)
        for device in self:
            device.is_online = bool(device.last_seen and device.last_seen >= threshold)

    _sql_constraints = [
        (
            "device_code_unique",
            "unique(device_code)",
            "Device code must be unique.",
        ),
    ]

    def _check_api_key(self, provided_key):
        """Constant-time comparison of the provided API key."""
        self.ensure_one()
        if not self.api_key or not provided_key:
            return False
        return hmac.compare_digest(self.api_key, str(provided_key))

    def _check_ip(self, remote_ip):
        """Return True when the caller IP is allowed for this device."""
        self.ensure_one()
        if not self.allowed_ip:
            return True
        allowed = [ip.strip() for ip in self.allowed_ip.split(",") if ip.strip()]
        return (remote_ip or "") in allowed

    def _touch_last_seen(self):
        self.sudo().write({"last_seen": fields.Datetime.now()})

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            raise UserError(
                "Fiscal devices cannot be deleted. Archive them instead."
            )
        return super().unlink()

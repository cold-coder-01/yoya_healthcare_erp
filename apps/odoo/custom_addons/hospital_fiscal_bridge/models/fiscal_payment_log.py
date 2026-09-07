from odoo import fields, models
from odoo.exceptions import UserError


class HospitalFiscalPaymentLog(models.Model):
    _name = "hospital.fiscal.payment.log"
    _description = "Fiscal Bridge API / Audit Log"
    _order = "created_at desc, id desc"

    transaction_id = fields.Many2one(
        "hospital.fiscal.transaction",
        ondelete="set null",
        index=True,
    )
    device_id = fields.Many2one(
        "hospital.fiscal.device",
        ondelete="set null",
        index=True,
    )
    direction = fields.Selection(
        [
            ("inbound", "Inbound"),
            ("outbound", "Outbound"),
            ("internal", "Internal"),
        ],
        default="inbound",
        required=True,
    )
    event_type = fields.Selection(
        [
            ("terminal_lookup", "Terminal Lookup"),
            ("lookup_response", "Lookup Response"),
            ("payment_success", "Payment Success"),
            ("payment_failure", "Payment Failure"),
            ("status_check", "Status Check"),
            ("reversal", "Reversal"),
            ("manual_admin_action", "Manual / Admin Action"),
            ("error", "Error"),
        ],
        required=True,
        index=True,
    )
    request_payload = fields.Text()
    response_payload = fields.Text()
    status_code = fields.Char()
    terminal_code = fields.Char(index=True)
    external_reference = fields.Char()
    external_transaction_id = fields.Char(index=True)
    external_receipt_no = fields.Char(index=True)
    idempotency_key = fields.Char(index=True)
    ip_address = fields.Char()
    user_agent = fields.Char()
    error_message = fields.Text()
    created_at = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
        readonly=True,
    )

    def write(self, vals):
        # Audit trail must stay immutable for everyone except the system admin.
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ) and not self.env.su:
            raise UserError("Fiscal logs are immutable and cannot be modified.")
        return super().write(vals)

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            raise UserError(
                "Fiscal logs cannot be deleted. Contact the system administrator."
            )
        return super().unlink()

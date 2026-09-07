import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class HospitalFiscalTransaction(models.Model):
    _inherit = "hospital.fiscal.transaction"

    pharmacy_dispense_id = fields.Many2one(
        "hospital.pharmacy.dispense",
        string="Pharmacy Dispense",
        readonly=True,
        copy=False,
        index=True,
        ondelete="set null",
    )
    unified_receipt_id = fields.Many2one(
        "hospital.charge.receipt",
        string="Unified Receipt",
        readonly=True,
        copy=False,
        ondelete="restrict",
        help="Unified patient-advance receipt created for encounter-billing fiscal payments.",
    )

    def action_mark_paid_from_terminal(self, payload):
        result = super().action_mark_paid_from_terminal(payload)
        if isinstance(result, dict) and result.get("status") == "accepted":
            self._dispatch_source_payment_success()
        return result

    def _dispatch_source_payment_success(self):
        self.ensure_one()
        source = self._source_record() if hasattr(self, "_source_record") else self.env[self._name]
        if not source:
            return
        handler = getattr(source, "_on_fiscal_payment_success", None)
        if not callable(handler):
            return
        try:
            with self.env.cr.savepoint():
                handler(self)
        except Exception as exc:
            _logger.exception(
                "Pharmacy fiscal bridge: post-payment automation failed for %s (source %s,%s)",
                self.name,
                self.source_model,
                self.source_record_id,
            )
            self._create_audit_log(
                "update",
                f"Post-payment automation failed for {self.name} "
                f"(source {self.source_reference or self.source_record_id}): {exc}",
            )

    def action_view_pharmacy_dispense(self):
        self.ensure_one()
        if not self.pharmacy_dispense_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": "Pharmacy Dispense",
            "res_model": "hospital.pharmacy.dispense",
            "res_id": self.pharmacy_dispense_id.id,
            "view_mode": "form",
            "target": "current",
        }

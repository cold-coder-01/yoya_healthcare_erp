from odoo import api, models


class HospitalBillingEngineAdmissionLedger(models.AbstractModel):
    _inherit = "hospital.billing.engine"

    @api.model
    def _advance_application_is_pooled(self, invoice):
        """An admission's visit pools the patient's money on the deposit
        charge (and on whatever lines it was paid against), apart from the
        care lines being invoiced. Its applications live in the GL and the
        application record only -- never mirrored onto care lines that never
        received the cash (applied <= received per line)."""
        encounter = invoice.sudo().hospital_encounter_id
        if encounter and self.env["hospital.admission"].sudo().with_context(active_test=False).search_count(
            [("encounter_id", "=", encounter.id)], limit=1
        ):
            return True
        return super()._advance_application_is_pooled(invoice)

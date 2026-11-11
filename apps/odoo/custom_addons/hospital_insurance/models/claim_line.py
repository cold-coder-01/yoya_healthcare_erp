from odoo import api, fields, models


class HospitalInsuranceClaimLine(models.Model):
    _name = "hospital.insurance.claim.line"
    _description = "Insurance Claim Line"
    _order = "claim_id, id"

    claim_id = fields.Many2one(
        "hospital.insurance.claim",
        required=True,
        ondelete="cascade",
    )
    bill_line_id = fields.Many2one(
        "hospital.patient.bill.line",
        string="Source Bill Line",
        ondelete="set null",
    )
    description = fields.Char()
    source_type = fields.Selection(
        [
            ("consultation", "Consultation"),
            ("laboratory", "Laboratory"),
            ("radiology", "Radiology"),
            ("pharmacy", "Pharmacy"),
            ("procedure", "Procedure"),
            ("surgery", "Surgery"),
            ("admission", "Admission"),
            ("other", "Other"),
        ],
    )
    quantity = fields.Float(default=1.0, digits=(16, 3))
    unit_price = fields.Monetary()
    subtotal = fields.Monetary()
    coverage_percent = fields.Float(string="Coverage %", default=0.0)
    claimed_amount = fields.Monetary(
        compute="_compute_claimed_amount",
        store=True,
    )
    approved_amount = fields.Monetary()
    rejected_amount = fields.Monetary(
        compute="_compute_rejected_amount",
        store=True,
    )
    patient_share = fields.Monetary(
        compute="_compute_shares",
        store=True,
    )
    payer_share = fields.Monetary(
        compute="_compute_shares",
        store=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="claim_id.currency_id",
        store=True,
    )
    notes = fields.Text()

    @api.depends("subtotal", "coverage_percent")
    def _compute_claimed_amount(self):
        for line in self:
            line.claimed_amount = line.subtotal * line.coverage_percent / 100.0

    @api.depends("claimed_amount", "approved_amount")
    def _compute_rejected_amount(self):
        for line in self:
            line.rejected_amount = max(
                0.0, line.claimed_amount - line.approved_amount
            )

    @api.depends("subtotal", "claimed_amount", "approved_amount")
    def _compute_shares(self):
        for line in self:
            # Payer share reflects the approved figure once set, otherwise the
            # claimed amount. The patient covers whatever the payer does not.
            payer = line.approved_amount if line.approved_amount else line.claimed_amount
            payer = min(payer, line.subtotal)
            line.payer_share = payer
            line.patient_share = max(0.0, line.subtotal - payer)

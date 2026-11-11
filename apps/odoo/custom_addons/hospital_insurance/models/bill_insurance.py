from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalPatientBill(models.Model):
    _inherit = "hospital.patient.bill"

    payer_type = fields.Selection(
        [
            ("cash", "Cash / Self Pay"),
            ("insurance", "Insurance"),
            ("corporate", "Corporate Employer"),
            ("government_program", "Government Program"),
            ("ngo", "NGO"),
            ("donor", "Donor"),
            ("credit_agreement", "Credit Agreement"),
        ],
        default="cash",
        tracking=True,
    )
    insurance_provider_id = fields.Many2one(
        "hospital.insurance.provider",
        string="Payer",
        tracking=True,
    )
    coverage_id = fields.Many2one(
        "hospital.patient.coverage",
        string="Coverage",
    )
    guarantee_id = fields.Many2one(
        "hospital.guarantee.letter",
        string="Guarantee Letter",
    )
    insurance_claim_id = fields.Many2one(
        "hospital.insurance.claim",
        string="Insurance Claim",
        copy=False,
    )
    insurance_state = fields.Selection(
        [
            ("not_applicable", "Not Applicable"),
            ("pending_authorization", "Pending Authorization"),
            ("ready_to_claim", "Ready to Claim"),
            ("claim_submitted", "Claim Submitted"),
            ("claim_approved", "Claim Approved"),
            ("claim_rejected", "Claim Rejected"),
            ("claim_paid", "Claim Paid"),
            ("patient_responsibility", "Patient Responsibility"),
        ],
        default="not_applicable",
        tracking=True,
        copy=False,
    )
    claim_settlement_mode = fields.Selection(
        [
            ("direct_to_hospital", "Direct to Hospital"),
            ("patient_reimbursement", "Patient Reimbursement"),
        ],
        default="direct_to_hospital",
        help="Direct to Hospital: the payer owes the hospital and a claim is "
        "created for the payer share. Patient Reimbursement: the patient pays "
        "the hospital and claims reimbursement personally; no hospital "
        "receivable claim is created.",
    )
    coverage_percent = fields.Float(string="Coverage %", default=0.0)
    payer_responsibility_amount = fields.Monetary(
        compute="_compute_responsibility_amounts",
        store=True,
    )
    patient_responsibility_amount = fields.Monetary(
        compute="_compute_responsibility_amounts",
        store=True,
    )
    claim_count = fields.Integer(compute="_compute_claim_count")
    insurance_note = fields.Text()

    @api.depends("amount_total", "coverage_percent", "payer_type")
    def _compute_responsibility_amounts(self):
        for bill in self:
            if bill.payer_type and bill.payer_type != "cash":
                payer = bill.amount_total * bill.coverage_percent / 100.0
            else:
                payer = 0.0
            payer = min(payer, bill.amount_total)
            bill.payer_responsibility_amount = payer
            bill.patient_responsibility_amount = max(0.0, bill.amount_total - payer)

    def _compute_claim_count(self):
        Claim = self.env["hospital.insurance.claim"]
        for bill in self:
            bill.claim_count = (
                Claim.search_count([("bill_id", "=", bill.id)]) if bill.id else 0
            )

    @api.onchange("payer_type")
    def _onchange_payer_type(self):
        if self.payer_type == "cash":
            self.insurance_provider_id = False
            self.coverage_id = False
            self.guarantee_id = False
            self.coverage_percent = 0.0
            if self.insurance_state != "not_applicable":
                self.insurance_state = "not_applicable"
        elif self.insurance_state == "not_applicable":
            self.insurance_state = "ready_to_claim"

    @api.onchange("coverage_id")
    def _onchange_coverage_id(self):
        if self.coverage_id:
            self.insurance_provider_id = self.coverage_id.provider_id
            if not self.coverage_percent:
                self.coverage_percent = self.coverage_id.coverage_percent

    @api.onchange("insurance_provider_id")
    def _onchange_insurance_provider_id(self):
        if self.insurance_provider_id and not self.coverage_percent:
            self.coverage_percent = self.insurance_provider_id.default_coverage_percent

    def action_create_insurance_claim(self):
        self.ensure_one()
        if not self.payer_type or self.payer_type == "cash":
            raise UserError(
                "Set a non-cash payer type before creating an insurance claim."
            )
        if self.claim_settlement_mode == "patient_reimbursement":
            raise UserError(
                "Patient reimbursement mode does not create a hospital "
                "receivable claim. Print the bill/receipt as supporting "
                "documentation for the patient."
            )
        # A bill the patient has already settled in full leaves no payer
        # receivable, so a direct hospital-to-payer claim is not valid. Use
        # currency rounding to avoid unsafe float comparisons.
        currency = self.currency_id or self.env.company.currency_id
        if self.amount_total > 0:
            fully_due_settled = (
                currency.compare_amounts(self.amount_due, 0.0) <= 0
            )
            fully_paid = (
                currency.compare_amounts(self.amount_paid, self.amount_total)
                >= 0
            )
            if fully_due_settled and fully_paid:
                raise UserError(
                    "This bill is already fully paid by the patient. A hospital "
                    "insurance claim cannot be created because there is no payer "
                    "receivable. Use patient reimbursement documentation instead."
                )
        if not self.insurance_provider_id:
            raise UserError("A payer is required before creating a claim.")
        if (
            self.insurance_provider_id.requires_guarantee_letter
            and not self.guarantee_id
        ):
            raise UserError(
                "This payer requires an approved guarantee letter before "
                "claiming."
            )
        if self.guarantee_id and not self.guarantee_id._is_usable_for_claim():
            raise UserError(
                "The linked guarantee letter must be approved and not expired "
                "to be used for a claim."
            )
        existing = self.env["hospital.insurance.claim"].search(
            [
                ("bill_id", "=", self.id),
                ("provider_id", "=", self.insurance_provider_id.id),
                ("state", "!=", "cancelled"),
            ],
            limit=1,
        )
        if existing:
            raise UserError(
                f"An active claim ({existing.name}) already exists for this "
                "bill and payer."
            )
        if not self.line_ids:
            raise UserError("Cannot create a claim from a bill with no lines.")

        # Keys the claim line actually accepts. Bill line source types can be
        # extended by other modules (e.g. "surgery"); anything unknown is stored
        # as "other" with the original value preserved in the line notes so
        # claim creation never crashes with a raw ValueError.
        valid_source_types = dict(
            self.env["hospital.insurance.claim.line"]
            ._fields["source_type"]
            .selection
        )
        line_vals = []
        for line in self.line_ids:
            source_type = line.source_type
            note = False
            if source_type and source_type not in valid_source_types:
                note = f"Original bill line source type: {source_type}"
                source_type = "other"
            line_vals.append(
                (
                    0,
                    0,
                    {
                        "bill_line_id": line.id,
                        "description": line.description,
                        "source_type": source_type,
                        "quantity": line.quantity,
                        "unit_price": line.unit_price,
                        "subtotal": line.subtotal,
                        "coverage_percent": self.coverage_percent,
                        "notes": note,
                    },
                )
            )
        claim = self.env["hospital.insurance.claim"].create(
            {
                "patient_id": self.patient_id.id,
                "bill_id": self.id,
                "provider_id": self.insurance_provider_id.id,
                "coverage_id": self.coverage_id.id or False,
                "guarantee_id": self.guarantee_id.id or False,
                "payer_responsibility_amount": self.payer_responsibility_amount,
                "patient_responsibility_amount": self.patient_responsibility_amount,
                "currency_id": self.currency_id.id,
                "line_ids": line_vals,
            }
        )
        self.write(
            {
                "insurance_claim_id": claim.id,
                "insurance_state": "ready_to_claim",
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Insurance Claim",
            "res_model": "hospital.insurance.claim",
            "view_mode": "form",
            "res_id": claim.id,
            "target": "current",
        }

    def action_view_insurance_claim(self):
        self.ensure_one()
        action = {
            "type": "ir.actions.act_window",
            "name": "Insurance Claims",
            "res_model": "hospital.insurance.claim",
            "view_mode": "list,form",
            "domain": [("bill_id", "=", self.id)],
            "context": {"default_bill_id": self.id},
        }
        if self.insurance_claim_id:
            action["view_mode"] = "form,list"
            action["res_id"] = self.insurance_claim_id.id
        return action

    def action_mark_patient_responsibility(self):
        for bill in self:
            bill.insurance_state = "patient_responsibility"

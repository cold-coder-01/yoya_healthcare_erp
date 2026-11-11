from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalPatientBillPaymentSource(models.Model):
    """Tag each patient bill payment with its funding source.

    Existing rows default to ``patient`` so legacy bills/totals are unaffected.
    """

    _inherit = "hospital.patient.bill.payment"

    payment_source = fields.Selection(
        [
            ("patient", "Patient"),
            ("insurance_payer", "Insurance Payer"),
            ("corporate_payer", "Corporate Payer"),
            ("government_program", "Government Program"),
            ("ngo", "NGO"),
            ("donor", "Donor"),
            ("credit_agreement", "Credit Agreement"),
            ("other", "Other"),
        ],
        default="patient",
        required=True,
        help="Patient payments clear Patient Receivable only. Payer payments "
        "are posted from the insurance claim and do not reduce patient "
        "responsibility.",
    )


class HospitalPatientBillSplit(models.Model):
    _inherit = "hospital.patient.bill"

    patient_amount_paid = fields.Monetary(
        compute="_compute_split_balances",
        string="Patient Paid",
        currency_field="currency_id",
    )
    patient_amount_due = fields.Monetary(
        compute="_compute_split_balances",
        string="Patient Due",
        currency_field="currency_id",
    )
    payer_amount_paid = fields.Monetary(
        compute="_compute_split_balances",
        string="Payer Paid",
        currency_field="currency_id",
    )
    payer_amount_due = fields.Monetary(
        compute="_compute_split_balances",
        string="Payer Due",
        currency_field="currency_id",
    )
    total_responsibility_paid = fields.Monetary(
        compute="_compute_split_balances",
        string="Total Paid",
        currency_field="currency_id",
    )
    total_responsibility_due = fields.Monetary(
        compute="_compute_split_balances",
        string="Total Due",
        currency_field="currency_id",
    )
    claim_state = fields.Selection(
        related="insurance_claim_id.state",
        string="Claim State",
        readonly=True,
    )
    claim_accounting_state = fields.Selection(
        related="insurance_claim_id.accounting_state",
        string="Claim Accounting State",
        readonly=True,
    )
    overall_settlement_state = fields.Selection(
        [
            ("unpaid", "Unpaid"),
            ("partially_paid", "Partially Paid"),
            ("paid", "Paid"),
        ],
        compute="_compute_split_balances",
        string="Overall Settlement Status",
    )
    direct_payer_settled = fields.Boolean(
        compute="_compute_split_balances",
        string="Direct-Payer Settled",
    )
    # --- Rejection resolution adjustments (Task 33D) -------------------
    rejected_to_patient_amount = fields.Monetary(
        compute="_compute_split_balances",
        string="Rejected Transferred to Patient",
        currency_field="currency_id",
    )
    rejected_written_off_amount = fields.Monetary(
        compute="_compute_split_balances",
        string="Rejected Written Off",
        currency_field="currency_id",
    )
    rejected_unresolved_amount = fields.Monetary(
        compute="_compute_split_balances",
        string="Unresolved Rejected",
        currency_field="currency_id",
    )
    adjusted_patient_responsibility_amount = fields.Monetary(
        compute="_compute_split_balances",
        string="Adjusted Patient Responsibility",
        currency_field="currency_id",
    )
    adjusted_payer_collectible = fields.Monetary(
        compute="_compute_split_balances",
        string="Adjusted Payer Collectible",
        currency_field="currency_id",
    )
    total_collected = fields.Monetary(
        compute="_compute_split_balances",
        string="Total Collected (Cash)",
        currency_field="currency_id",
    )
    total_resolved = fields.Monetary(
        compute="_compute_split_balances",
        string="Total Resolved",
        currency_field="currency_id",
    )

    @api.depends(
        "payment_ids.amount",
        "payment_ids.active",
        "payment_ids.payment_source",
        "patient_responsibility_amount",
        "payer_responsibility_amount",
        "amount_total",
        "payer_type",
        "insurance_claim_id.amount_paid_by_payer",
        "insurance_claim_id.rejected_amount_transferred_to_patient",
        "insurance_claim_id.rejected_amount_written_off",
        "insurance_claim_id.rejected_amount_unresolved",
    )
    def _compute_split_balances(self):
        for bill in self:
            currency = bill.currency_id or self.env.company.currency_id
            claim = bill.insurance_claim_id
            patient_paid = sum(
                bill.payment_ids.filtered(
                    lambda p: p.active and p.payment_source == "patient"
                ).mapped("amount")
            )
            payer_paid = claim.amount_paid_by_payer if claim else 0.0
            transferred = (
                claim.rejected_amount_transferred_to_patient if claim else 0.0
            )
            written_off = claim.rejected_amount_written_off if claim else 0.0
            unresolved = claim.rejected_amount_unresolved if claim else 0.0

            # Rejected amount transferred to the patient increases the patient's
            # responsibility; transferred + written-off reduce what the payer is
            # collectible for. Unresolved rejected stays on the payer side so the
            # bill is not treated as settled until it is resolved.
            adj_patient_resp = bill.patient_responsibility_amount + transferred
            adj_payer_coll = max(
                0.0,
                bill.payer_responsibility_amount - transferred - written_off,
            )

            bill.patient_amount_paid = patient_paid
            bill.patient_amount_due = max(0.0, adj_patient_resp - patient_paid)
            bill.payer_amount_paid = payer_paid
            bill.payer_amount_due = max(0.0, adj_payer_coll - payer_paid)

            bill.rejected_to_patient_amount = transferred
            bill.rejected_written_off_amount = written_off
            bill.rejected_unresolved_amount = unresolved
            bill.adjusted_patient_responsibility_amount = adj_patient_resp
            bill.adjusted_payer_collectible = adj_payer_coll

            total_collected = patient_paid + payer_paid
            total_due = bill.patient_amount_due + bill.payer_amount_due
            total_resolved = total_collected + written_off
            bill.total_collected = total_collected
            bill.total_responsibility_paid = total_collected
            bill.total_responsibility_due = total_due
            bill.total_resolved = total_resolved

            if currency.compare_amounts(total_due, 0.0) <= 0 and (
                currency.compare_amounts(total_resolved, 0.0) > 0
            ):
                bill.overall_settlement_state = "paid"
            elif currency.compare_amounts(total_resolved, 0.0) > 0:
                bill.overall_settlement_state = "partially_paid"
            else:
                bill.overall_settlement_state = "unpaid"
            bill.direct_payer_settled = bool(
                bill._is_direct_payer_bill()
                and currency.compare_amounts(total_due, 0.0) <= 0
            )

    def _is_direct_payer_bill(self):
        """True for insurance/corporate/etc. direct-to-hospital payer bills.

        Cash / self-pay bills return False and keep their existing behaviour.
        A non-cash payer bill qualifies regardless of the coverage split so its
        settlement reflects both patient and payer collection.
        """
        self.ensure_one()
        return bool(self.payer_type and self.payer_type != "cash")

    # ------------------------------------------------------------------
    # Overall settlement: fold payer payments into the header totals for
    # direct-payer bills without ever creating a bill payment record.
    # ------------------------------------------------------------------
    @api.depends(
        "payment_ids.amount",
        "payment_ids.active",
        "payment_ids.payment_source",
        "payer_type",
        "insurance_claim_id.amount_paid_by_payer",
    )
    def _compute_amount_paid(self):
        for bill in self:
            if bill._is_direct_payer_bill():
                patient_paid = sum(
                    bill.payment_ids.filtered(
                        lambda p: p.active and p.payment_source == "patient"
                    ).mapped("amount")
                )
                payer_paid = (
                    bill.insurance_claim_id.amount_paid_by_payer
                    if bill.insurance_claim_id
                    else 0.0
                )
                bill.amount_paid = patient_paid + payer_paid
            else:
                bill.amount_paid = sum(bill.payment_ids.mapped("amount"))

    def _update_payment_state(self):
        """Settle direct-payer bills against total (patient + payer) collection.

        Cash / self-pay bills keep the core payment-record-only behaviour.
        """
        direct = self.filtered(lambda b: b._is_direct_payer_bill())
        others = self - direct
        if others:
            super(HospitalPatientBillSplit, others)._update_payment_state()
        for bill in direct:
            if bill.state in ("draft", "cancelled"):
                continue
            currency = bill.currency_id or self.env.company.currency_id
            # Settle against the overall due, which already reflects rejection
            # resolution (transfer to patient / write-off). Write-off counts as
            # resolved but not as cash collected.
            total_due = bill.total_responsibility_due
            total_resolved = bill.total_resolved
            if currency.compare_amounts(total_resolved, 0.0) <= 0:
                new_state = "confirmed"
            elif currency.compare_amounts(total_due, 0.0) <= 0:
                new_state = "paid"
            else:
                new_state = "partially_paid"
            if bill.state != new_state:
                bill.write({"state": new_state})


class HospitalPatientBillPaymentWizardControl(models.TransientModel):
    _inherit = "hospital.patient.bill.payment.wizard"

    payment_party = fields.Selection(
        [
            ("patient", "Patient"),
            ("payer", "Payer"),
        ],
        string="Payment Party",
        default="patient",
        required=True,
        help="Who is paying. This bill payment wizard only collects the patient "
        "responsibility. Payer payments are posted from the insurance claim.",
    )
    is_direct_payer_bill = fields.Boolean(
        compute="_compute_split_display",
    )
    bill_total = fields.Monetary(
        compute="_compute_split_display",
        string="Bill Total",
        currency_field="currency_id",
    )
    patient_responsibility_amount = fields.Monetary(
        compute="_compute_split_display",
        string="Patient Responsibility",
        currency_field="currency_id",
    )
    patient_amount_paid = fields.Monetary(
        compute="_compute_split_display",
        string="Patient Paid",
        currency_field="currency_id",
    )
    patient_amount_due = fields.Monetary(
        compute="_compute_split_display",
        string="Patient Due",
        currency_field="currency_id",
    )
    payer_responsibility_amount = fields.Monetary(
        compute="_compute_split_display",
        string="Payer Responsibility",
        currency_field="currency_id",
    )
    payer_amount_paid = fields.Monetary(
        compute="_compute_split_display",
        string="Payer Paid",
        currency_field="currency_id",
    )
    payer_amount_due = fields.Monetary(
        compute="_compute_split_display",
        string="Payer Due",
        currency_field="currency_id",
    )

    @api.depends("bill_id")
    def _compute_split_display(self):
        for wizard in self:
            bill = wizard.bill_id
            wizard.is_direct_payer_bill = bool(
                bill and bill._is_direct_payer_bill()
            )
            wizard.bill_total = bill.amount_total if bill else 0.0
            wizard.patient_responsibility_amount = (
                bill.patient_responsibility_amount if bill else 0.0
            )
            wizard.patient_amount_paid = bill.patient_amount_paid if bill else 0.0
            wizard.patient_amount_due = bill.patient_amount_due if bill else 0.0
            wizard.payer_responsibility_amount = (
                bill.payer_responsibility_amount if bill else 0.0
            )
            wizard.payer_amount_paid = bill.payer_amount_paid if bill else 0.0
            wizard.payer_amount_due = bill.payer_amount_due if bill else 0.0

    @api.model
    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        bill_id = result.get("bill_id") or self.env.context.get(
            "default_bill_id"
        )
        if bill_id and "amount" in fields_list:
            bill = self.env["hospital.patient.bill"].browse(bill_id)
            # For insurance / corporate direct-payer bills, the patient can only
            # pay their own responsibility, so default the amount to the patient
            # remaining due (not the full bill due).
            if bill._is_direct_payer_bill():
                result["amount"] = bill.patient_amount_due
        return result

    @api.onchange("payment_party", "bill_id")
    def _onchange_payment_party_amount(self):
        """Keep the amount aligned with the selected party's remaining due.

        The payer amount is shown for clarity only; confirming a payer payment
        from this wizard is still blocked in action_confirm_payment.
        """
        bill = self.bill_id
        if not bill or not bill._is_direct_payer_bill():
            return
        if self.payment_party == "payer":
            self.amount = bill.payer_amount_due
        else:
            self.amount = bill.patient_amount_due

    def action_confirm_payment(self):
        """Restrict the bill payment wizard to patient responsibility only.

        Payer payments must be posted from the insurance claim so they clear the
        payer receivable, not Patient Receivable.
        """
        self.ensure_one()
        if self.payment_party == "payer":
            raise UserError(
                "Payer payments must be posted from the insurance claim using "
                "Post Payer Payment. This bill payment wizard is only for "
                "patient responsibility collection."
            )
        bill = self.bill_id
        if bill and bill._is_direct_payer_bill():
            currency = bill.currency_id or self.env.company.currency_id
            patient_due = bill.patient_amount_due
            if currency.compare_amounts(patient_due, 0.0) <= 0:
                raise UserError(
                    "Patient responsibility is already fully paid. Use the "
                    "insurance claim payment workflow for payer responsibility."
                )
            if currency.compare_amounts(self.amount, patient_due) > 0:
                raise UserError(
                    "This is an insurance/corporate bill. The patient can only "
                    "pay the remaining patient responsibility amount of %.2f. "
                    "The payer share must be collected through the insurance "
                    "claim payment workflow." % patient_due
                )
        return super().action_confirm_payment()

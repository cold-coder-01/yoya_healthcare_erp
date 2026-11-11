import json

from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalInsurancePayerPaymentWizard(models.TransientModel):
    _name = "hospital.insurance.payer.payment.wizard"
    _description = "Insurance Payer Payment Wizard"

    claim_id = fields.Many2one(
        "hospital.insurance.claim",
        required=True,
    )
    provider_id = fields.Many2one(
        "hospital.insurance.provider",
        related="claim_id.provider_id",
        readonly=True,
    )
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        related="claim_id.bill_id",
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="claim_id.currency_id",
        readonly=True,
    )
    approved_amount = fields.Monetary(
        related="claim_id.approved_amount",
        readonly=True,
    )
    amount_paid_by_payer = fields.Monetary(
        related="claim_id.amount_paid_by_payer",
        readonly=True,
    )
    amount_due_from_payer = fields.Monetary(
        related="claim_id.amount_due_from_payer",
        readonly=True,
    )
    payment_amount = fields.Monetary(required=True)
    payment_date = fields.Date(default=fields.Date.context_today, required=True)
    payment_journal_id = fields.Many2one(
        "account.journal",
        string="Payment Journal",
        required=True,
    )
    bank_account_id = fields.Many2one(
        "account.account",
        string="Bank / Cash Account",
        help="Debit account. Falls back to the journal's default account when "
        "left empty.",
    )
    payment_reference = fields.Char()
    notes = fields.Text()

    @api.model
    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        claim_id = result.get("claim_id") or self.env.context.get(
            "default_claim_id"
        )
        if claim_id and "payment_amount" in fields_list:
            claim = self.env["hospital.insurance.claim"].browse(claim_id)
            result["payment_amount"] = claim.amount_due_from_payer
        return result

    def action_post_payer_payment(self):
        self.ensure_one()
        claim = self.claim_id.sudo()

        # --- Validation -------------------------------------------------
        if claim.state not in (
            "approved",
            "partially_approved",
            "paid",
        ) and claim.accounting_state != "receivable_reclassified":
            raise UserError(
                "Only approved / partially approved / reclassified claims can "
                "register a payer payment."
            )
        reclass_move = claim.accounting_move_id
        if not reclass_move:
            reclass_log = self.env["hospital.insurance.accounting.log"].sudo().search(
                [
                    ("claim_id", "=", claim.id),
                    ("action_type", "=", "reclassification"),
                    ("state", "=", "success"),
                    ("move_id.state", "=", "posted"),
                ],
                order="id desc",
                limit=1,
            )
            reclass_move = reclass_log.move_id
        if reclass_move and reclass_move.state == "posted" and claim.accounting_state not in ("receivable_reclassified", "partially_paid", "paid"):
            claim.sudo().write({"accounting_move_id": reclass_move.id, "accounting_state": "receivable_reclassified", "accounting_error": False})
            claim.invalidate_recordset(["accounting_state"])
        if claim.accounting_state not in (
            "receivable_reclassified",
            "partially_paid",
            "paid",
        ):
            raise UserError(
                "The payer receivable must be reclassified before a payer "
                "payment can be posted."
            )
        if not claim.accounting_move_id:
            raise UserError(
                "This claim has no reclassification entry. Reclassify the payer "
                "receivable first."
            )

        company = claim._get_accounting_company()
        currency = claim.currency_id or company.currency_id

        if currency.compare_amounts(self.payment_amount, 0.0) <= 0:
            raise UserError("Payment amount must be greater than zero.")
        reference = (self.payment_reference or "").strip()
        if reference:
            existing_log = self.env["hospital.insurance.accounting.log"].sudo().search(
                [
                    ("company_id", "=", company.id),
                    ("action_type", "=", "payment"),
                    ("external_reference", "=", reference),
                    ("state", "=", "success"),
                ],
                limit=1,
            )
            if existing_log:
                if existing_log.claim_id != claim:
                    raise UserError(
                        "Payment reference %s was already used for another insurer claim." % reference
                    )
                return {"type": "ir.actions.act_window_close"}
        if not self.payment_journal_id:
            raise UserError("A payment journal is required.")

        provider_type = claim.provider_id.payer_type or "other"
        config = claim._get_insurance_accounting_config(provider_type, company)
        if not config:
            raise UserError(
                "No active insurance accounting configuration found for provider "
                "type '%s' in company '%s'." % (provider_type, company.name)
            )
        credit_account = config.get_payer_receivable_account(provider_type)
        if not credit_account:
            raise UserError(
                "No payer receivable account is configured for provider type "
                "'%s'." % provider_type
            )

        debit_account = self.bank_account_id or (
            self.payment_journal_id.default_account_id
        )
        if not debit_account:
            raise UserError(
                "No bank / cash account could be resolved. Set a default account "
                "on the payment journal or select a bank account."
            )

        amount = currency.round(self.payment_amount)
        provider_name = claim.provider_id.display_name or claim.provider_id.name

        move_vals = {
            "move_type": "entry",
            "date": self.payment_date or fields.Date.context_today(self),
            "journal_id": self.payment_journal_id.id,
            "ref": "Insurance Payer Payment - %s" % claim.name,
            "company_id": company.id,
            "line_ids": [
                (0, 0, {
                    "account_id": debit_account.id,
                    "name": "Payer Payment Received - %s - %s"
                    % (claim.name, provider_name),
                    "debit": amount,
                    "credit": 0.0,
                    "partner_id": claim.provider_id.partner_id.id if hasattr(claim.provider_id, "partner_id") and claim.provider_id.partner_id else False,
                }),
                (0, 0, {
                    "account_id": credit_account.id,
                    "name": "Clear Payer Receivable - %s - %s"
                    % (claim.name, provider_name),
                    "debit": 0.0,
                    "credit": amount,
                    "partner_id": claim.provider_id.partner_id.id if hasattr(claim.provider_id, "partner_id") and claim.provider_id.partner_id else False,
                }),
            ],
        }

        raw_payload = json.dumps({
            "claim": claim.name,
            "provider": provider_name,
            "provider_type": provider_type,
            "currency": currency.name,
            "amount": amount,
            "journal": self.payment_journal_id.display_name,
            "debit_account": debit_account.display_name,
            "credit_account": credit_account.display_name,
            "external_reference": reference,
        }, indent=2)

        # --- Create + post move ----------------------------------------
        try:
            move = self.env["account.move"].create(move_vals)
            if hasattr(move, "action_post"):
                move.action_post()
            receivable_debits = claim.accounting_move_id.line_ids.filtered(
                lambda line: line.account_id == credit_account
                and not line.reconciled
                and line.amount_residual > 0
            )
            payment_credit = move.line_ids.filtered(
                lambda line: line.account_id == credit_account
                and not line.reconciled
                and line.amount_residual < 0
            )
            if receivable_debits and payment_credit:
                (receivable_debits + payment_credit).reconcile()
        except Exception as exc:  # noqa: BLE001
            claim.write(
                {"accounting_state": "error", "accounting_error": str(exc)}
            )
            claim._create_accounting_log(
                action_type="error",
                state="error",
                message="Failed to post payer payment: %s" % exc,
                raw_payload=raw_payload,
                external_reference=reference,
            )
            raise UserError("Failed to post payer payment: %s" % exc)

        # --- Update claim payer balances -------------------------------
        new_paid = currency.round(claim.amount_paid_by_payer + amount)
        claim_vals = {
            "amount_paid_by_payer": new_paid,
            "accounting_error": False,
        }
        # amount_due_from_payer is computed/stored from approved_amount and
        # amount_paid_by_payer; write paid first, then read the fresh due.
        claim.write(claim_vals)
        remaining = claim.amount_due_from_payer
        if currency.compare_amounts(remaining, 0.0) <= 0:
            claim.write({
                "state": "paid",
                "accounting_state": "paid",
                "paid_date": fields.Datetime.now(),
            })
        else:
            claim.write({"accounting_state": "partially_paid"})

        # Refresh the originating bill's overall settlement state so the header
        # reflects the payer collection (the bill's amount_paid recomputes from
        # the claim automatically; state is not a computed field).
        if claim.bill_id and hasattr(claim.bill_id, "_update_payment_state"):
            claim.bill_id._update_payment_state()

        raw_payload = json.dumps({
            **json.loads(raw_payload),
            "move": move.name,
            "amount_paid_by_payer_after": new_paid,
            "amount_due_after": claim.amount_due_from_payer,
            "insurer_credit_after": claim.insurer_credit_amount,
        }, indent=2)

        claim._create_accounting_log(
            action_type="payment",
            state="success",
            message="Payer payment posted. Journal entry: %s" % move.name,
            move_id=move.id,
            raw_payload=raw_payload,
            external_reference=reference,
        )
        return {"type": "ir.actions.act_window_close"}

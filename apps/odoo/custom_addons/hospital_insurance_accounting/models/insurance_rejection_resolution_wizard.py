import json

from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalInsuranceRejectionResolutionWizard(models.TransientModel):
    _name = "hospital.insurance.rejection.resolution.wizard"
    _description = "Insurance Claim Rejection Resolution Wizard"

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
    rejected_amount_total = fields.Monetary(
        related="claim_id.rejected_amount_total",
        readonly=True,
    )
    already_transferred_amount = fields.Monetary(
        related="claim_id.rejected_amount_transferred_to_patient",
        readonly=True,
        string="Already Transferred",
    )
    already_written_off_amount = fields.Monetary(
        related="claim_id.rejected_amount_written_off",
        readonly=True,
        string="Already Written Off",
    )
    unresolved_rejected_amount = fields.Monetary(
        related="claim_id.rejected_amount_unresolved",
        readonly=True,
    )
    resolution_type = fields.Selection(
        [
            ("transfer_to_patient", "Transfer to Patient"),
            ("writeoff", "Write Off"),
        ],
        required=True,
        default="transfer_to_patient",
    )
    resolution_amount = fields.Monetary(required=True)
    journal_id = fields.Many2one(
        "account.journal",
        string="Journal",
        required=True,
    )
    writeoff_expense_account_id = fields.Many2one(
        "account.account",
        string="Write-off Expense Account",
    )
    notes = fields.Text(required=True)

    @api.model
    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        claim_id = result.get("claim_id") or self.env.context.get(
            "default_claim_id"
        )
        if claim_id:
            claim = self.env["hospital.insurance.claim"].browse(claim_id)
            if "resolution_amount" in fields_list:
                result["resolution_amount"] = claim.rejected_amount_unresolved
            if "journal_id" in fields_list:
                company = claim._get_accounting_company()
                config = claim._get_insurance_accounting_config(
                    claim.provider_id.payer_type or "other", company
                )
                if config:
                    result["journal_id"] = config.journal_id.id
                    if "writeoff_expense_account_id" in fields_list:
                        result["writeoff_expense_account_id"] = (
                            config.writeoff_expense_account_id.id
                        )
        return result

    def action_resolve_rejection(self):
        self.ensure_one()
        claim = self.claim_id

        # --- Validation -------------------------------------------------
        company = claim._get_accounting_company()
        currency = claim.currency_id or company.currency_id

        if currency.compare_amounts(claim.rejected_amount_total, 0.0) <= 0:
            raise UserError("This claim has no rejected amount to resolve.")
        if claim.accounting_state not in (
            "receivable_reclassified",
            "partially_paid",
            "paid",
            "rejected",
            "error",
        ):
            raise UserError(
                "The payer receivable must be reclassified before a rejected "
                "amount can be resolved."
            )
        if not claim.accounting_move_id:
            raise UserError(
                "This claim has no reclassification entry. Reclassify the payer "
                "receivable first."
            )
        if currency.compare_amounts(self.resolution_amount, 0.0) <= 0:
            raise UserError("The resolution amount must be greater than zero.")
        unresolved = claim.rejected_amount_unresolved
        if currency.compare_amounts(self.resolution_amount, unresolved) > 0:
            raise UserError(
                "The resolution amount (%.2f) cannot exceed the unresolved "
                "rejected amount (%.2f)." % (self.resolution_amount, unresolved)
            )
        if not self.journal_id:
            raise UserError("A journal is required.")
        if not self.notes:
            raise UserError("Notes are required to resolve a rejected amount.")

        provider_type = claim.provider_id.payer_type or "other"
        config = claim._get_insurance_accounting_config(provider_type, company)
        if not config:
            raise UserError(
                "No active insurance accounting configuration found for provider "
                "type '%s' in company '%s'." % (provider_type, company.name)
            )
        payer_account = config.get_payer_receivable_account(provider_type)
        if not payer_account:
            raise UserError(
                "No payer receivable account is configured for provider type "
                "'%s'." % provider_type
            )

        amount = currency.round(self.resolution_amount)
        provider_name = claim.provider_id.display_name or claim.provider_id.name

        if self.resolution_type == "transfer_to_patient":
            patient_account = config.patient_receivable_account_id
            if not patient_account:
                raise UserError(
                    "No patient receivable account is configured for provider "
                    "type '%s'." % provider_type
                )
            ref = "Insurance Rejection Transfer to Patient - %s" % claim.name
            move_lines = [
                (0, 0, {
                    "account_id": patient_account.id,
                    "name": "Rejected Claim Transferred to Patient - %s"
                    % claim.name,
                    "debit": amount,
                    "credit": 0.0,
                }),
                (0, 0, {
                    "account_id": payer_account.id,
                    "name": "Reduce Payer Receivable for Rejection - %s"
                    % claim.name,
                    "debit": 0.0,
                    "credit": amount,
                }),
            ]
            debit_account = patient_account
            credit_account = payer_account
            action_type = "rejection_transfer"
        else:  # writeoff
            expense_account = (
                self.writeoff_expense_account_id
                or config.writeoff_expense_account_id
            )
            if not expense_account:
                raise UserError(
                    "A write-off expense account is required (configure it on "
                    "the insurance accounting configuration or select one)."
                )
            ref = "Insurance Claim Rejection Write-off - %s" % claim.name
            move_lines = [
                (0, 0, {
                    "account_id": expense_account.id,
                    "name": "Claim Rejection Write-off - %s" % claim.name,
                    "debit": amount,
                    "credit": 0.0,
                }),
                (0, 0, {
                    "account_id": payer_account.id,
                    "name": "Reduce Payer Receivable for Write-off - %s"
                    % claim.name,
                    "debit": 0.0,
                    "credit": amount,
                }),
            ]
            debit_account = expense_account
            credit_account = payer_account
            action_type = "writeoff"

        move_vals = {
            "move_type": "entry",
            "date": fields.Date.context_today(self),
            "journal_id": self.journal_id.id,
            "ref": ref,
            "company_id": company.id,
            "line_ids": move_lines,
        }

        raw_payload = json.dumps({
            "claim": claim.name,
            "bill": claim.bill_id.name if claim.bill_id else False,
            "provider": provider_name,
            "provider_type": provider_type,
            "resolution_type": self.resolution_type,
            "currency": currency.name,
            "amount": amount,
            "debit_account": debit_account.display_name,
            "credit_account": credit_account.display_name,
            "journal": self.journal_id.display_name,
            "notes": self.notes,
        }, indent=2)

        # --- Create + post move ----------------------------------------
        try:
            move = self.env["account.move"].create(move_vals)
            if hasattr(move, "action_post"):
                move.action_post()
        except Exception as exc:  # noqa: BLE001
            claim.write(
                {"accounting_state": "error", "accounting_error": str(exc)}
            )
            claim._create_accounting_log(
                action_type=action_type,
                state="error",
                message="Failed to resolve rejected amount: %s" % exc,
                raw_payload=raw_payload,
            )
            raise UserError("Failed to resolve rejected amount: %s" % exc)

        # --- Update claim rejection balances ---------------------------
        if self.resolution_type == "transfer_to_patient":
            claim.rejected_amount_transferred_to_patient = currency.round(
                claim.rejected_amount_transferred_to_patient + amount
            )
        else:
            claim.rejected_amount_written_off = currency.round(
                claim.rejected_amount_written_off + amount
            )
        claim.accounting_error = False

        # Refresh the bill settlement so patient/payer due reflect the change.
        if claim.bill_id and hasattr(claim.bill_id, "_update_payment_state"):
            claim.bill_id._update_payment_state()

        claim._create_accounting_log(
            action_type=action_type,
            state="success",
            message="Rejected amount resolved (%s). Journal entry: %s"
            % (self.resolution_type, move.name),
            move_id=move.id,
            raw_payload=raw_payload,
        )
        return {"type": "ir.actions.act_window_close"}

import json

from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalInsuranceClaimAccounting(models.Model):
    _inherit = "hospital.insurance.claim"

    accounting_state = fields.Selection(
        [
            ("not_posted", "Not Posted"),
            ("ready", "Ready"),
            ("receivable_reclassified", "Receivable Reclassified"),
            ("partially_paid", "Partially Paid"),
            ("paid", "Paid"),
            ("rejected", "Rejected"),
            ("written_off", "Written Off"),
            ("error", "Error"),
            ("reversed", "Reversed"),
        ],
        default="not_posted",
        copy=False,
        tracking=True,
    )
    accounting_move_id = fields.Many2one(
        "account.move",
        string="Reclassification Entry",
        readonly=True,
        copy=False,
    )
    accounting_posted_date = fields.Datetime(readonly=True, copy=False)
    accounting_error = fields.Text(readonly=True, copy=False)
    accounting_log_ids = fields.One2many(
        "hospital.insurance.accounting.log",
        "claim_id",
        string="Accounting Logs",
    )
    accounting_log_count = fields.Integer(
        compute="_compute_accounting_log_count",
    )
    payer_payment_move_ids = fields.Many2many(
        "account.move",
        string="Payer Payment Entries",
        compute="_compute_payer_payment_moves",
    )
    payer_payment_move_count = fields.Integer(
        compute="_compute_payer_payment_moves",
    )
    insurer_credit_amount = fields.Monetary(
        compute="_compute_insurer_credit_amount",
        string="Insurer Credit",
        help="Amount paid by the insurer beyond the approved receivable. This remains insurer credit, never patient credit.",
    )

    # --- Rejection resolution (Task 33D) -------------------------------
    rejection_resolution_state = fields.Selection(
        [
            ("none", "None"),
            ("pending", "Pending"),
            ("transferred_to_patient", "Transferred to Patient"),
            ("written_off", "Written Off"),
            ("partially_resolved", "Partially Resolved"),
        ],
        compute="_compute_rejection_resolution",
        store=True,
        default="none",
        tracking=True,
    )
    rejected_amount_total = fields.Monetary(
        compute="_compute_rejection_resolution",
        store=True,
    )
    rejected_amount_transferred_to_patient = fields.Monetary(
        default=0.0,
        copy=False,
        readonly=True,
    )
    rejected_amount_written_off = fields.Monetary(
        default=0.0,
        copy=False,
        readonly=True,
    )
    rejected_amount_unresolved = fields.Monetary(
        compute="_compute_rejection_resolution",
        store=True,
    )
    rejection_transfer_move_ids = fields.Many2many(
        "account.move",
        "hospital_claim_rejection_transfer_move_rel",
        string="Rejection Transfer Entries",
        compute="_compute_rejection_moves",
    )
    rejection_transfer_move_count = fields.Integer(
        compute="_compute_rejection_moves",
    )
    writeoff_move_ids = fields.Many2many(
        "account.move",
        "hospital_claim_writeoff_move_rel",
        string="Write-off Entries",
        compute="_compute_rejection_moves",
    )
    writeoff_move_count = fields.Integer(
        compute="_compute_rejection_moves",
    )

    @api.depends("accounting_log_ids")
    def _compute_accounting_log_count(self):
        for claim in self:
            claim.accounting_log_count = len(claim.accounting_log_ids)

    @api.depends(
        "accounting_log_ids.move_id",
        "accounting_log_ids.action_type",
        "accounting_log_ids.state",
    )
    def _compute_payer_payment_moves(self):
        for claim in self:
            moves = claim.accounting_log_ids.filtered(
                lambda log: log.action_type == "payment"
                and log.state == "success"
                and log.move_id
            ).mapped("move_id")
            claim.payer_payment_move_ids = moves
            claim.payer_payment_move_count = len(moves)

    @api.depends("approved_amount", "amount_paid_by_payer")
    def _compute_insurer_credit_amount(self):
        for claim in self:
            currency = claim.currency_id or self.env.company.currency_id
            claim.insurer_credit_amount = currency.round(max(0.0, claim.amount_paid_by_payer - claim.approved_amount))

    @api.depends(
        "state",
        "claim_amount",
        "approved_amount",
        "rejected_amount_transferred_to_patient",
        "rejected_amount_written_off",
    )
    def _compute_rejection_resolution(self):
        for claim in self:
            # Rejected amount is only meaningful once the payer has adjudicated
            # the claim (partial approval / rejection / paid).
            if claim.state in (
                "approved",
                "partially_approved",
                "rejected",
                "paid",
            ):
                rejected_total = max(
                    0.0, claim.claim_amount - claim.approved_amount
                )
            else:
                rejected_total = 0.0
            transferred = claim.rejected_amount_transferred_to_patient
            written_off = claim.rejected_amount_written_off
            unresolved = max(0.0, rejected_total - transferred - written_off)
            claim.rejected_amount_total = rejected_total
            claim.rejected_amount_unresolved = unresolved
            if rejected_total <= 0:
                state = "none"
            elif transferred > 0 and written_off > 0:
                state = "partially_resolved"
            elif unresolved > 0:
                state = "partially_resolved" if (
                    transferred > 0 or written_off > 0
                ) else "pending"
            elif transferred > 0:
                state = "transferred_to_patient"
            elif written_off > 0:
                state = "written_off"
            else:
                state = "pending"
            claim.rejection_resolution_state = state

    @api.depends(
        "accounting_log_ids.move_id",
        "accounting_log_ids.action_type",
        "accounting_log_ids.state",
    )
    def _compute_rejection_moves(self):
        for claim in self:
            transfers = claim.accounting_log_ids.filtered(
                lambda log: log.action_type == "rejection_transfer"
                and log.state == "success"
                and log.move_id
            ).mapped("move_id")
            writeoffs = claim.accounting_log_ids.filtered(
                lambda log: log.action_type == "writeoff"
                and log.state == "success"
                and log.move_id
            ).mapped("move_id")
            claim.rejection_transfer_move_ids = transfers
            claim.rejection_transfer_move_count = len(transfers)
            claim.writeoff_move_ids = writeoffs
            claim.writeoff_move_count = len(writeoffs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_accounting_company(self):
        """Resolve the company for accounting, defensively.

        res.currency has no company_id in Odoo 18, so the company is never
        derived from the currency. Related records are probed with getattr so a
        missing company_id field on an optional model can never crash.
        """
        self.ensure_one()
        # Direct company on the claim itself (if the model ever gains one).
        company = getattr(self, "company_id", False)
        if company:
            return company
        for source in (
            self.bill_id,
            self.provider_id,
            self.coverage_id,
            self.guarantee_id,
        ):
            if source and getattr(source, "company_id", False):
                return source.company_id
        if self.env.company:
            return self.env.company
        raise UserError(
            "Unable to resolve the company for this insurance claim. Please set "
            "a company on the bill or provider."
        )

    def _get_insurance_accounting_config(self, provider_type, company):
        return self.env["hospital.insurance.accounting.config"].search(
            [
                ("company_id", "=", company.id),
                ("provider_type", "=", provider_type or "other"),
                ("active", "=", True),
            ],
            limit=1,
        )

    def _create_accounting_log(self, action_type, state, message, **extra):
        self.ensure_one()
        vals = {
            "claim_id": self.id,
            "bill_id": self.bill_id.id if self.bill_id else False,
            "action_type": action_type,
            "state": state,
            "message": message,
            "company_id": self._get_accounting_company().id,
        }
        vals.update(extra)
        return (
            self.env["hospital.insurance.accounting.log"].sudo().create(vals)
        )

    def _get_reclassification_amount(self):
        """Amount of payer share to reclassify.

        Claim lines represent the payer share, so claim_amount is preferred.
        Falls back to payer_responsibility_amount when the claim has no lines.
        """
        self.ensure_one()
        amount = self.claim_amount
        if not amount:
            amount = self.payer_responsibility_amount
        return amount

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_mark_ready_for_accounting(self):
        for claim in self:
            if claim.accounting_state not in ("not_posted", "error"):
                raise UserError(
                    "Only claims that are not posted (or in error) can be "
                    "marked ready for accounting."
                )
            if claim.accounting_move_id:
                raise UserError(
                    "This claim already has a reclassification entry and cannot "
                    "be marked ready again."
                )
            claim.write(
                {"accounting_state": "ready", "accounting_error": False}
            )
            claim._create_accounting_log(
                action_type="reclassification",
                state="success",
                message="Claim marked ready for accounting.",
            )
        return True

    def action_view_accounting_entry(self):
        self.ensure_one()
        if not self.accounting_move_id:
            raise UserError(
                "This claim has no reclassification journal entry yet."
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Reclassification Entry",
            "res_model": "account.move",
            "res_id": self.accounting_move_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_accounting_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Logs",
            "res_model": "hospital.insurance.accounting.log",
            "view_mode": "list,form",
            "domain": [("claim_id", "=", self.id)],
            "context": {"default_claim_id": self.id},
        }

    def action_open_payer_payment_wizard(self):
        self.ensure_one()
        if self.accounting_state not in (
            "receivable_reclassified",
            "partially_paid",
        ):
            raise UserError(
                "The payer receivable must be reclassified before posting a "
                "payer payment."
            )
        if not self.accounting_move_id:
            raise UserError(
                "This claim has no reclassification entry yet. Reclassify the "
                "payer receivable first."
            )
        currency = self.currency_id or self.env.company.currency_id
        if currency.compare_amounts(self.amount_due_from_payer, 0.0) <= 0:
            raise UserError(
                "There is no outstanding payer amount due on this claim."
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Post Payer Payment",
            "res_model": "hospital.insurance.payer.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_claim_id": self.id},
        }

    def action_view_payer_payment_moves(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Payer Payment Entries",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.payer_payment_move_ids.ids)],
        }

    def action_open_rejection_resolution_wizard(self):
        self.ensure_one()
        if self.accounting_state not in (
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
        currency = self.currency_id or self.env.company.currency_id
        if currency.compare_amounts(self.rejected_amount_unresolved, 0.0) <= 0:
            raise UserError(
                "There is no unresolved rejected amount on this claim."
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Resolve Rejected Amount",
            "res_model": "hospital.insurance.rejection.resolution.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_claim_id": self.id},
        }

    def action_view_rejection_transfer_entries(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Rejection Transfer Entries",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.rejection_transfer_move_ids.ids)],
        }

    def action_view_writeoff_entries(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Write-off Entries",
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.writeoff_move_ids.ids)],
        }

    def action_reclassify_payer_receivable(self):
        self.ensure_one()

        # --- Duplicate protection --------------------------------------
        if (
            self.accounting_move_id
            or self.accounting_state == "receivable_reclassified"
        ):
            raise UserError(
                "This insurance claim has already been reclassified to payer "
                "receivable."
            )

        # --- Validation -------------------------------------------------
        if self.accounting_state not in ("ready", "error"):
            raise UserError(
                "The claim must be marked ready (or be in error state) before "
                "reclassifying the payer receivable."
            )
        if not self.bill_id:
            raise UserError("The claim has no linked patient bill.")
        if not self.provider_id:
            raise UserError("The claim has no payer / provider.")
        if self.state == "rejected":
            raise UserError(
                "A fully rejected claim cannot be reclassified to payer "
                "receivable."
            )
        if self.state not in (
            "submitted",
            "under_review",
            "approved",
            "partially_approved",
            "paid",
        ):
            raise UserError(
                "Only submitted, under-review, approved, partially approved, or "
                "paid claims can be reclassified."
            )

        company = self._get_accounting_company()
        currency = self.currency_id or company.currency_id

        amount = self._get_reclassification_amount()
        if currency.compare_amounts(amount, 0.0) <= 0:
            raise UserError(
                "The claim / payer responsibility amount must be greater than "
                "zero to reclassify."
            )

        # Safe check: the originating bill should already be posted to
        # accounting when the billing bridge fields are present.
        bill = self.bill_id
        bill_accounting_state = getattr(bill, "accounting_state", None)
        if bill_accounting_state is not None and bill_accounting_state not in (
            "posted",
            "partially_posted",
        ):
            raise UserError(
                "The patient bill must be posted to accounting before the payer "
                "receivable can be reclassified."
            )

        provider_type = self.provider_id.payer_type or "other"
        config = self._get_insurance_accounting_config(provider_type, company)
        if not config:
            raise UserError(
                "No active insurance accounting configuration found for provider "
                "type '%s' in company '%s'." % (provider_type, company.name)
            )

        debit_account = config.get_payer_receivable_account(provider_type)
        if not debit_account:
            raise UserError(
                "No payer receivable account is configured for provider type "
                "'%s'." % provider_type
            )
        credit_account = config.patient_receivable_account_id
        if not credit_account:
            raise UserError(
                "No patient receivable account is configured for provider type "
                "'%s'." % provider_type
            )
        journal = config.journal_id
        if not journal:
            raise UserError(
                "No journal is configured for provider type '%s'."
                % provider_type
            )

        # A claim may have been marked paid (via the legacy "Register Payer
        # Payment" status action) before any accounting reclassification. Allow
        # it, but leave an audit trail so the sequence is transparent.
        if self.state == "paid":
            warning = (
                "Claim was marked paid before payer receivable reclassification."
                " Reclassification is being posted for accounting cleanup."
            )
            self.message_post(body=warning)
            self._create_accounting_log(
                action_type="reclassification",
                state="success",
                message=warning,
            )

        amount = currency.round(amount)
        provider_name = self.provider_id.display_name or self.provider_id.name

        move_vals = {
            "move_type": "entry",
            "date": fields.Date.context_today(self),
            "journal_id": journal.id,
            "ref": "Insurance Receivable Reclassification - %s" % self.name,
            "company_id": company.id,
            "line_ids": [
                (0, 0, {
                    "account_id": debit_account.id,
                    "name": "Payer Receivable - %s - %s"
                    % (self.name, provider_name),
                    "debit": amount,
                    "credit": 0.0,
                }),
                (0, 0, {
                    "account_id": credit_account.id,
                    "name": "Patient Receivable Reclassification - %s"
                    % self.name,
                    "debit": 0.0,
                    "credit": amount,
                }),
            ],
        }

        raw_payload = json.dumps({
            "claim": self.name,
            "bill": bill.name,
            "provider": provider_name,
            "provider_type": provider_type,
            "currency": currency.name,
            "amount": amount,
            "debit_account": debit_account.display_name,
            "credit_account": credit_account.display_name,
            "journal": journal.display_name,
        }, indent=2)

        # --- Create + post move ----------------------------------------
        try:
            move = self.env["account.move"].create(move_vals)
            if hasattr(move, "action_post"):
                move.action_post()
        except Exception as exc:  # noqa: BLE001
            self.write(
                {"accounting_state": "error", "accounting_error": str(exc)}
            )
            self._create_accounting_log(
                action_type="error",
                state="error",
                message="Failed to reclassify payer receivable: %s" % exc,
                raw_payload=raw_payload,
            )
            raise UserError(
                "Failed to reclassify payer receivable: %s" % exc
            )

        # --- Success ----------------------------------------------------
        self.write({
            "accounting_move_id": move.id,
            "accounting_state": "receivable_reclassified",
            "accounting_posted_date": fields.Datetime.now(),
            "accounting_error": False,
        })
        self._create_accounting_log(
            action_type="reclassification",
            state="success",
            message="Payer receivable reclassified. Journal entry: %s"
            % move.name,
            move_id=move.id,
            raw_payload=raw_payload,
        )
        return True

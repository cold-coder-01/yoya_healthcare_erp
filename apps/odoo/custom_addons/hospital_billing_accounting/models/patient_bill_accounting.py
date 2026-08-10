import json

from odoo import api, fields, models
from odoo.exceptions import UserError


class HospitalPatientBillAccounting(models.Model):
    _inherit = "hospital.patient.bill"

    accounting_state = fields.Selection(
        [
            ("not_posted", "Not Posted"),
            ("ready", "Ready"),
            ("posted", "Posted"),
            ("partially_posted", "Partially Posted"),
            ("reversed", "Reversed"),
            ("error", "Error"),
        ],
        default="not_posted",
        copy=False,
        tracking=True,
    )
    accounting_posted = fields.Boolean(readonly=True, copy=False)
    accounting_posted_date = fields.Datetime(readonly=True, copy=False)
    accounting_move_id = fields.Many2one(
        "account.move",
        string="Journal Entry",
        readonly=True,
        copy=False,
    )
    accounting_error = fields.Text(readonly=True, copy=False)
    accounting_log_ids = fields.One2many(
        "hospital.accounting.posting.log",
        "bill_id",
        string="Accounting Logs",
    )
    accounting_log_count = fields.Integer(
        compute="_compute_accounting_log_count",
    )

    @api.depends("accounting_log_ids")
    def _compute_accounting_log_count(self):
        for bill in self:
            bill.accounting_log_count = len(bill.accounting_log_ids)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_accounting_company(self):
        self.ensure_one()
        company = getattr(self, "company_id", False)
        return company or self.env.company

    def _get_accounting_config(self, source_type):
        """Return the active accounting config for the given source type."""
        self.ensure_one()
        company = self._get_accounting_company()
        return self.env["hospital.billing.accounting.config"].search(
            [
                ("company_id", "=", company.id),
                ("source_type", "=", source_type or "other"),
                ("active", "=", True),
            ],
            limit=1,
        )

    def _create_accounting_log(self, event_type, status, message, **extra):
        self.ensure_one()
        vals = {
            "bill_id": self.id,
            "event_type": event_type,
            "status": status,
            "message": message,
            "currency_id": self.currency_id.id or self.env.company.currency_id.id,
        }
        vals.update(extra)
        return self.env["hospital.accounting.posting.log"].sudo().create(vals)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_mark_ready_for_accounting(self):
        for bill in self:
            bill._assert_not_encounter_managed_duplicate()
            if bill.state not in ("confirmed", "partially_paid", "paid"):
                raise UserError(
                    "Only confirmed or paid bills can be marked ready for accounting."
                )
            if bill.accounting_state == "posted" or bill.accounting_move_id:
                raise UserError(
                    "This bill already has an accounting journal entry and cannot "
                    "be marked ready again."
                )
            bill.write({"accounting_state": "ready"})
            bill._create_accounting_log(
                event_type="bill_ready",
                status="success",
                message="Bill marked ready for accounting.",
            )
        return True

    def action_view_accounting_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Logs",
            "res_model": "hospital.accounting.posting.log",
            "view_mode": "list,form",
            "domain": [("bill_id", "=", self.id)],
            "context": {"default_bill_id": self.id},
        }

    def action_view_accounting_move(self):
        self.ensure_one()
        if not self.accounting_move_id:
            raise UserError("This bill has no accounting journal entry yet.")
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Entry",
            "res_model": "account.move",
            "res_id": self.accounting_move_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_post_bill_to_accounting(self):
        self.ensure_one()
        self._assert_not_encounter_managed_duplicate()

        # --- Validation -------------------------------------------------
        if self.state not in ("confirmed", "partially_paid", "paid"):
            raise UserError(
                "Only confirmed or paid bills can be posted to accounting."
            )
        if self.accounting_state not in ("ready", "error"):
            raise UserError(
                "The bill must be marked ready (or be in error state) before posting."
            )
        if not self.line_ids:
            raise UserError("The bill has no lines to post.")
        # Duplicate posting protection: never create a second move, even if the
        # existing one is still in draft.
        if self.accounting_state == "posted" or self.accounting_move_id:
            raise UserError("This bill already has an accounting journal entry.")

        company = self._get_accounting_company()
        currency = self.currency_id or company.currency_id

        # Group bill lines per source type and validate configs. Bill lines whose
        # subtotal is zero or negative are skipped for revenue posting so the
        # journal entry never contains 0.00 (or negative) revenue lines.
        revenue_by_source = {}
        receivable_account = False
        bill_journal = False
        for line in self.line_ids:
            source_type = line.source_type or "other"
            config = self._get_accounting_config(source_type)
            if not config:
                raise UserError(
                    "No accounting configuration found for source type '%s'. "
                    "Please configure it first." % source_type
                )
            if not config.receivable_account_id or not config.revenue_account_id \
                    or not config.journal_id:
                raise UserError(
                    "Accounting configuration for '%s' is incomplete. "
                    "Receivable account, revenue account and journal are required."
                    % source_type
                )
            # Always remember a receivable account / journal from a valid config,
            # even if this particular line is zero-value.
            if not receivable_account:
                receivable_account = config.receivable_account_id
                bill_journal = config.journal_id
            # Skip non-positive lines.
            if currency.compare_amounts(line.subtotal, 0.0) <= 0:
                continue
            revenue_by_source.setdefault(
                source_type, {"account": config.revenue_account_id, "amount": 0.0}
            )
            revenue_by_source[source_type]["amount"] += line.subtotal

        # Drop any grouped source whose total rounds to zero or below.
        revenue_by_source = {
            st: grp
            for st, grp in revenue_by_source.items()
            if currency.compare_amounts(grp["amount"], 0.0) > 0
        }

        if not revenue_by_source:
            raise UserError(
                "Cannot post accounting entry because the bill has no "
                "positive-value lines."
            )

        # Debit receivable = sum of positive revenue lines (not blindly
        # amount_total, which could carry zero/negative lines).
        credit_total = currency.round(
            sum(grp["amount"] for grp in revenue_by_source.values())
        )
        debit_total = credit_total

        # Safe validation against the bill total: tolerate rounding noise, but
        # flag a material mismatch instead of silently posting a wrong amount.
        if currency.compare_amounts(credit_total, self.amount_total) != 0:
            raise UserError(
                "Mismatch between the bill total (%.2f) and the sum of "
                "positive-value lines (%.2f). Please review the bill before "
                "posting to accounting." % (self.amount_total, credit_total)
            )

        # --- Build move lines ------------------------------------------
        move_lines = [
            (0, 0, {
                "account_id": receivable_account.id,
                "name": "Patient Receivable - %s" % self.name,
                "debit": debit_total,
                "credit": 0.0,
            }),
        ]
        for source_type, grp in revenue_by_source.items():
            move_lines.append((0, 0, {
                "account_id": grp["account"].id,
                "name": "Hospital Revenue %s - %s" % (source_type, self.name),
                "debit": 0.0,
                "credit": currency.round(grp["amount"]),
            }))

        move_vals = {
            "move_type": "entry",
            "date": self.bill_date or fields.Date.context_today(self),
            "journal_id": bill_journal.id,
            "ref": "Hospital Bill %s" % self.name,
            "company_id": company.id,
            "line_ids": move_lines,
        }

        # --- Create move (hard failure) --------------------------------
        try:
            move = self.env["account.move"].create(move_vals)
        except Exception as exc:  # noqa: BLE001
            self.write({"accounting_state": "error", "accounting_error": str(exc)})
            self._create_accounting_log(
                event_type="error",
                status="failed",
                message="Failed to create journal entry: %s" % exc,
                debit_total=debit_total,
                credit_total=credit_total,
            )
            raise UserError("Failed to create journal entry: %s" % exc)

        # Readable audit payload (billing/accounting detail only — no clinical data).
        raw_payload = json.dumps({
            "bill": self.name,
            "currency": currency.name,
            "debit_account": receivable_account.display_name,
            "debit_total": debit_total,
            "credit_accounts": [
                {
                    "source_type": st,
                    "account": grp["account"].display_name,
                    "amount": currency.round(grp["amount"]),
                }
                for st, grp in revenue_by_source.items()
            ],
            "credit_total": credit_total,
        }, indent=2)

        # --- Post move (soft failure) ----------------------------------
        try:
            if hasattr(move, "action_post"):
                move.action_post()
            self.write({
                "accounting_move_id": move.id,
                "accounting_state": "posted",
                "accounting_posted": True,
                "accounting_posted_date": fields.Datetime.now(),
                "accounting_error": False,
            })
            self._create_accounting_log(
                event_type="bill_post",
                status="success",
                message="Bill posted to accounting. Journal entry: %s" % move.name,
                account_move_id=move.id,
                debit_total=debit_total,
                credit_total=credit_total,
                raw_payload=raw_payload,
            )
        except Exception as exc:  # noqa: BLE001
            self.write({"accounting_state": "error", "accounting_error": str(exc)})
            self._create_accounting_log(
                event_type="error",
                status="failed",
                message="Journal entry created but posting failed: %s" % exc,
                account_move_id=move.id,
                debit_total=debit_total,
                credit_total=credit_total,
            )
        return True


class HospitalPatientBillPaymentAccounting(models.Model):
    _inherit = "hospital.patient.bill.payment"

    accounting_state = fields.Selection(
        [
            ("not_posted", "Not Posted"),
            ("posted", "Posted"),
            ("error", "Error"),
        ],
        default="not_posted",
        copy=False,
    )
    accounting_posted_date = fields.Datetime(readonly=True, copy=False)
    accounting_move_id = fields.Many2one(
        "account.move",
        string="Journal Entry",
        readonly=True,
        copy=False,
    )
    accounting_error = fields.Text(readonly=True, copy=False)

    # Map hospital payment_method values to config account fields
    def _resolve_payment_account(self, config):
        self.ensure_one()
        method = (self.payment_method or self.bill_id.payment_method or "").lower()
        cash_methods = ("cash",)
        bank_methods = ("bank_transfer", "bank", "card", "cheque", "check")
        mobile_methods = (
            "mobile_money", "mobile", "telebirr", "cbe_birr", "cbe birr", "mpesa",
        )
        if method in cash_methods:
            return config.cash_account_id
        if method in bank_methods:
            return config.bank_account_id
        if method in mobile_methods:
            return config.mobile_money_account_id
        return False

    def _create_accounting_log(self, event_type, status, message, **extra):
        self.ensure_one()
        vals = {
            "payment_id": self.id,
            "bill_id": self.bill_id.id,
            "event_type": event_type,
            "status": status,
            "message": message,
            "currency_id": self.currency_id.id or self.env.company.currency_id.id,
        }
        vals.update(extra)
        return self.env["hospital.accounting.posting.log"].sudo().create(vals)

    def action_view_accounting_move(self):
        self.ensure_one()
        if not self.accounting_move_id:
            raise UserError("This payment has no accounting journal entry yet.")
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Entry",
            "res_model": "account.move",
            "res_id": self.accounting_move_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_post_payment_to_accounting(self):
        self.ensure_one()

        # --- Validation -------------------------------------------------
        if self.amount <= 0:
            raise UserError("Payment amount must be greater than zero.")
        if not self.bill_id:
            raise UserError("This payment is not linked to a bill.")
        if self.accounting_state == "posted" or self.accounting_move_id:
            raise UserError("This payment has already been posted to accounting.")

        bill = self.bill_id
        company = bill._get_accounting_company()

        # Use the receivable config of the first line's source type (consistent
        # with bill posting). Fall back to 'other'.
        source_type = bill.line_ids[:1].source_type or "other"
        config = bill._get_accounting_config(source_type)
        if not config:
            raise UserError(
                "No accounting configuration found for this bill (source type '%s')."
                % source_type
            )
        if not config.receivable_account_id:
            raise UserError(
                "No receivable account configured for source type '%s'." % source_type
            )

        payment_account = self._resolve_payment_account(config)
        if not payment_account:
            raise UserError(
                "No accounting payment account configured for this payment method."
            )

        journal = config.payment_journal_id or config.journal_id
        if not journal:
            raise UserError("No payment journal configured for this bill.")

        move_vals = {
            "move_type": "entry",
            "date": (self.payment_date.date() if self.payment_date
                     else fields.Date.context_today(self)),
            "journal_id": journal.id,
            "ref": "Hospital Payment %s / %s" % (self.name, bill.name),
            "company_id": company.id,
            "line_ids": [
                (0, 0, {
                    "account_id": payment_account.id,
                    "name": "Payment Received - %s" % self.name,
                    "debit": self.amount,
                    "credit": 0.0,
                }),
                (0, 0, {
                    "account_id": config.receivable_account_id.id,
                    "name": "Patient Receivable Settlement - %s" % self.name,
                    "debit": 0.0,
                    "credit": self.amount,
                }),
            ],
        }

        currency = self.currency_id or company.currency_id
        raw_payload = json.dumps({
            "payment": self.name,
            "bill": bill.name,
            "payment_method": self.payment_method or bill.payment_method or "",
            "currency": currency.name,
            "debit_account": payment_account.display_name,
            "credit_receivable": config.receivable_account_id.display_name,
            "amount": currency.round(self.amount),
        }, indent=2)

        try:
            move = self.env["account.move"].create(move_vals)
            if hasattr(move, "action_post"):
                move.action_post()
            self.write({
                "accounting_move_id": move.id,
                "accounting_state": "posted",
                "accounting_posted_date": fields.Datetime.now(),
                "accounting_error": False,
            })
            self._create_accounting_log(
                event_type="payment_post",
                status="success",
                message="Payment posted to accounting. Journal entry: %s" % move.name,
                account_move_id=move.id,
                debit_total=self.amount,
                credit_total=self.amount,
                raw_payload=raw_payload,
            )
        except Exception as exc:  # noqa: BLE001
            self.write({"accounting_state": "error", "accounting_error": str(exc)})
            self._create_accounting_log(
                event_type="error",
                status="failed",
                message="Failed to post payment to accounting: %s" % exc,
                debit_total=self.amount,
                credit_total=self.amount,
            )
            raise UserError("Failed to post payment to accounting: %s" % exc)
        return True

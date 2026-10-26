"""The inpatient accounting close: the operational settlement, in the ledger.

THE ECONOMICS
-------------
    every patient receipt     Dr cash / bank          Cr Patient Advance Liability
    delivered care            Dr Patient Receivable   Cr Healthcare Revenue
    advance application       Dr Advance Liability    Cr Patient Receivable
    refund of unused advance  Dr Advance Liability    Cr cash / bank

An estimate is never posted, an undelivered order is never invoiced, and a
refund never touches revenue or a delivered charge.

ONE AUTHORITY, NOT TWO
----------------------
How much of the patient's money pays for their care is the settlement
authority's own figure, _inpatient_financial_summary()["advance_applied"]
(= min(patient funds, patient share)). The close applies exactly that much in
total and no more; it never re-derives a balance. After posting it PROVES the
ledger agrees with the authority, inside the same savepoint:

    open patient receivable  ==  summary remaining_due
    open advance liability   ==  summary patient_credit + legacy refunds not yet posted

and refuses (accounting_close_mismatch, nothing posted) if either differs.

REUSED, NOT REBUILT
-------------------
Receipt posting, delivered-quantity invoicing (with its charge provenance and
token idempotency) and the advance application record are
hospital_billing_accounting's existing services. This module orders them,
passes them ONE validated company advance configuration, and records the
provenance row (hospital.admission.accounting.operation).

POOLED MONEY. An inpatient's money sits on the admission's deposit charge and
on whatever lines it was paid against, while the care sits on bed, pharmacy,
lab and radiology lines. The application is therefore authoritative in the GL
and its application record only -- see billing_engine.py: it is never mirrored
onto care lines that never received the cash.

ATOMIC AND FAIL-CLOSED. Every act runs under the admission's row lock, inside
the caller's savepoint. A missing mapping, an archived journal, a locked
period or a reconciliation failure raises a fixed code and leaves no entry,
no reconciliation and no operational refund behind. Discharge never runs any
of this.

SELF-PAY ONLY. A payer, sponsor or authorized sponsor share refuses with
payer_accounting_outside_scope: the patient's advance is never applied to a
payer's responsibility, and payer accounting is not this slice.
"""
import json
from datetime import date as date_type

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.hospital_admission.models.admission_accounting import ACCOUNTING_UNAVAILABLE
from odoo.addons.hospital_admission.models.admission_authority import (
    ADMISSION_ACTIVE_STATES,
    AdmissionDeskError,
    AdmissionWorkflowError,
)
from odoo.addons.hospital_admission.models.admission_cashier import (
    CASHIER_SETTLEMENT_MESSAGES,
    AdmissionAccountingError,
    CashierSettlementError,
)
from odoo.addons.hospital_admission.models.admission_stay import STAY_STATES
from odoo.addons.hospital_billing.models.billing_account import LIVE_CHARGE_STATES
from odoo.addons.hospital_billing.models.charge_line import AMOUNT_TOLERANCE

from .admission_accounting_operation import accounting_operation_capability

QTY_TOLERANCE = 1e-6
ACCOUNTING_AUDIT_KIND = "inpatient_accounting"

# Token namespaces, so one client key can never collide across acts.
POST_TOKEN = "inpatient-accounting-post:%s"
LEGACY_TOKEN = "inpatient-accounting-legacy:%s"
LEGACY_REFUND_TOKEN = "inpatient-accounting-legacy:%s:refund:%s"
REFUND_CLOSE_TOKEN = "inpatient-accounting-refund-close:%s"
REFUND_TOKEN = "inpatient-accounting-refund:%s"


def _accounting_detail(exc):
    """The accounting layer's own sentence, single-line and bounded."""
    text = " ".join(str(getattr(exc, "args", [exc])[0] if getattr(exc, "args", None) else exc).split())
    return text[:400]


class HospitalAdmissionLedger(models.Model):
    _inherit = "hospital.admission"

    # ------------------------------------------------------------------
    # Scope
    # ------------------------------------------------------------------
    def _ledger_partner(self):
        self.ensure_one()
        return self.sudo().patient_id.accounting_partner_id.commercial_partner_id

    def _ledger_care_charges(self, account):
        """Live care lines of the visit: everything but the deposit charge,
        which is money and never care."""
        self.ensure_one()
        lines = self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(
            [("billing_account_id", "=", account.id)], order="id"
        )
        return lines.filtered(
            lambda line: line.charge_state in LIVE_CHARGE_STATES and not self._is_deposit_line(line)
        )

    def _ledger_receipts(self, account):
        return self.env["hospital.charge.receipt"].sudo().search(
            [("billing_account_id", "=", account.id), ("state", "=", "confirmed")],
            order="received_at asc, id asc",
        )

    def _ledger_invoices(self, account, partner):
        """Posted hospital customer documents of this visit for THIS patient
        (a payer's documents are never the patient's receivable)."""
        moves = self.env["account.move"].sudo().search(
            [
                ("hospital_billing_account_id", "=", account.id),
                ("hospital_managed_invoice", "=", True),
                ("move_type", "in", ("out_invoice", "out_refund")),
                ("state", "=", "posted"),
            ],
            order="invoice_date asc, id asc",
        )
        return moves.filtered(lambda move: move.partner_id.commercial_partner_id == partner)

    def _ledger_applications(self, account):
        return self.env["hospital.patient.advance.application"].sudo().search(
            [("billing_account_id", "=", account.id), ("state", "=", "posted")]
        )

    def _ledger_operations(self, kinds=None):
        self.ensure_one()
        domain = [("admission_id", "=", self.id)]
        if kinds:
            domain.append(("kind", "in", list(kinds)))
        return self.env["hospital.admission.accounting.operation"].sudo().search(domain, order="id")

    def _ledger_self_pay_refusal(self, summary):
        """payer_accounting_outside_scope, or None for a genuine self-pay stay."""
        self.ensure_one()
        admission = self.sudo()
        encounter = admission.encounter_id
        account = admission._billing_account()
        if (encounter.payer_type or "self_pay") != "self_pay":
            return "payer_accounting_outside_scope"
        if account and (account.payer_type or "self_pay") != "self_pay":
            return "payer_accounting_outside_scope"
        if "patient_payer_id" in encounter._fields and encounter.patient_payer_id:
            return "payer_accounting_outside_scope"
        if summary["payer_authorized"] > AMOUNT_TOLERANCE:
            return "payer_accounting_outside_scope"
        if account:
            lines = self.env["hospital.charge.line"].sudo().with_context(active_test=False).search(
                [("billing_account_id", "=", account.id)]
            )
            if any(line.amount_sponsor_responsibility > AMOUNT_TOLERANCE for line in lines):
                return "payer_accounting_outside_scope"
        return None

    # ------------------------------------------------------------------
    # Ledger facts (read)
    # ------------------------------------------------------------------
    def _ledger_balances(self, account, config, partner):
        """(open patient receivable, open advance liability) of this visit,
        read from the entries this visit produced -- never from a total."""
        self.ensure_one()
        currency = account.currency_id or self.company_id.currency_id
        invoices = self._ledger_invoices(account, partner)
        receivable = sum(
            invoices.mapped("line_ids").filtered(
                lambda line: line.account_id.account_type == "asset_receivable"
                and line.partner_id.commercial_partner_id == partner
            ).mapped("amount_residual")
        )
        moves = (
            self._ledger_receipts(account).mapped("accounting_move_id")
            | self._ledger_applications(account).mapped("move_id")
            | self._ledger_operations(("refund", "legacy_refund")).mapped("move_id")
        ).filtered(lambda move: move.state == "posted")
        liability = -sum(
            moves.mapped("line_ids").filtered(
                lambda line: line.account_id == config.patient_advance_liability_account_id
                and line.partner_id.commercial_partner_id == partner
            ).mapped("balance")
        )
        return currency.round(receivable), currency.round(liability)

    def _accounting_annotate_refunds(self, rows):
        rows = super()._accounting_annotate_refunds(rows)
        if not rows:
            return rows
        operations = self.sudo()._ledger_operations(("refund", "legacy_refund"))
        by_log = {op.audit_log_id.id: op for op in operations if op.audit_log_id}
        by_token = {op.request_token: op for op in operations}
        for row in rows:
            op = by_log.get(row["id"])
            if not op and row.get("operation_token"):
                op = by_token.get(REFUND_TOKEN % row["operation_token"])
            if op and op.move_id and op.move_id.state == "posted":
                row["accounting_posted"] = True
                row["accounting_reference"] = op.move_id.name
                row["accounting_posted_at"] = op.performed_at
                row["payment_method"] = row.get("payment_method") or op.payment_method
                row["payment_reference"] = row.get("payment_reference") or op.payment_reference
                row["legacy"] = op.kind == "legacy_refund"
            else:
                # With this bridge installed every new refund is posted in the
                # same savepoint, so an unposted refund is one recorded before
                # refund accounting existed.
                row["legacy"] = True
        return rows

    def _accounting_status(self, summary=None):
        self.ensure_one()
        admission = self.sudo()
        status = dict(ACCOUNTING_UNAVAILABLE, refusal=None)
        account = admission._billing_account() if admission.encounter_id else False
        if admission.state not in STAY_STATES or not account:
            status.update(status="nothing_to_post", refusal="accounting_nothing_to_post")
            return status
        summary = summary or admission._inpatient_financial_summary()
        operations = admission._ledger_operations()
        last = operations[-1:] if operations else operations
        if last:
            status.update(
                reference=last.name,
                posted_at=last.performed_at,
                posted_by=last.performed_by_id.name or None,
            )
        refusal = admission._ledger_self_pay_refusal(summary)
        if refusal:
            status.update(status="outside_scope", refusal=refusal)
            return status

        legacy = [row for row in admission._accounting_refunds_out() if not row["accounting_posted"]]
        status["legacy_refund_total"] = account.currency_id.round(sum(row["amount"] for row in legacy))
        receipts = admission._ledger_receipts(account)
        unposted = receipts.filtered(lambda receipt: not receipt.accounting_move_id)
        uninvoiced = any(
            charge.qty_invoice_eligible > QTY_TOLERANCE for charge in admission._ledger_care_charges(account)
        )
        applied = sum(admission._ledger_applications(account).mapped("amount"))
        unapplied = summary["advance_applied"] - applied > AMOUNT_TOLERANCE

        if legacy:
            state = "legacy_pending"
        elif unposted or uninvoiced or unapplied:
            state = "not_posted"
        elif receipts or admission._ledger_invoices(account, admission._ledger_partner()):
            state = "posted"
        else:
            state = "nothing_to_post"
        status["status"] = state

        review = summary["financial_state"] == "needs_review"
        care_over = not admission._cashier_in_care()
        if state == "not_posted":
            if review:
                status["refusal"] = "accounting_review_required"
            elif admission.state != "discharged":
                status["refusal"] = "accounting_post_not_open"
            else:
                status["may_post"] = True
        elif state == "legacy_pending":
            if review:
                status["refusal"] = "accounting_review_required"
            elif not care_over:
                status["refusal"] = "accounting_post_not_open"
            else:
                status["may_post_legacy"] = True
                status["refusal"] = "accounting_legacy_required"
        else:
            status["refusal"] = "accounting_nothing_to_post"
        return status

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    def _ledger_assert_authorized(self):
        """hospital_billing's ACCOUNTING groups, as the real user, in an
        allowed company."""
        if not self._accounting_may_desk():
            raise AccessError(CASHIER_SETTLEMENT_MESSAGES["accounting_not_authorized"])
        for admission in self:
            if admission.sudo().company_id not in self.env.user.company_ids:
                raise AccessError(CASHIER_SETTLEMENT_MESSAGES["accounting_not_authorized"])

    @api.model
    def _ledger_clean_token(self, idempotency_key):
        try:
            return self._desk_clean_token(idempotency_key)
        except AdmissionDeskError:
            raise CashierSettlementError("inpatient_idempotency_key_required") from None

    @api.model
    def _ledger_clean_date(self, value):
        if value in (None, False, ""):
            return fields.Date.context_today(self)
        if isinstance(value, date_type):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = fields.Date.to_date(value.strip())
            except (TypeError, ValueError):
                raise AdmissionAccountingError("accounting_date_invalid") from None
        else:
            raise AdmissionAccountingError("accounting_date_invalid")
        if not parsed or parsed > fields.Date.context_today(self):
            raise AdmissionAccountingError("accounting_date_invalid")
        return parsed

    def _ledger_replay(self, token, kind, payment_method=None, payment_reference=None):
        """The earlier identical operation for this token, or None. A token
        reused by someone else, on another admission or for another act, is
        a conflict, never a replay."""
        self.ensure_one()
        admission = self.sudo()
        op = self.env["hospital.admission.accounting.operation"].sudo().search(
            [("company_id", "=", admission.company_id.id), ("request_token", "=", token)], limit=1
        )
        if not op:
            return None
        if (
            op.admission_id != admission
            or op.kind != kind
            or op.performed_by_id.id != self.env.uid
            or (op.payment_method or None) != (payment_method or None)
            or (op.payment_reference or None) != (payment_reference or None)
        ):
            raise CashierSettlementError("inpatient_operation_conflict")
        return op

    def _ledger_config(self, company):
        try:
            return self.env["hospital.billing.accounting.config"]._company_advance_config(company)
        except (UserError, ValidationError) as exc:
            raise AdmissionAccountingError(
                "accounting_configuration_incomplete", _accounting_detail(exc)
            ) from None

    # ------------------------------------------------------------------
    # The close
    # ------------------------------------------------------------------
    def _ledger_close(self, token, legacy_total=0.0, payment_method=None, payment_reference=None):
        """Post everything the settlement holds that the ledger does not:
        receipts, delivered care, the advance application. Returns the close
        operation. Caller holds the admission lock and the savepoint."""
        self.ensure_one()
        caller = self.env(su=False)
        admission = self.sudo()
        company = admission.company_id
        replay = self._ledger_replay(token, "close", payment_method, payment_reference)
        if replay:
            return replay

        now = fields.Datetime.now()
        if admission.state in ADMISSION_ACTIVE_STATES:
            # The same idempotent act settlement and discharge perform: every
            # bed-day started by now is posted, so the invoice holds the stay.
            try:
                admission._sync_stay_charges(now)
            except AdmissionWorkflowError:
                raise AdmissionAccountingError("accounting_review_required") from None
        self.env.flush_all()
        self.env.invalidate_all()

        summary = admission._inpatient_financial_summary(now)
        if summary["financial_state"] == "needs_review":
            raise AdmissionAccountingError("accounting_review_required")
        refusal = admission._ledger_self_pay_refusal(summary)
        if refusal:
            raise AdmissionAccountingError(refusal)
        account = admission._billing_account()
        if not account:
            raise AdmissionAccountingError("accounting_nothing_to_post")
        if summary["stay_unposted"] > AMOUNT_TOLERANCE or summary["procedures_actual"] > AMOUNT_TOLERANCE:
            # Value the charge engine does not hold cannot be invoiced from it.
            raise AdmissionAccountingError(
                "accounting_close_mismatch",
                "Delivered care outside the charge engine (an unposted stay or a legacy "
                "procedure bill) cannot be invoiced by the close.",
            )
        partner = admission._ledger_partner()
        if not partner:
            raise AdmissionAccountingError(
                "accounting_configuration_incomplete",
                "The patient has no accounting customer. Use Create / Link Accounting "
                "Customer first.",
            )
        self.env.cr.execute(
            "SELECT id FROM hospital_billing_account WHERE id = %s FOR UPDATE", [account.id]
        )
        config = admission._ledger_config(company)
        engine = self.env["hospital.billing.engine"].with_env(caller)

        # 1. Every confirmed patient receipt: Dr liquidity / Cr Advance Liability.
        unposted = admission._ledger_receipts(account).filtered(lambda receipt: not receipt.accounting_move_id)
        try:
            for receipt in unposted:
                receipt.with_env(caller)._post_receipt_accounting(config=config)
        except CashierSettlementError:
            raise
        except (UserError, ValidationError) as exc:
            raise AdmissionAccountingError("accounting_posting_failed", _accounting_detail(exc)) from None

        # 2. Delivered, uninvoiced care: one customer invoice, delivered
        #    quantity only (the engine's own provenance ceiling).
        invoices = self.env["account.move"]
        eligible = admission._ledger_care_charges(account).filtered(
            lambda charge: charge.qty_invoice_eligible > QTY_TOLERANCE
        )
        if eligible:
            invoice_token = "%s:invoice" % token
            try:
                invoice = engine.create_invoice(account, charges=eligible, request_token=invoice_token)
            except CashierSettlementError:
                raise
            except (UserError, ValidationError) as exc:
                raise AdmissionAccountingError(
                    "accounting_configuration_incomplete", _accounting_detail(exc)
                ) from None
            if not invoice:
                batch = self.env["hospital.invoice.batch"].sudo().with_context(active_test=False).search(
                    [("company_id", "=", company.id), ("request_token", "=", invoice_token)], limit=1
                )
                raise AdmissionAccountingError(
                    "accounting_configuration_incomplete", batch.error_summary or None
                )
            try:
                if invoice.state == "draft":
                    invoice.with_env(caller).action_post()
            except (UserError, ValidationError) as exc:
                raise AdmissionAccountingError("accounting_posting_failed", _accounting_detail(exc)) from None
            invoices |= invoice

        # 3. The advance application: the AUTHORITY's advance_applied, in total.
        applications = self.env["hospital.patient.advance.application"]
        applied = sum(admission._ledger_applications(account).mapped("amount"))
        currency = account.currency_id or company.currency_id
        to_apply = currency.round(summary["advance_applied"] - applied)
        if to_apply < -AMOUNT_TOLERANCE:
            raise AdmissionAccountingError(
                "accounting_close_mismatch",
                "More patient advance is applied in the ledger than the settlement uses.",
            )
        if to_apply > AMOUNT_TOLERANCE:
            open_invoices = admission._ledger_invoices(account, partner).filtered(
                lambda move: move.move_type == "out_invoice" and move.amount_residual > AMOUNT_TOLERANCE
            )
            try:
                for invoice in open_invoices:
                    if to_apply <= AMOUNT_TOLERANCE:
                        break
                    step = currency.round(min(to_apply, invoice.amount_residual))
                    application = engine._apply_patient_advance_to_invoice(
                        invoice,
                        amount=step,
                        request_token="%s:apply:%s" % (token, invoice.id),
                        config=config,
                    )
                    applications |= application
                    to_apply = currency.round(to_apply - application.amount)
            except CashierSettlementError:
                raise
            except (UserError, ValidationError) as exc:
                raise AdmissionAccountingError("accounting_posting_failed", _accounting_detail(exc)) from None
            if to_apply > AMOUNT_TOLERANCE:
                raise AdmissionAccountingError(
                    "accounting_close_mismatch",
                    "The patient advance could not be applied in full to the delivered-care invoice.",
                )

        # 4. PROOF: the ledger agrees with the settlement authority.
        self.env.flush_all()
        self.env.invalidate_all()
        receivable, liability = admission._ledger_balances(account, config, partner)
        expected_liability = currency.round(summary["patient_credit"] + legacy_total)
        if (
            abs(receivable - summary["remaining_due"]) > AMOUNT_TOLERANCE
            or abs(liability - expected_liability) > AMOUNT_TOLERANCE
        ):
            raise AdmissionAccountingError(
                "accounting_close_mismatch",
                "Ledger receivable %.2f / advance liability %.2f; settlement expects %.2f / %.2f."
                % (receivable, liability, summary["remaining_due"], expected_liability),
            )

        return admission._ledger_record(
            "close",
            token,
            account,
            payment_method=payment_method,
            payment_reference=payment_reference,
            receipt_ids=unposted.ids,
            invoice_ids=invoices.ids,
            application_ids=applications.ids,
            audit={
                "receipts_posted": unposted.mapped("name"),
                "invoices_posted": invoices.mapped("name"),
                "advance_applied": currency.round(sum(applications.mapped("amount"))),
                "receivable_open": receivable,
                "advance_liability_open": liability,
            },
        )

    # ------------------------------------------------------------------
    # The refund entry
    # ------------------------------------------------------------------
    def _ledger_post_refund(self, kind, token, amount, payment_method, payment_reference,
                            close, accounting_date=None, audit_log=None):
        """Dr Patient Advance Liability / Cr cash-bank, reconciled against the
        open liability of this visit's posted receipts. Returns the operation."""
        self.ensure_one()
        admission = self.sudo()
        company = admission.company_id
        account = admission._billing_account()
        partner = admission._ledger_partner()
        config = admission._ledger_config(company)
        currency = account.currency_id or company.currency_id
        amount = currency.round(amount)
        try:
            liquidity = config._refund_liquidity_account(payment_method)
        except UserError as exc:
            raise AdmissionAccountingError(
                "accounting_configuration_incomplete", _accounting_detail(exc)
            ) from None
        engine = self.env["hospital.billing.engine"].sudo()
        open_lines = engine._available_receipt_liability_lines(account, config, partner)
        available = currency.round(sum(-line.amount_residual for line in open_lines))
        if amount - available > AMOUNT_TOLERANCE:
            raise AdmissionAccountingError(
                "accounting_close_mismatch",
                "The refund is more than the open patient advance liability in the ledger.",
            )
        label = "%s / %s" % (admission.name, admission.encounter_id.name)
        try:
            move = self.env["account.move"].sudo().create({
                "move_type": "entry",
                "date": accounting_date or fields.Date.context_today(self),
                "journal_id": config.advance_refund_journal_id.id,
                "company_id": company.id,
                "currency_id": currency.id,
                "ref": "Inpatient Refund - %s" % label,
                "line_ids": [
                    (0, 0, {
                        "name": "Refund of unused patient advance - %s" % label,
                        "account_id": config.patient_advance_liability_account_id.id,
                        "partner_id": partner.id,
                        "debit": amount,
                        "credit": 0.0,
                    }),
                    (0, 0, {
                        "name": "Patient refund paid (%s) - %s" % (payment_method, label),
                        "account_id": liquidity.id,
                        "partner_id": partner.id,
                        "debit": 0.0,
                        "credit": amount,
                    }),
                ],
            })
            move.action_post()
            debit = move.line_ids.filtered(
                lambda line: line.account_id == config.patient_advance_liability_account_id and line.debit > 0
            )
            (open_lines + debit).reconcile()
        except (UserError, ValidationError) as exc:
            raise AdmissionAccountingError("accounting_posting_failed", _accounting_detail(exc)) from None
        return admission._ledger_record(
            kind,
            token,
            account,
            amount=amount,
            payment_method=payment_method,
            payment_reference=payment_reference,
            accounting_date=move.date,
            journal_id=config.advance_refund_journal_id.id,
            move_id=move.id,
            close_id=close.id if close else False,
            audit_log_id=audit_log.id if audit_log else False,
            audit={
                "refund_entry": move.name,
                "amount": amount,
                "payment_method": payment_method,
                "payment_reference": payment_reference,
                "accounting_date": fields.Date.to_string(move.date),
            },
        )

    def _ledger_record(self, kind, token, account, audit=None, **values):
        """The provenance row and its audit entry. Last, inside the savepoint."""
        self.ensure_one()
        admission = self.sudo()
        vals = {
            "kind": kind,
            "request_token": token,
            "admission_id": admission.id,
            "encounter_id": admission.encounter_id.id,
            "patient_id": admission.patient_id.id,
            "billing_account_id": account.id,
            "company_id": admission.company_id.id,
            "currency_id": (account.currency_id or admission.company_id.currency_id).id,
            "performed_by_id": self.env.uid,
        }
        for key in ("receipt_ids", "invoice_ids", "application_ids"):
            if key in values:
                values[key] = [(6, 0, values[key])]
        vals.update(values)
        with accounting_operation_capability():
            op = self.env["hospital.admission.accounting.operation"].sudo().create(vals)
        admission._audit(
            patient_id=admission.patient_id.id,
            model_name=admission._name,
            record_id=admission.id,
            action_type="update",
            description="Inpatient accounting %s %s posted on %s." % (kind.replace("_", " "), op.name, admission.name),
            new_value=json.dumps(dict(audit or {}, kind=ACCOUNTING_AUDIT_KIND, act=kind,
                                      reference=op.name, operation_token=token)),
        )
        return op

    # ------------------------------------------------------------------
    # The acts
    # ------------------------------------------------------------------
    def _ledger_legacy_rows(self):
        return [row for row in self.sudo()._accounting_refunds_out() if not row["accounting_posted"]]

    def _accounting_before_refund(self, amount, payment_method, payment_reference, token):
        """Close the stay's accounting, then post the refund entry. Raising
        refuses the whole refund (the caller's savepoint rolls it all back)."""
        self.ensure_one()
        self._ledger_assert_authorized()
        if self._ledger_legacy_rows():
            raise AdmissionAccountingError("accounting_legacy_required")
        close = self._ledger_close(REFUND_CLOSE_TOKEN % token)
        return self._ledger_post_refund(
            "refund", REFUND_TOKEN % token, amount, payment_method, payment_reference, close
        )

    def _accounting_after_refund(self, posting, audit_log):
        if posting and audit_log:
            with accounting_operation_capability():
                posting.sudo().write({"audit_log_id": audit_log.id})
        return posting

    def _accounting_post(self, idempotency_key):
        """Post accounting for a DISCHARGED stay (no refund needed, or before
        one). Returns (close operation, replayed)."""
        self.ensure_one()
        self._ledger_assert_authorized()
        token = POST_TOKEN % self._ledger_clean_token(idempotency_key)
        admission = self.sudo()
        with self.env.cr.savepoint():
            admission._lock_for_occupancy(self.env["hospital.bed"])
            replay = self._ledger_replay(token, "close")
            if replay:
                return replay, True
            if admission.state != "discharged":
                raise AdmissionAccountingError("accounting_post_not_open")
            status = admission._accounting_status()
            if status["status"] == "outside_scope":
                raise AdmissionAccountingError(status["refusal"])
            if status["status"] == "legacy_pending":
                raise AdmissionAccountingError("accounting_legacy_required")
            if status["status"] != "not_posted":
                raise AdmissionAccountingError("accounting_nothing_to_post")
            op = self._ledger_close(token)
            self.env.flush_all()
        return op, False

    def _accounting_post_legacy(self, idempotency_key, payment_method, payment_reference=None,
                                accounting_date=None):
        """Post the accounting of refunds recorded BEFORE refund accounting
        existed, with the method (and reference, and date) the Accountant
        states -- never an assumed one. The operational refund already
        happened and is not repeated. Returns (close operation, replayed)."""
        self.ensure_one()
        self._ledger_assert_authorized()
        token = LEGACY_TOKEN % self._ledger_clean_token(idempotency_key)
        payment_method, payment_reference = self._clean_refund_method(payment_method, payment_reference)
        when = self._ledger_clean_date(accounting_date)
        admission = self.sudo()
        with self.env.cr.savepoint():
            admission._lock_for_occupancy(self.env["hospital.bed"])
            replay = self._ledger_replay(token, "close", payment_method, payment_reference)
            if replay:
                return replay, True
            if admission._cashier_in_care():
                raise AdmissionAccountingError("accounting_post_not_open")
            legacy = admission._ledger_legacy_rows()
            if not legacy or any(row["amount"] <= AMOUNT_TOLERANCE for row in legacy):
                raise AdmissionAccountingError("accounting_legacy_not_applicable")
            currency = admission.company_id.currency_id
            legacy_total = currency.round(sum(row["amount"] for row in legacy))
            close = self._ledger_close(
                token, legacy_total=legacy_total,
                payment_method=payment_method, payment_reference=payment_reference,
            )
            logs = self.env["hospital.audit.log"].sudo().browse([row["id"] for row in legacy])
            for row, log in zip(legacy, logs):
                self._ledger_post_refund(
                    "legacy_refund",
                    LEGACY_REFUND_TOKEN % (token, row["id"]),
                    row["amount"],
                    payment_method,
                    payment_reference,
                    close,
                    accounting_date=when,
                    audit_log=log,
                )
            # Proof again, after the refunds: nothing but the patient's
            # current credit remains in the advance liability.
            self.env.flush_all()
            self.env.invalidate_all()
            account = admission._billing_account()
            config = admission._ledger_config(admission.company_id)
            summary = admission._inpatient_financial_summary()
            _receivable, liability = admission._ledger_balances(account, config, admission._ledger_partner())
            if abs(liability - summary["patient_credit"]) > AMOUNT_TOLERANCE:
                raise AdmissionAccountingError(
                    "accounting_close_mismatch",
                    "Advance liability %.2f after the legacy refunds; settlement expects %.2f."
                    % (liability, summary["patient_credit"]),
                )
            self.env.flush_all()
        return close, False

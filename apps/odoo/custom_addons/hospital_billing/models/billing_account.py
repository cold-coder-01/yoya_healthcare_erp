from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .charge_line import (
    ACCOUNTING_READ,
    ACCOUNTING_RECEIPT_STATES,
    AMOUNT_TOLERANCE,
    OPERATIONAL_MONEY_READ,
    CHARGE_FISCAL_STATES,
    OPERATIONAL_FUNDING_STATES,
    SETTLEMENT_STATES,
)
from .charge_receipt import PAYMENT_METHODS, REFERENCE_REQUIRED

LIVE_CHARGE_STATES = ("draft", "active")

FINANCIAL_CLEARANCE_STATES = [
    ("not_required", "Not Required"),
    ("pending", "Pending"),
    ("cleared", "Cleared"),
    ("credit_authorized", "Credit Authorized"),
    ("emergency_bypass", "Emergency Bypass"),
]


class HospitalBillingAccount(models.Model):
    _name = "hospital.billing.account"
    _description = "Hospital Billing Account"
    _order = "id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default="New")

    encounter_id = fields.Many2one(
        "hospital.encounter",
        required=True,
        ondelete="restrict",
        index=True,
        copy=False,
    )
    patient_id = fields.Many2one(
        related="encounter_id.patient_id",
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(related="encounter_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="encounter_id.currency_id", store=True, readonly=True)

    payer_type = fields.Selection(
        [
            ("self_pay", "Self Pay"),
            ("insurance", "Insurance"),
            ("corporate", "Corporate"),
            ("government", "Government"),
            ("ngo", "NGO / Charity"),
            ("other", "Other"),
        ],
        default="self_pay",
        required=True,
    )
    payer_id = fields.Many2one("res.partner", string="Payer", ondelete="restrict")

    financial_clearance_state = fields.Selection(
        FINANCIAL_CLEARANCE_STATES,
        default="not_required",
        required=True,
        index=True,
        copy=False,
        help="Financial clearance is tracked independently of the clinical encounter state. "
        "It never blocks an encounter from closing; physical discharge clearance is enforced "
        "later in hospital_admission.",
    )

    state = fields.Selection(
        [
            ("open", "Open"),
            ("partially_billed", "Partially Billed"),
            ("settled", "Settled"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        default="open",
        required=True,
        index=True,
        copy=False,
    )

    operational_funding_state = fields.Selection(
        OPERATIONAL_FUNDING_STATES,
        string="Operational Funding",
        compute="_compute_financial_meaning_states",
        store=True,
        compute_sudo=True,
        index=True,
        help="Aggregate operational funding from receipt allocations. It is independent "
        "of invoice, accounting receipt, settlement and fiscal states.",
    )
    accounting_receipt_state = fields.Selection(
        ACCOUNTING_RECEIPT_STATES,
        string="Receipt Accounting",
        compute="_compute_financial_meaning_states",
        store=True,
        compute_sudo=True,
        index=True,
    )
    invoice_state = fields.Selection(
        [
            ("not_invoiced", "Not Invoiced"),
            ("partially_invoiced", "Partially Invoiced"),
            ("invoiced", "Invoiced"),
            ("partially_credited", "Partially Credited"),
            ("credited", "Credited"),
        ],
        string="Invoice State",
        compute="_compute_financial_meaning_states",
        store=True,
        compute_sudo=True,
        index=True,
    )
    settlement_state = fields.Selection(
        SETTLEMENT_STATES,
        string="Accounting Settlement",
        compute="_compute_financial_meaning_states",
        store=True,
        compute_sudo=True,
        index=True,
    )
    fiscal_state = fields.Selection(
        CHARGE_FISCAL_STATES,
        string="Fiscal Event",
        compute="_compute_financial_meaning_states",
        store=True,
        compute_sudo=True,
        index=True,
    )

    charge_line_ids = fields.One2many("hospital.charge.line", "billing_account_id", string="Charge Lines")
    charge_line_count = fields.Integer(compute="_compute_charge_line_count", compute_sudo=True)
    receipt_ids = fields.One2many(
        "hospital.charge.receipt", "billing_account_id", string="Payment Receipts",
        groups=OPERATIONAL_MONEY_READ,
    )
    receipt_count = fields.Integer(compute="_compute_charge_line_count", compute_sudo=True)

    # Clinical value totals: readable by any role that can read the account.
    amount_estimated = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2), compute_sudo=True)
    amount_authorized = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2), compute_sudo=True)
    amount_bypassed = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2), compute_sudo=True)
    amount_delivered = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2), compute_sudo=True)
    amount_eligible = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2), compute_sudo=True)

    # Cashier-visible operational figures.
    amount_to_invoice = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ)
    amount_received = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ)
    amount_outstanding = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="Sum of the charge lines' receivables. No cross-line netting.",
    )
    amount_prepayment_held = fields.Float(
        string="Advance Held",
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="Cash received but not applied and not refunded. A deposit, not a debt.",
    )
    amount_patient_credit = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="Over-collection owed back to the patient/payer, net of credit refunds made.",
    )
    amount_due_for_clearance = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="Operational pre-service payment requirement. Not an accounting receivable.",
    )

    # Accounting-only: invoice, credit and refund detail.
    amount_invoiced = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ)
    amount_credited = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ)
    amount_applied_to_invoice = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ)
    amount_refunded_from_advance = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ)
    amount_refunded_from_credit = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ)
    amount_refunded = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ)
    net_invoiced_amount = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ)
    receivable_balance = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ,
        help="Signed: net_invoiced_amount - amount_applied_to_invoice.",
    )
    adjustment_required_count = fields.Integer(
        compute="_compute_amounts", store=True, compute_sudo=True,
        help="Live charges whose delivery and invoiced quantities disagree.",
    )

    notes = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "billing_account_encounter_unique",
            "unique(encounter_id)",
            "A billing account already exists for this encounter.",
        ),
        (
            "billing_account_name_company_unique",
            "unique(name, company_id)",
            "A billing account with this reference already exists for this company.",
        ),
    ]

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends("charge_line_ids", "receipt_ids")
    def _compute_charge_line_count(self):
        for account in self:
            account.charge_line_count = len(account.charge_line_ids)
            account.receipt_count = len(account.receipt_ids)

    @api.depends(
        "charge_line_ids.amount_estimated",
        "charge_line_ids.amount_delivered",
        "charge_line_ids.amount_eligible",
        "charge_line_ids.amount_to_invoice",
        "charge_line_ids.amount_invoiced",
        "charge_line_ids.amount_credited",
        "charge_line_ids.amount_received",
        "charge_line_ids.amount_applied_to_invoice",
        "charge_line_ids.amount_refunded_from_advance",
        "charge_line_ids.amount_refunded_from_credit",
        "charge_line_ids.amount_outstanding",
        "charge_line_ids.amount_prepayment_held",
        "charge_line_ids.amount_patient_credit",
        "charge_line_ids.amount_due_for_clearance",
        "charge_line_ids.adjustment_state",
        "charge_line_ids.charge_state",
        "charge_line_ids.authorization_state",
    )
    def _compute_amounts(self):
        """Every total is a direct aggregation of the corresponding line field.

        Two different scopes, deliberately:

        * ESTIMATES (estimated/delivered/eligible/to_invoice/clearance) cover LIVE
          charges only. A cancelled charge's own estimate is preserved on the line
          as history, but it must not inflate the account's forward-looking totals.

        * FISCAL AND CASH FACTS (invoiced/credited/received/applied/refunded, and
          everything derived from them) cover ALL charges, cancelled ones included.
          Cash the hospital physically holds against a cancelled prepaid service is
          still cash it holds and must return -- excluding it would hide a real
          liability. Likewise an invoice raised before a reversal still existed.
        """
        for account in self:
            lines = account.charge_line_ids
            live = lines.filtered(lambda l: l.charge_state in LIVE_CHARGE_STATES)

            # --- forward-looking estimates: live charges only
            account.amount_estimated = sum(live.mapped("amount_estimated"))
            account.amount_delivered = sum(live.mapped("amount_delivered"))
            account.amount_eligible = sum(live.mapped("amount_eligible"))
            account.amount_to_invoice = sum(live.mapped("amount_to_invoice"))
            account.amount_due_for_clearance = sum(live.mapped("amount_due_for_clearance"))
            account.adjustment_required_count = len(
                live.filtered(
                    lambda l: l.adjustment_state in ("additional_invoice_required", "credit_required")
                )
            )

            # 'bypassed' is emergency care rendered without payer authorization.
            # It is tracked separately and is deliberately NOT counted as authorized.
            account.amount_authorized = sum(
                live.filtered(lambda l: l.authorization_state in ("authorized", "not_required"))
                .mapped("amount_estimated")
            )
            account.amount_bypassed = sum(
                live.filtered(lambda l: l.authorization_state == "bypassed").mapped("amount_estimated")
            )

            # --- fiscal and cash facts: ALL charges, including cancelled/reversed
            account.amount_invoiced = sum(lines.mapped("amount_invoiced"))
            account.amount_credited = sum(lines.mapped("amount_credited"))
            account.amount_received = sum(lines.mapped("amount_received"))
            account.amount_applied_to_invoice = sum(lines.mapped("amount_applied_to_invoice"))
            account.amount_refunded_from_advance = sum(lines.mapped("amount_refunded_from_advance"))
            account.amount_refunded_from_credit = sum(lines.mapped("amount_refunded_from_credit"))
            account.amount_refunded = sum(lines.mapped("amount_refunded"))

            account.net_invoiced_amount = account.amount_invoiced - account.amount_credited
            account.receivable_balance = (
                account.net_invoiced_amount - account.amount_applied_to_invoice
            )

            # Aggregate the LINES' own derived figures rather than re-deriving from
            # account netting: a receivable on one line must not be silently cancelled
            # out by an unapplied advance sitting on another.
            account.amount_outstanding = sum(lines.mapped("amount_outstanding"))
            account.amount_prepayment_held = sum(lines.mapped("amount_prepayment_held"))
            account.amount_patient_credit = sum(lines.mapped("amount_patient_credit"))

    @api.depends(
        "amount_received",
        "amount_refunded",
        "amount_eligible",
        "net_invoiced_amount",
        "charge_line_ids.invoice_state",
        "charge_line_ids.settlement_state",
        "charge_line_ids.fiscal_state",
        "receipt_ids.state",
        "receipt_ids.accounting_posted",
    )
    def _compute_financial_meaning_states(self):
        for account in self:
            net_funding = max(0.0, account.amount_received - account.amount_refunded)
            target = max(0.0, account.amount_eligible)
            if net_funding <= AMOUNT_TOLERANCE:
                account.operational_funding_state = (
                    "refunded"
                    if account.amount_received > AMOUNT_TOLERANCE
                    and account.amount_refunded + AMOUNT_TOLERANCE >= account.amount_received
                    else "not_funded"
                )
            elif target > AMOUNT_TOLERANCE and net_funding + AMOUNT_TOLERANCE < target:
                account.operational_funding_state = "partially_funded"
            elif target > AMOUNT_TOLERANCE and net_funding > target + AMOUNT_TOLERANCE:
                account.operational_funding_state = "overfunded"
            else:
                account.operational_funding_state = "funded"

            receipts = account.receipt_ids.filtered(lambda receipt: receipt.state == "confirmed")
            if not receipts:
                account.accounting_receipt_state = "not_received"
            else:
                posted_count = len(receipts.filtered("accounting_posted"))
                account.accounting_receipt_state = (
                    "unposted"
                    if not posted_count
                    else "posted"
                    if posted_count == len(receipts)
                    else "partially_posted"
                )

            live = account.charge_line_ids.filtered(
                lambda line: line.charge_state in LIVE_CHARGE_STATES
            )
            invoice_states = set(live.mapped("invoice_state"))
            if not invoice_states or invoice_states == {"not_invoiced"}:
                account.invoice_state = "not_invoiced"
            elif invoice_states == {"invoiced"}:
                account.invoice_state = "invoiced"
            elif invoice_states == {"credited"}:
                account.invoice_state = "credited"
            elif "partially_credited" in invoice_states or "credited" in invoice_states:
                account.invoice_state = "partially_credited"
            else:
                account.invoice_state = "partially_invoiced"

            settlement_states = set(account.charge_line_ids.mapped("settlement_state"))
            if account.net_invoiced_amount <= AMOUNT_TOLERANCE:
                account.settlement_state = "not_applicable"
            elif settlement_states and settlement_states <= {"settled", "not_applicable"}:
                account.settlement_state = "settled"
            elif "settled" in settlement_states or "partially_settled" in settlement_states:
                account.settlement_state = "partially_settled"
            else:
                account.settlement_state = "unsettled"

            fiscal_states = set(account.charge_line_ids.mapped("fiscal_state"))
            if not fiscal_states or fiscal_states == {"not_started"}:
                account.fiscal_state = "not_started"
            elif fiscal_states == {"completed"}:
                account.fiscal_state = "completed"
            elif "error" in fiscal_states:
                account.fiscal_state = "error"
            elif fiscal_states == {"reversed"}:
                account.fiscal_state = "reversed"
            else:
                account.fiscal_state = "pending"

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("payer_type", "payer_id")
    def _check_payer(self):
        for account in self:
            if account.payer_type != "self_pay" and not account.payer_id:
                raise ValidationError("A payer is required when the payer type is not Self Pay.")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("hospital.billing.account.sequence") or "New"
        return super().create(vals_list)

    def write(self, vals):
        protected_financial_states = {
            "operational_funding_state",
            "accounting_receipt_state",
            "invoice_state",
            "settlement_state",
            "fiscal_state",
        }
        if not self.env.su and (protected_financial_states & set(vals)):
            raise AccessError(
                "Operational funding, receipt accounting, invoicing, settlement and "
                "fiscal states are derived by controlled workflows and cannot be "
                "changed directly."
            )
        for account in self:
            if account.state in ("closed", "cancelled"):
                blocked = set(vals) - {"active", "state", "notes"}
                if blocked:
                    raise UserError(
                        f"Billing account {account.name} is {account.state} and cannot be modified. "
                        f"Blocked fields: {', '.join(sorted(blocked))}."
                    )
        return super().write(vals)

    def unlink(self):
        for account in self:
            if account.charge_line_ids:
                raise UserError(
                    f"Billing account {account.name} has charge lines and cannot be deleted. Cancel it instead."
                )
            if account.state != "open":
                raise UserError(f"Billing account {account.name} is {account.state} and cannot be deleted.")
        return super().unlink()

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_settle(self):
        for account in self:
            if account.state in ("closed", "cancelled"):
                raise UserError(f"Billing account {account.name} is {account.state}.")
            if account.amount_outstanding > 0:
                raise UserError(
                    f"Billing account {account.name} still has an outstanding balance of "
                    f"{account.amount_outstanding:.2f} and cannot be settled."
                )
            if account.settlement_state != "settled":
                raise UserError(
                    f"Billing account {account.name} has not been settled through posted "
                    "and reconciled accounting receivables. Operational funding alone "
                    "cannot mark it Settled."
                )
        return self.write({"state": "settled"})

    def action_close(self):
        for account in self:
            if account.state != "settled":
                raise UserError(
                    f"Billing account {account.name} must be settled before it can be closed."
                )
        return self.write({"state": "closed"})

    def action_cancel(self):
        for account in self:
            if account.amount_invoiced > 0:
                raise UserError(
                    f"Billing account {account.name} has invoiced charges and cannot be cancelled."
                )
            if account.state in ("closed", "cancelled"):
                raise UserError(f"Billing account {account.name} is already {account.state}.")
        return self.write({"state": "cancelled"})

    def action_record_payment(self):
        """One payment event settling any/all of this account's outstanding charges."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Record Payment / Advance",
            "res_model": "hospital.charge.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_source_billing_account_id": self.id},
        }

    def record_operational_payment(
        self,
        amount,
        payment_method,
        payment_reference=None,
        note=None,
        intake_token=None,
    ):
        """Record one operational payment through the canonical wizard path.

        This method is intentionally a thin server-side launcher: the receipt,
        allocation, confirmation, audit and payment-state logic remains in
        hospital.charge.payment.wizard.action_confirm().
        """
        self.ensure_one()

        Allocation = self.env["hospital.charge.receipt.allocation"]
        Allocation._assert_intake_group("record a payment")

        if amount is None or amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")

        valid_methods = {key for key, _label in PAYMENT_METHODS}
        if payment_method not in valid_methods:
            raise ValidationError(
                "Payment method must be one of %s." % ", ".join(sorted(valid_methods))
            )

        if payment_method in REFERENCE_REQUIRED and not (
            payment_reference or ""
        ).strip():
            raise ValidationError(
                "A %s payment requires an external reference."
                % dict(PAYMENT_METHODS)[payment_method]
            )

        Receipt = self.env["hospital.charge.receipt"]
        if intake_token:
            existing = Receipt.search([("intake_token", "=", intake_token)], limit=1)
            if existing:
                same_action = (
                    existing.billing_account_id == self
                    and abs(existing.amount - amount) <= AMOUNT_TOLERANCE
                    and existing.payment_method == payment_method
                    and (existing.payment_reference or "") == (payment_reference or "")
                    and (existing.note or "") == (note or "")
                    and existing.state == "confirmed"
                )
                if same_action:
                    return existing
                raise ValidationError(
                    "This idempotency key has already been used for a different payment."
                )

        Wizard = (
            self.env["hospital.charge.payment.wizard"]
            .with_context(default_source_billing_account_id=self.id)
        )
        values = Wizard.default_get(list(Wizard._fields))
        values.update(
            {
                "payment_method": payment_method,
                "payment_reference": payment_reference or False,
                "note": note or False,
            }
        )
        if intake_token:
            values["intake_token"] = intake_token

        wizard = Wizard.create(values)
        if not wizard.line_ids:
            raise UserError("There are no payable charges on this billing account.")

        remaining = float(amount)
        payable_lines = wizard.line_ids.sorted("id")
        for line in payable_lines:
            if remaining <= AMOUNT_TOLERANCE:
                line.amount = 0.0
                continue
            allocation = min(remaining, line.amount_available)
            line.amount = allocation
            remaining -= allocation

        if remaining > AMOUNT_TOLERANCE:
            payable_lines[-1].amount += remaining

        action = wizard.action_confirm()
        receipt = wizard.receipt_id
        if not receipt and isinstance(action, dict) and action.get("res_id"):
            receipt = Receipt.browse(action["res_id"])
        return receipt
    def action_view_receipts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Payment Receipts",
            "res_model": "hospital.charge.receipt",
            "view_mode": "list,form",
            "domain": [("billing_account_id", "=", self.id)],
        }

    def action_view_charge_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Charge Lines",
            "res_model": "hospital.charge.line",
            "view_mode": "list,form",
            "domain": [("billing_account_id", "=", self.id)],
            "context": {"default_billing_account_id": self.id},
        }


class HospitalEncounterBilling(models.Model):
    """Adds the encounter -> billing account link.

    This lives in hospital_billing rather than hospital_management because
    hospital_billing depends on hospital_management; declaring the Many2one
    upstream would create a circular dependency.
    """

    _inherit = "hospital.encounter"

    billing_account_ids = fields.One2many(
        "hospital.billing.account", "encounter_id", string="Billing Accounts (technical)",
    )
    billing_account_id = fields.Many2one(
        "hospital.billing.account",
        string="Billing Account",
        compute="_compute_billing_account_id",
        search="_search_billing_account_id",
        compute_sudo=True,
    )
    financial_clearance_state = fields.Selection(
        FINANCIAL_CLEARANCE_STATES,
        string="Financial Clearance",
        compute="_compute_financial_clearance_state",
        search="_search_financial_clearance_state",
        compute_sudo=True,
        help="Mirror of the billing account's clearance state. Read-only on the encounter; "
        "set it on the billing account itself.",
    )

    @api.depends("billing_account_ids")
    def _compute_billing_account_id(self):
        # A unique constraint on billing_account.encounter_id guarantees at most
        # one account, so the [:1] slice is a singleton, never an arbitrary pick.
        for encounter in self:
            encounter.billing_account_id = encounter.billing_account_ids[:1]

    @api.model
    def _search_billing_account_id(self, operator, value):
        return [("billing_account_ids", operator, value)]

    @api.depends("billing_account_ids.financial_clearance_state")
    def _compute_financial_clearance_state(self):
        for encounter in self:
            account = encounter.billing_account_ids[:1]
            encounter.financial_clearance_state = account.financial_clearance_state or False

    @api.model
    def _search_financial_clearance_state(self, operator, value):
        # Non-stored compute: without this, any filter or group-by on the field
        # would raise rather than silently work. Delegate to the One2many so the
        # search is executed against the billing account table.
        return [("billing_account_ids.financial_clearance_state", operator, value)]

    # NOTE: hospital_billing deliberately does NOT override _check_can_close().
    #
    # Clinical state and financial state are independent. An encounter may close
    # with insurance claims pending, corporate credit extended, emergency debt
    # written against it, or patient receivables outstanding. The billing account
    # simply remains open after the encounter closes and is settled on its own
    # timeline. Physical discharge clearance is a separate concern and will be
    # enforced in hospital_admission, not here.

    def action_view_billing_account(self):
        self.ensure_one()
        account = self.billing_account_id
        if not account:
            raise UserError(f"Encounter {self.name} has no billing account yet.")
        return {
            "type": "ir.actions.act_window",
            "name": "Billing Account",
            "res_model": "hospital.billing.account",
            "view_mode": "form",
            "res_id": account.id,
        }

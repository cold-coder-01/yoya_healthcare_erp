import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

FROZEN_CHARGE_STATES = ("cancelled", "reversed")
INVOICED_STATES = (
    "partially_invoiced",
    "invoiced",
    "partially_credited",
    "credited",
)

QTY_TOLERANCE = 1e-6
AMOUNT_TOLERANCE = 0.005  # half a cent

OPERATIONAL_FUNDING_STATES = [
    ("not_funded", "Not Funded"),
    ("partially_funded", "Partially Funded"),
    ("funded", "Funded / Advance Allocated"),
    ("overfunded", "Overfunded"),
    ("refunded", "Funding Refunded"),
]
ACCOUNTING_RECEIPT_STATES = [
    ("not_received", "No Operational Receipt"),
    ("unposted", "Accounting Unposted"),
    ("partially_posted", "Partially Accounting Posted"),
    ("posted", "Accounting Posted"),
]
SETTLEMENT_STATES = [
    ("not_applicable", "Not Yet Applicable"),
    ("unsettled", "Unsettled"),
    ("partially_settled", "Partially Settled"),
    ("settled", "Settled / Reconciled"),
]
CHARGE_FISCAL_STATES = [
    ("not_started", "Not Started"),
    ("pending", "Pending"),
    ("completed", "Completed"),
    ("error", "Error"),
    ("reversed", "Reversed"),
]

# ----------------------------------------------------------------------
# SECURITY MODEL
#
# An Odoo context key is CALLER-CONTROLLED: an RPC client can pass any context
# it likes. A context marker therefore proves nothing and is NEVER an
# authorization boundary here. It is used only to carry the reason string and to
# mark provenance.
#
# The real boundaries live inside write(), which every path -- controlled method,
# direct ORM write, or forged-context RPC call -- must pass through:
#
#   1. GROUP CHECK      per protected field, against real res.groups membership
#   2. CEILING CHECK    @api.constrains, enforced on every write regardless of path
#   3. REASON REQUIRED  refunds and post-invoice delivery corrections
#   4. AUDIT            written by write() itself, so no future method can omit it
#
# Consequence: forging the context buys a caller nothing. It cannot grant a group,
# cannot lift a ceiling, cannot skip the reason, and cannot suppress the audit log.
# ----------------------------------------------------------------------

# Provenance/reason carriers. NOT authorization.
ALLOCATION_REASON_CTX = "hospital_billing_allocation_reason"
ALLOCATION_REFERENCE_CTX = "hospital_billing_allocation_reference"
DELIVERY_REASON_CTX = "hospital_billing_delivery_reason"

# Existing hospital_management groups.
G_RECEPTIONIST = "hospital_management.group_hospital_receptionist"
G_DOCTOR = "hospital_management.group_hospital_doctor"
G_NURSE = "hospital_management.group_hospital_nurse"
G_PHARMACIST = "hospital_management.group_hospital_pharmacist"
G_LAB = "hospital_management.group_hospital_lab_technician"
G_ACCOUNTANT = "hospital_management.group_hospital_accountant"
G_MANAGER = "hospital_management.group_hospital_manager"
G_ADMIN = "hospital_management.group_hospital_system_administrator"

# Owned by THIS module (security/hospital_billing_groups.xml). Cash intake is a
# billing act, so the group that authorizes it must be resolvable from billing
# code without depending on any downstream module.
G_CASHIER = "hospital_billing.group_hospital_cashier"

# THE authorization boundary for operational cash intake: creating a receipt,
# allocating it against charges, and confirming it.
#
# THE SINGLE SOURCE OF TRUTH. charge_receipt.py and pharmacy_billing.py import
# this tuple rather than restating it; yoya_emr_api mirrors it for capability
# reporting and asserts equality in its tests. Three independent copies used to
# exist, which is how the standalone Cashier group came to be locked out of the
# only path that actually moves money.
#
# The receptionist is deliberately ABSENT. Registering a patient and taking their
# money are separable duties; a receptionist observes payment state (see
# OPERATIONAL_MONEY_READ) but cannot create it. Manager and admin are listed
# explicitly -- manager implies receptionist, not cashier.
OPERATIONAL_INTAKE_GROUPS = (G_CASHIER, G_ACCOUNTANT, G_MANAGER, G_ADMIN)

# Applying and refunding money is an accounting act.
ACCOUNTING_GROUPS = (G_ACCOUNTANT, G_MANAGER, G_ADMIN)

# Over-application correction is manager-only (see adjust_applied_amount).
ADJUSTMENT_GROUPS = (G_MANAGER, G_ADMIN)

# Recording clinical delivery.
DELIVERY_GROUPS = (
    G_DOCTOR, G_NURSE, G_PHARMACIST, G_LAB, G_RECEPTIONIST, G_MANAGER, G_ADMIN,
)

# Per-field write authorization. Checked in write() on EVERY path.
#
# amount_received is NOT here any more: it is a stored COMPUTE derived from the
# confirmed receipt allocations, so it cannot be written at all -- by anyone, through
# any path. Cash enters only by creating a receipt allocation, which is itself
# group-guarded on hospital.charge.receipt.allocation.
ALLOCATION_FIELD_GROUPS = {
    "amount_applied_to_invoice": ACCOUNTING_GROUPS,
    "amount_refunded_from_advance": ACCOUNTING_GROUPS,
    "amount_refunded_from_credit": ACCOUNTING_GROUPS,
}
ALLOCATION_FIELDS = set(ALLOCATION_FIELD_GROUPS)

# Money leaving the hospital always needs a documented reason.
REFUND_FIELDS = {"amount_refunded_from_advance", "amount_refunded_from_credit"}

# Read visibility for operational money fields (cash in, what is still owed).
#
# WIDER than OPERATIONAL_INTAKE_GROUPS on purpose, and the difference is the
# whole point of the split: the cashier must SEE these to collect, and the
# receptionist must SEE them to tell a patient why they cannot proceed to
# triage -- but only the cashier may act on them.
#
# The cashier was missing here until now, which meant a cashier could not read
# amount_received or amount_due_for_clearance at all. Widening the intake tuple
# without also widening this would have produced a cashier who may take money
# but cannot see how much to take.
OPERATIONAL_MONEY_READ = ",".join(
    (G_CASHIER, G_RECEPTIONIST, G_ACCOUNTANT, G_MANAGER, G_ADMIN)
)
ACCOUNTING_READ = ",".join((G_ACCOUNTANT, G_MANAGER, G_ADMIN))

# Quantities that must not be rewritten by hand once anything has been invoiced.
# Delivery may still legitimately change -- but only through record_delivery(),
# which audits the change and re-derives adjustment_state.
POST_INVOICE_PROTECTED_QTY = {"qty_requested", "qty_delivered", "delivery_state"}

# Pricing terms that must not change once any part of the charge has been
# invoiced. Quantities are deliberately ABSENT: delivery may keep moving after
# invoicing, and the difference is resolved by a further charge or a credit --
# never by mutating what was already invoiced.
FROZEN_PRICING_FIELDS = {
    "unit_price",
    "discount",
    "tax_treatment",
    "tax_rate",
    "service_id",
    "billing_basis",
}


class HospitalChargeLine(models.Model):
    _name = "hospital.charge.line"
    _description = "Hospital Charge Line"
    _order = "encounter_id, id"

    name = fields.Char(required=True, readonly=True, copy=False, default="New")

    billing_account_id = fields.Many2one(
        "hospital.billing.account",
        required=True,
        ondelete="cascade",
        index=True,
    )
    encounter_id = fields.Many2one(
        related="billing_account_id.encounter_id",
        store=True,
        readonly=True,
        index=True,
    )
    patient_id = fields.Many2one(
        related="billing_account_id.patient_id",
        store=True,
        readonly=True,
        index=True,
    )
    company_id = fields.Many2one(related="billing_account_id.company_id", store=True, readonly=True)
    currency_id = fields.Many2one(related="billing_account_id.currency_id", store=True, readonly=True)

    service_id = fields.Many2one("hospital.billing.service", ondelete="restrict")
    description = fields.Char(required=True)
    uom_id = fields.Many2one("uom.uom", string="Unit of Measure")

    billing_basis = fields.Selection(
        [
            ("delivery", "Delivery Based"),
            ("prepaid", "Prepayment Required"),
        ],
        default="delivery",
        required=True,
        help="Snapshotted from the service at charge creation. Determines whether the eligible "
        "quantity follows what was requested (prepaid) or what was actually delivered.",
    )

    # ------------------------------------------------------------------
    # Quantities
    #
    #   qty_requested  what was ordered
    #   qty_delivered  what was actually performed / dispensed
    #   qty_billable   ELIGIBLE delivered quantity, regardless of prepayment.
    #                  Never frozen. Delivery may keep moving after invoicing.
    #   qty_invoiced   what has been placed on a fiscal document (invoice truth)
    #   qty_credited   what has been credited back off a fiscal document
    #   qty_to_invoice max(0, qty_billable - (qty_invoiced - qty_credited))
    #
    # Payment never constrains any of these.
    # ------------------------------------------------------------------
    qty_requested = fields.Float(default=1.0, digits=(16, 3))
    qty_delivered = fields.Float(default=0.0, digits=(16, 3))
    qty_billable = fields.Float(
        string="Eligible Quantity",
        compute="_compute_qty_billable",
        store=True,
        digits=(16, 3),
        help="Delivered quantity eligible to be invoiced. Operational prepayment "
        "does not make an undelivered quantity invoiceable. "
        "Not frozen by invoicing -- qty_invoiced carries the fiscal truth.",
    )
    qty_invoiced = fields.Float(
        default=0.0,
        digits=(16, 3),
        readonly=True,
        copy=False,
        help="Maintained by the invoicing phase. Never written by clinical modules.",
    )
    qty_credited = fields.Float(
        default=0.0,
        digits=(16, 3),
        readonly=True,
        copy=False,
        help="Maintained by the credit-note phase. Never written by clinical modules.",
    )
    qty_to_invoice = fields.Float(compute="_compute_qty_to_invoice", store=True, digits=(16, 3))
    net_invoiced_qty = fields.Float(
        compute="_compute_qty_to_invoice", store=True, digits=(16, 3),
        help="qty_invoiced - qty_credited. What the fiscal documents currently assert.",
    )
    delivery_invoice_variance_qty = fields.Float(
        string="Delivery/Invoice Variance",
        compute="_compute_variance", store=True, digits=(16, 3),
        help="qty_delivered - net_invoiced_qty. Positive: more was delivered than invoiced. "
        "Negative: more was invoiced than delivered.",
    )
    adjustment_state = fields.Selection(
        [
            ("none", "None"),
            ("additional_invoice_required", "Additional Invoice Required"),
            ("credit_required", "Credit Required"),
            ("resolved", "Resolved"),
        ],
        compute="_compute_variance",
        store=True,
        index=True,
        help="Derived from the delivery/invoice variance. Phase 1 surfaces the need for an "
        "adjustment; it does not create the fiscal document.",
    )

    unit_price = fields.Float(digits=(16, 2))
    discount = fields.Float(string="Discount (%)", default=0.0)
    price_unit_net = fields.Float(
        string="Net Unit Price",
        compute="_compute_price_unit_net",
        store=True,
        digits=(16, 2),
    )

    # ------------------------------------------------------------------
    # Tax snapshot.
    #
    # PHASE 1 CAVEAT: tax_amount_estimated below is an ESTIMATE produced by a
    # flat percentage applied to the net line amount. It is NOT an authoritative
    # tax computation. It does not model account.tax, tax groups, price-included
    # taxes, fiscal positions, per-line vs per-document rounding, or the rounding
    # rules the tax authority requires. When accounting integration lands, the
    # authoritative figures must come from Odoo's configured tax objects on the
    # fiscal document; these snapshot fields exist to preserve WHICH treatment
    # applied at the time of service, and to give staff an indicative total.
    # ------------------------------------------------------------------
    tax_treatment = fields.Selection(
        [
            ("exempt", "Exempt"),
            ("standard", "Standard Rated"),
            ("zero_rated", "Zero Rated"),
            ("out_of_scope", "Out of Scope"),
        ],
        default="exempt",
        required=True,
    )
    tax_rate = fields.Float(string="Tax Rate (%)", default=0.0)
    tax_amount_estimated = fields.Float(
        string="Estimated Tax",
        compute="_compute_amounts",
        store=True,
        digits=(16, 2),
        help="Indicative only. Authoritative tax is computed by the accounting layer.",
    )

    # ------------------------------------------------------------------
    # Monetary meanings, each on its OWN quantity base. Never interchangeable.
    #
    #   amount_estimated  <- qty_requested   (what was ordered; survives cancellation)
    #   amount_delivered  <- qty_delivered   (clinical value rendered)
    #   amount_eligible   <- qty_billable    (what MAY be invoiced)
    #   amount_to_invoice <- qty_to_invoice  (what is still to be invoiced)
    #   amount_invoiced   <- qty_invoiced    (fiscal fact)
    #   amount_credited   <- qty_credited    (fiscal fact)
    # ------------------------------------------------------------------
    price_subtotal = fields.Float(
        string="Estimated Untaxed",
        compute="_compute_amounts", store=True, digits=(16, 2),
        help="qty_requested valued at the net unit price, before estimated tax.",
    )
    amount_estimated = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        help="REQUESTED quantity valued at the net unit price, plus estimated tax. "
        "This is the historical line estimate and is NOT destroyed by cancellation -- "
        "cancelled lines are excluded from account totals instead.",
    )
    amount_delivered = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        help="DELIVERED quantity valued at the net unit price. Clinical value rendered.",
    )
    amount_eligible = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        help="ELIGIBLE (billable) quantity valued at the net unit price.",
    )
    amount_to_invoice = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
    )
    amount_invoiced = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ,
    )
    amount_credited = fields.Float(
        compute="_compute_amounts", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ,
    )

    # ------------------------------------------------------------------
    # CASH ALLOCATION BUCKETS.
    #
    # There is no single "amount_paid". Money received is not the same thing as
    # money applied to an invoice: a prepayment sits as an ADVANCE until the
    # allocation phase explicitly applies it. Nothing here is auto-applied merely
    # because an invoice came into existence.
    #
    # All four are written ONLY through the controlled allocation methods below.
    # ------------------------------------------------------------------
    # Cashier-visible: front-desk staff must see money in and what is still owed.
    #
    # SINGLE SOURCE OF TRUTH: amount_received is DERIVED from the confirmed receipt
    # allocations pointing at this charge. It is never incremented by a separate write,
    # so a receipt total and a charge total cannot drift apart.
    allocation_ids = fields.One2many(
        "hospital.charge.receipt.allocation", "charge_line_id",
        string="Receipt Allocations", groups=OPERATIONAL_MONEY_READ,
    )
    amount_received = fields.Float(
        compute="_compute_amount_received", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="Cash received against this charge. Reconciles exactly to the sum of the "
        "CONFIRMED receipt allocations for it.",
    )
    amount_prepayment_held = fields.Float(
        string="Advance Held",
        compute="_compute_outstanding", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="Cash received but not yet applied to an invoice and not yet refunded. "
        "This is a DEPOSIT, not a debt owed to the patient.",
    )
    amount_outstanding = fields.Float(
        compute="_compute_outstanding", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="max(0, receivable_balance). Receivable only.",
    )
    amount_patient_credit = fields.Float(
        compute="_compute_outstanding", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="Over-collection against invoiced amounts, net of credit refunds already made. "
        "This IS a debt owed back to the patient.",
    )
    amount_due_for_clearance = fields.Float(
        compute="_compute_outstanding", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="OPERATIONAL pre-service payment requirement for prepaid services. "
        "Not an accounting receivable -- it exists before any invoice does.",
    )

    # ------------------------------------------------------------------
    # SPONSOR / PATIENT RESPONSIBILITY SPLIT (Phase 3).
    #
    # amount_estimated is the authoritative basis: it is the number this gate
    # already divides, and it exists before delivery (amount_eligible is zero
    # until then, and a pre-service split cannot divide zero).
    #
    # The patient figure is a RESIDUAL, never a stored decision. Only an
    # AUTHORIZED sponsor row reduces it -- a draft is a proposal and buys the
    # patient nothing at the cashier's window.
    # ------------------------------------------------------------------
    responsibility_ids = fields.One2many(
        "hospital.charge.responsibility", "charge_id",
        string="Sponsor Responsibility", groups=OPERATIONAL_MONEY_READ,
    )
    amount_sponsor_responsibility = fields.Float(
        compute="_compute_responsibility", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="Sponsor share claimed on this charge: drafted plus authorized. "
        "What is PROPOSED, which is not what the patient may rely on.",
    )
    amount_sponsor_authorized = fields.Float(
        compute="_compute_responsibility", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="Sponsor share that has been explicitly authorized. This is the "
        "only figure that reduces what the patient is asked to pay.",
    )
    amount_patient_responsibility = fields.Float(
        compute="_compute_responsibility", store=True, digits=(16, 2),
        compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
        help="max(0, amount_estimated - amount_sponsor_authorized). A residual, "
        "so it cannot disagree with its own definition.",
    )
    responsibility_state = fields.Selection(
        [
            ("self_pay", "Patient Only"),
            ("proposed", "Sponsor Share Proposed"),
            ("authorized", "Sponsor Share Authorized"),
        ],
        compute="_compute_responsibility", store=True, compute_sudo=True,
        index=True,
        help="'self_pay' means no live sponsor row exists -- which is also what "
        "a genuine self-pay visit looks like, deliberately.",
    )

    # Accounting-only: application and refund detail is not front-desk information.
    amount_applied_to_invoice = fields.Float(
        default=0.0, digits=(16, 2), readonly=True, copy=False, groups=ACCOUNTING_READ,
        help="Portion of the received cash explicitly applied against invoiced amounts. "
        "Written only by the allocation phase -- never inferred from invoice existence.",
    )
    amount_refunded_from_advance = fields.Float(
        default=0.0, digits=(16, 2), readonly=True, copy=False, groups=ACCOUNTING_READ,
        help="Unapplied advance returned to the payer (e.g. cancelled prepaid service).",
    )
    amount_refunded_from_credit = fields.Float(
        default=0.0, digits=(16, 2), readonly=True, copy=False, groups=ACCOUNTING_READ,
        help="Over-collection returned to the payer after a credit note.",
    )
    amount_refunded = fields.Float(
        compute="_compute_outstanding", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ,
        help="amount_refunded_from_advance + amount_refunded_from_credit.",
    )
    net_invoiced_amount = fields.Float(
        compute="_compute_outstanding", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ,
        help="amount_invoiced - amount_credited. What the fiscal documents currently demand.",
    )
    receivable_balance = fields.Float(
        compute="_compute_outstanding", store=True, digits=(16, 2),
        compute_sudo=True, groups=ACCOUNTING_READ,
        help="SIGNED: net_invoiced_amount - amount_applied_to_invoice. "
        "Positive: the payer owes the hospital. Negative: over-applied.",
    )

    # Provenance / idempotency
    source_model = fields.Char(index=True)
    source_res_id = fields.Integer(index=True)
    source_line_id = fields.Integer()
    source_event = fields.Char()
    source_key = fields.Char(
        index=True,
        copy=False,
        help="Stable idempotency key. Re-emitting the same key updates the existing charge "
        "instead of creating a duplicate.",
    )

    # ------------------------------------------------------------------
    # Five independent lifecycles. None gates another.
    # ------------------------------------------------------------------
    charge_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("cancelled", "Cancelled"),
            ("reversed", "Reversed"),
        ],
        default="draft",
        required=True,
        index=True,
        copy=False,
    )
    authorization_state = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("pending", "Pending"),
            ("authorized", "Authorized"),
            ("rejected", "Rejected"),
            ("bypassed", "Bypassed (Emergency)"),
        ],
        default="not_required",
        required=True,
        copy=False,
        help="'Bypassed' records that care proceeded under emergency bypass without payer "
        "authorization. It is NOT authorization and must never be counted as such.",
    )
    delivery_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("in_progress", "In Progress"),
            ("partially_delivered", "Partially Delivered"),
            ("delivered", "Delivered"),
            ("not_delivered", "Not Delivered"),
        ],
        default="pending",
        required=True,
        copy=False,
    )
    invoice_state = fields.Selection(
        [
            ("not_invoiced", "Not Invoiced"),
            ("partially_invoiced", "Partially Invoiced"),
            ("invoiced", "Invoiced"),
            ("partially_credited", "Partially Credited"),
            ("credited", "Credited"),
        ],
        default="not_invoiced",
        required=True,
        index=True,
        copy=False,
        help="Fiscal-document lifecycle. Drives pricing immutability.",
    )
    payment_state = fields.Selection(
        [
            ("unpaid", "Not Funded"),
            ("partially_paid", "Partially Funded"),
            ("paid", "Funded"),
            ("refunded", "Funding Refunded"),
        ],
        string="Legacy Operational Funding State",
        default="unpaid",
        required=True,
        readonly=True,
        index=True,
        copy=False,
        help="Compatibility field retaining the historical technical values unpaid / "
        "partially_paid / paid / refunded. It describes operational funding only; "
        "it never proves accounting posting, reconciliation or settlement.",
    )
    operational_funding_state = fields.Selection(
        OPERATIONAL_FUNDING_STATES,
        string="Operational Funding",
        compute="_compute_financial_meaning_states",
        store=True,
        compute_sudo=True,
        index=True,
        help="Whether operational receipt allocations reserve enough cash against this "
        "charge. Funded does not mean invoiced, accounting-posted, paid or reconciled.",
    )
    accounting_receipt_state = fields.Selection(
        ACCOUNTING_RECEIPT_STATES,
        string="Receipt Accounting",
        compute="_compute_financial_meaning_states",
        store=True,
        compute_sudo=True,
        index=True,
        help="Whether accounting has been posted for confirmed operational receipt "
        "headers funding this charge. Task 32B-1 creates no such entries.",
    )
    settlement_state = fields.Selection(
        SETTLEMENT_STATES,
        string="Accounting Settlement",
        compute="_compute_financial_meaning_states",
        store=True,
        compute_sudo=True,
        index=True,
        help="Settlement is reserved for posted receivables reconciled in Accounting. "
        "Operational receipt allocation never changes this state.",
    )
    fiscal_state = fields.Selection(
        CHARGE_FISCAL_STATES,
        string="Fiscal Event",
        compute="_compute_financial_meaning_states",
        store=True,
        compute_sudo=True,
        index=True,
        help="Independent fiscal-event lifecycle. Task 32B-1 does not fiscalize charges "
        "or receipts, so existing charges remain Not Started.",
    )

    authorized_at = fields.Datetime(readonly=True, copy=False)
    service_started_at = fields.Datetime(readonly=True, copy=False)
    delivered_at = fields.Datetime(readonly=True, copy=False)
    invoiced_at = fields.Datetime(readonly=True, copy=False)
    paid_at = fields.Datetime(
        string="Operationally Funded At (legacy field)",
        readonly=True,
        copy=False,
        help="Historical field name retained for compatibility. This timestamp records "
        "operational funding, not accounting settlement.",
    )

    # Receipts reach the charge through ALLOCATIONS, not through the deprecated
    # receipt.charge_id column. One consolidated receipt paying two charges appears
    # once on each of them.
    receipt_ids = fields.Many2many(
        "hospital.charge.receipt", string="Payment Receipts",
        compute="_compute_receipts", compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
    )
    receipt_count = fields.Integer(
        compute="_compute_receipts", compute_sudo=True, groups=OPERATIONAL_MONEY_READ,
    )

    reversal_of_id = fields.Many2one("hospital.charge.line", readonly=True, copy=False)
    cancel_reason = fields.Text(copy=False)
    notes = fields.Text()
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "charge_source_key_unique",
            "unique(source_key)",
            "A charge line already exists for this source key.",
        ),
        (
            "charge_name_company_unique",
            "unique(name, company_id)",
            "A charge line with this reference already exists for this company.",
        ),
    ]

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    @api.depends(
        "billing_basis", "qty_requested", "qty_delivered", "delivery_state",
        "charge_state", "authorization_state",
    )
    def _compute_qty_billable(self):
        """Delivered-only invoice eligibility.

        Prepayment is operational funding, never delivery and never service
        revenue. Requested, cancelled, reversed, rejected and emergency-bypassed
        quantities are therefore not invoiceable until a valid delivered charge
        exists under the normal authorization rules.
        """
        for line in self:
            if (
                line.charge_state in FROZEN_CHARGE_STATES
                or line.authorization_state in ("rejected", "bypassed")
                or line.delivery_state == "not_delivered"
            ):
                line.qty_billable = 0.0
            else:
                line.qty_billable = max(0.0, line.qty_delivered)

    @api.depends("allocation_ids.receipt_id")
    def _compute_receipts(self):
        for line in self:
            receipts = line.allocation_ids.mapped("receipt_id")
            line.receipt_ids = receipts
            line.receipt_count = len(receipts)

    @api.depends("allocation_ids.amount", "allocation_ids.receipt_state")
    def _compute_amount_received(self):
        """Cash received = sum of CONFIRMED allocations. Nothing else may write it."""
        for line in self:
            line.amount_received = sum(
                line.allocation_ids.filtered(
                    lambda a: a.receipt_state == "confirmed"
                ).mapped("amount")
            )

    # ------------------------------------------------------------------
    # RESPONSIBILITY HELPERS -- THE ONLY PLACE THIS ARITHMETIC LIVES.
    #
    # Every consumer (the clearance engine, the receipt cap, the payment
    # wizard, the API serializer) calls these rather than re-deriving a
    # residual. A split re-implemented in four places is a split that will
    # disagree with itself in four places.
    # ------------------------------------------------------------------
    def _responsibility_mode(self):
        """'off' | 'shadow' | 'enforce' for this charge's company.

        Falls back to 'off' -- the safe default -- when the company cannot be
        resolved, so a half-built record can never accidentally enforce.
        """
        self.ensure_one()
        company = self.company_id or self.billing_account_id.company_id
        return company.sudo().payer_responsibility_mode or "off"

    def get_authorized_sponsor_responsibility(self):
        """The sponsor share that actually reduces the patient's cash demand."""
        return sum(self.sudo().mapped("amount_sponsor_authorized"))

    def get_patient_responsibility(self):
        """The residual the patient carries: charge value less authorized sponsor."""
        return sum(self.sudo().mapped("amount_patient_responsibility"))

    def get_patient_cash_due(self):
        """What the cashier may still collect from the patient, right now.

        Mode-aware through amount_due_for_clearance, so 'off' and 'shadow'
        return exactly the legacy figure.
        """
        return sum(self.sudo().mapped("amount_due_for_clearance"))

    def get_patient_payable_ceiling(self):
        """Upper bound on cash a receipt may allocate against these charges.

        Under 'enforce' the patient may not be charged for the sponsor's share,
        so the ceiling is the patient residual less what is already in hand.
        Under 'off'/'shadow' it is the legacy whole-charge figure.
        """
        total = 0.0
        for line in self.sudo():
            target = (
                line.amount_patient_responsibility
                if line._responsibility_mode() == "enforce"
                else line.amount_estimated
            )
            total += max(
                0.0, target - line.amount_received + line.amount_refunded
            )
        return total

    @api.depends("qty_billable", "qty_invoiced", "qty_credited")
    def _compute_qty_to_invoice(self):
        for line in self:
            line.net_invoiced_qty = line.qty_invoiced - line.qty_credited
            line.qty_to_invoice = max(0.0, line.qty_billable - line.net_invoiced_qty)

    @api.depends("qty_delivered", "net_invoiced_qty", "invoice_state")
    def _compute_variance(self):
        for line in self:
            variance = line.qty_delivered - line.net_invoiced_qty
            line.delivery_invoice_variance_qty = variance
            if line.invoice_state == "not_invoiced":
                line.adjustment_state = "none"
            elif variance > QTY_TOLERANCE:
                # More was delivered than the fiscal documents assert.
                line.adjustment_state = "additional_invoice_required"
            elif variance < -QTY_TOLERANCE:
                # More was invoiced than was actually delivered.
                line.adjustment_state = "credit_required"
            else:
                line.adjustment_state = "resolved"

    @api.depends("unit_price", "discount")
    def _compute_price_unit_net(self):
        for line in self:
            line.price_unit_net = line.unit_price * (1.0 - (line.discount or 0.0) / 100.0)

    def _estimate_tax(self, net_amount):
        """Indicative tax only. See the tax snapshot caveat above."""
        self.ensure_one()
        if self.tax_treatment != "standard":
            return 0.0
        return net_amount * (self.tax_rate or 0.0) / 100.0

    def _value(self, quantity):
        """Value a quantity at the net unit price, plus estimated tax."""
        self.ensure_one()
        net = quantity * self.price_unit_net
        return net + self._estimate_tax(net)

    @api.depends(
        "qty_requested", "qty_delivered", "qty_billable", "qty_to_invoice",
        "qty_invoiced", "qty_credited", "price_unit_net", "tax_treatment", "tax_rate",
    )
    def _compute_amounts(self):
        """Each monetary field is valued on its OWN quantity base.

        amount_estimated is deliberately based on qty_requested, so the historical
        line estimate survives cancellation and delivery shortfalls. Account totals
        exclude cancelled/reversed lines rather than zeroing the line's own estimate.
        """
        for line in self:
            net = line.price_unit_net

            requested_net = line.qty_requested * net
            line.price_subtotal = requested_net
            line.tax_amount_estimated = line._estimate_tax(requested_net)
            line.amount_estimated = requested_net + line.tax_amount_estimated

            line.amount_delivered = line._value(line.qty_delivered)
            line.amount_eligible = line._value(line.qty_billable)
            line.amount_to_invoice = line._value(line.qty_to_invoice)
            line.amount_invoiced = line._value(line.qty_invoiced)
            line.amount_credited = line._value(line.qty_credited)

    @api.depends(
        "amount_estimated",
        "responsibility_ids.amount",
        "responsibility_ids.state",
    )
    def _compute_responsibility(self):
        """Sponsor shares in, patient residual out.

        Cancelled rows contribute nothing -- they are history, not a claim.
        """
        for line in self:
            live = line.responsibility_ids.filtered(
                lambda r: r.state in ("draft", "authorized")
            )
            authorized = live.filtered(lambda r: r.state == "authorized")
            line.amount_sponsor_responsibility = sum(live.mapped("amount"))
            line.amount_sponsor_authorized = sum(authorized.mapped("amount"))
            line.amount_patient_responsibility = max(
                0.0, line.amount_estimated - line.amount_sponsor_authorized
            )
            if not live:
                line.responsibility_state = "self_pay"
            elif authorized:
                line.responsibility_state = "authorized"
            else:
                line.responsibility_state = "proposed"

    @api.depends(
        "amount_invoiced", "amount_credited", "amount_estimated", "amount_eligible", "billing_basis",
        "amount_received", "amount_applied_to_invoice",
        "amount_refunded_from_advance", "amount_refunded_from_credit",
        "amount_patient_responsibility",
        "billing_account_id.company_id.payer_responsibility_mode",
    )
    def _compute_outstanding(self):
        """Separates ADVANCE HELD (a deposit) from PATIENT CREDIT (a debt).

        The distinction is driven by explicit allocation, not by invoice_state:
        under partial invoicing a single line can simultaneously hold an unapplied
        advance and owe nothing, which a state check cannot express.
        """
        for line in self:
            line.amount_refunded = (
                line.amount_refunded_from_advance + line.amount_refunded_from_credit
            )

            line.net_invoiced_amount = line.amount_invoiced - line.amount_credited
            line.receivable_balance = line.net_invoiced_amount - line.amount_applied_to_invoice
            line.amount_outstanding = max(0.0, line.receivable_balance)

            # Cash in hand that has not been applied and has not been given back.
            line.amount_prepayment_held = max(
                0.0,
                line.amount_received
                - line.amount_applied_to_invoice
                - line.amount_refunded_from_advance,
            )

            # Only an OVER-APPLICATION against invoiced amounts is a real debt.
            line.amount_patient_credit = max(
                0.0, -line.receivable_balance - line.amount_refunded_from_credit
            )

            # Operational clearance: satisfied by cash received (less advance refunds),
            # regardless of whether an invoice exists yet. Pre-service clearance is
            # based on the frozen requested/estimated obligation, not on delivered or
            # invoice-eligible value: the patient is paying so service may commence.
            #
            # UNDER 'enforce' THE TARGET IS THE PATIENT'S SHARE, NOT THE WHOLE
            # CHARGE. Under 'off' and 'shadow' it stays amount_estimated, so the
            # figure the cashier collects is byte-identical to the legacy one --
            # shadow observes the split without ever charging against it.
            #
            # Note the two collapse whenever no sponsor share is authorized:
            # amount_patient_responsibility is then amount_estimated by
            # definition, so enforce changes nothing for a self-pay visit.
            if line.billing_basis == "prepaid":
                cash_in_hand = line.amount_received - line.amount_refunded_from_advance
                target = (
                    line.amount_patient_responsibility
                    if line._responsibility_mode() == "enforce"
                    else line.amount_estimated
                )
                line.amount_due_for_clearance = max(0.0, target - cash_in_hand)
            else:
                line.amount_due_for_clearance = 0.0

    @api.depends(
        "amount_received",
        "amount_refunded",
        "amount_eligible",
        "net_invoiced_amount",
        "amount_applied_to_invoice",
        "receivable_balance",
        "invoice_state",
        "allocation_ids.receipt_state",
        "allocation_ids.receipt_id.accounting_posted",
    )
    def _compute_financial_meaning_states(self):
        """Keep operational funding independent from accounting settlement."""
        for line in self:
            net_funding = max(0.0, line.amount_received - line.amount_refunded)
            target = max(0.0, line.amount_eligible)
            if net_funding <= AMOUNT_TOLERANCE:
                line.operational_funding_state = (
                    "refunded"
                    if line.amount_received > AMOUNT_TOLERANCE
                    and line.amount_refunded + AMOUNT_TOLERANCE >= line.amount_received
                    else "not_funded"
                )
            elif target > AMOUNT_TOLERANCE and net_funding + AMOUNT_TOLERANCE < target:
                line.operational_funding_state = "partially_funded"
            elif target > AMOUNT_TOLERANCE and net_funding > target + AMOUNT_TOLERANCE:
                line.operational_funding_state = "overfunded"
            else:
                line.operational_funding_state = "funded"

            receipts = line.allocation_ids.filtered(
                lambda allocation: allocation.receipt_state == "confirmed"
            ).mapped("receipt_id")
            if not receipts:
                line.accounting_receipt_state = "not_received"
            else:
                posted_count = len(receipts.filtered("accounting_posted"))
                if not posted_count:
                    line.accounting_receipt_state = "unposted"
                elif posted_count == len(receipts):
                    line.accounting_receipt_state = "posted"
                else:
                    line.accounting_receipt_state = "partially_posted"

            if line.invoice_state == "not_invoiced" or line.net_invoiced_amount <= AMOUNT_TOLERANCE:
                line.settlement_state = "not_applicable"
            elif line.receivable_balance <= AMOUNT_TOLERANCE:
                line.settlement_state = "settled"
            elif line.amount_applied_to_invoice > AMOUNT_TOLERANCE:
                line.settlement_state = "partially_settled"
            else:
                line.settlement_state = "unsettled"
            line.fiscal_state = "not_started"

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("qty_requested", "qty_delivered", "qty_invoiced", "qty_credited", "unit_price")
    def _check_non_negative(self):
        for line in self:
            if min(line.qty_requested, line.qty_delivered, line.qty_invoiced, line.qty_credited) < 0:
                raise ValidationError(f"Charge {line.name}: quantities cannot be negative.")
            if line.unit_price < 0:
                raise ValidationError(f"Charge {line.name}: unit price cannot be negative.")

    @api.constrains(
        "amount_received", "amount_applied_to_invoice",
        "amount_refunded_from_advance", "amount_refunded_from_credit",
    )
    def _check_allocation_non_negative(self):
        # sudo() for READING only: a constraint must evaluate the record's true
        # state even when the acting user cannot read every restricted field
        # (e.g. a receptionist recording a receipt cannot read the applied bucket).
        # This grants no write rights -- write() still enforces the group check.
        for line in self.sudo():
            buckets = (
                line.amount_received,
                line.amount_applied_to_invoice,
                line.amount_refunded_from_advance,
                line.amount_refunded_from_credit,
            )
            if min(buckets) < -AMOUNT_TOLERANCE:
                raise ValidationError(
                    f"Charge {line.name}: cash allocation amounts cannot be negative."
                )

    @api.constrains("amount_applied_to_invoice", "amount_received")
    def _check_applied_within_received(self):
        # UNCONDITIONAL. The former context-based accounting-adjustment bypass has
        # been REMOVED: a raw context dict must never be able to over-apply cash.
        # A controlled manager-only adjustment method will reintroduce this in the
        # accounting phase (see adjust_applied_amount, a stable placeholder).
        for line in self.sudo():  # read-only elevation; see _check_allocation_non_negative
            if line.amount_applied_to_invoice > line.amount_received + AMOUNT_TOLERANCE:
                raise ValidationError(
                    f"Charge {line.name}: cannot apply {line.amount_applied_to_invoice:.2f} to invoices "
                    f"when only {line.amount_received:.2f} has been received."
                )

    @api.constrains(
        "amount_refunded_from_advance", "amount_received", "amount_applied_to_invoice",
    )
    def _check_advance_refund(self):
        for line in self.sudo():  # read-only elevation; see _check_allocation_non_negative
            available = line.amount_received - line.amount_applied_to_invoice
            if line.amount_refunded_from_advance > available + AMOUNT_TOLERANCE:
                raise ValidationError(
                    f"Charge {line.name}: cannot refund {line.amount_refunded_from_advance:.2f} from "
                    f"the advance when only {max(0.0, available):.2f} is unapplied."
                )

    @api.constrains(
        "amount_refunded_from_credit", "amount_invoiced", "amount_credited",
        "amount_applied_to_invoice",
    )
    def _check_credit_refund(self):
        for line in self.sudo():  # read-only elevation; see _check_allocation_non_negative
            over_applied = max(
                0.0,
                line.amount_applied_to_invoice - (line.amount_invoiced - line.amount_credited),
            )
            if line.amount_refunded_from_credit > over_applied + AMOUNT_TOLERANCE:
                raise ValidationError(
                    f"Charge {line.name}: cannot refund {line.amount_refunded_from_credit:.2f} from "
                    f"patient credit when only {over_applied:.2f} was over-applied."
                )

    @api.constrains(
        "amount_estimated",
        # The inputs as well as the derived value: a constraint listed only on a
        # stored compute can be evaluated after the write that caused it, and
        # this one must refuse the write itself.
        "qty_requested", "unit_price", "discount", "tax_treatment", "tax_rate",
    )
    def _check_covers_authorized_responsibility(self):
        """A charge may not shrink below the sponsor share already authorized.

        WHY THIS FAILS CLOSED RATHER THAN ADJUSTING.
        amount_patient_responsibility is max(0, estimated - authorized), so a
        reprice from 1500 to 700 against an authorized 1000 does not error on
        its own -- it silently floors the patient at zero and leaves a sponsor
        carrying more than the charge is worth. Nothing downstream would notice:
        the residual is still non-negative and the totals still add up.

        Correcting it properly means deciding what the sponsor now owes and
        whether anything must be given back, which is the credit/adjustment
        accounting this phase deliberately does not build. So the write is
        refused, and the operator is pointed at the lifecycle that IS safe:
        cancel the share, reprice, record the corrected share.

        Only AUTHORIZED money constrains the charge. A draft is a proposal and
        must never block an ordinary pricing correction.
        """
        for line in self.sudo():  # read-only elevation; see _check_allocation_non_negative
            authorized = line.amount_sponsor_authorized
            if authorized > line.amount_estimated + AMOUNT_TOLERANCE:
                raise ValidationError(
                    "Charge %s is worth %.2f but %.2f is already authorized to a "
                    "sponsor. Reducing it would leave the sponsor carrying more "
                    "than the charge.\n\nCancel the sponsor responsibility "
                    "first, then reprice, then record the corrected share."
                    % (line.name, line.amount_estimated, authorized)
                )

    @api.constrains("qty_credited", "qty_invoiced")
    def _check_qty_credited(self):
        for line in self:
            if line.qty_credited > line.qty_invoiced + 1e-6:
                raise ValidationError(
                    f"Charge {line.name}: credited quantity ({line.qty_credited}) cannot exceed "
                    f"the invoiced quantity ({line.qty_invoiced})."
                )

    @api.constrains("discount")
    def _check_discount(self):
        for line in self:
            if not 0.0 <= line.discount <= 100.0:
                raise ValidationError(f"Charge {line.name}: discount must be between 0 and 100 percent.")

    @api.constrains("tax_rate", "tax_treatment")
    def _check_tax_rate(self):
        for line in self:
            if line.tax_rate < 0:
                raise ValidationError(f"Charge {line.name}: tax rate cannot be negative.")
            if line.tax_treatment != "standard" and line.tax_rate:
                raise ValidationError(
                    f"Charge {line.name}: a tax rate may only be set when the tax treatment is Standard Rated."
                )

    @api.constrains("service_id", "currency_id")
    def _check_currency(self):
        for line in self:
            service_currency = line.service_id.currency_id
            if service_currency and line.currency_id and service_currency != line.currency_id:
                raise ValidationError(
                    f"Charge {line.name}: service currency ({service_currency.name}) does not match "
                    f"the billing account currency ({line.currency_id.name})."
                )

    # ------------------------------------------------------------------
    # Onchange
    # ------------------------------------------------------------------
    @api.onchange("service_id")
    def _onchange_service_id(self):
        service = self.service_id
        if not service:
            return
        self.description = service.name
        self.unit_price = service.default_price
        self.tax_treatment = service.tax_treatment
        self.tax_rate = service.tax_rate if service.tax_treatment == "standard" else 0.0
        self.billing_basis = "prepaid" if service.prepayment_required else "delivery"
        if service.uom_id:
            self.uom_id = service.uom_id
        if service.coverage_auth_required:
            self.authorization_state = "pending"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("hospital.charge.line.sequence") or "New"
        return super().create(vals_list)

    def _assert_group(self, groups, action):
        """Real res.groups membership check. The only authorization primitive.

        sudo() deliberately still passes: server-side system code must be able to
        act. What must never pass is an ordinary RPC user who lacks the group,
        whatever context they supply.
        """
        if self.env.su:
            return
        if not any(self.env.user.has_group(g) for g in groups):
            raise AccessError(
                f"You are not authorized to {action}. "
                f"Required: one of {', '.join(g.split('.')[-1] for g in groups)}."
            )

    def write(self, vals):
        protected_financial_states = {
            "payment_state",
            "paid_at",
            "operational_funding_state",
            "accounting_receipt_state",
            "settlement_state",
            "fiscal_state",
            "invoice_state",
            "qty_invoiced",
            "qty_credited",
            "invoiced_at",
        }
        if not self.env.su and (protected_financial_states & set(vals)):
            raise AccessError(
                "Operational funding, receipt accounting, settlement and fiscal states "
                "are derived by controlled workflows and cannot be changed directly."
            )
        touched_allocation = set(vals) & ALLOCATION_FIELDS

        if touched_allocation:
            # (1) GROUP CHECK -- per field, on EVERY path. Context cannot grant this.
            for fname in sorted(touched_allocation):
                self._assert_group(
                    ALLOCATION_FIELD_GROUPS[fname], f"write '{fname}'"
                )
            # (3) REASON -- required for EVERY cash movement, not just refunds. Every
            # controlled method supplies one, so this is what makes an ordinary direct
            # write to amount_received impossible: money only moves through the
            # wizard / the controlled methods, which also create the receipt.
            reason = (self.env.context.get(ALLOCATION_REASON_CTX) or "").strip()
            if not reason:
                raise UserError(
                    "Cash allocation fields cannot be written directly: %s.\n\n"
                    "Use 'Record Payment / Advance' (or the controlled methods "
                    "record_payment_received / apply_advance_to_invoice / refund_advance / "
                    "refund_patient_credit), so the movement is justified, audited and "
                    "given an operational receipt."
                    % ", ".join(sorted(touched_allocation))
                )
            # Snapshot for the audit trail before the values change.
            before = {
                line.id: {f: line[f] for f in touched_allocation} for line in self
            }

        for line in self:
            if line.charge_state in FROZEN_CHARGE_STATES:
                # 'name' is an identifier, not financial substance. It stays writable so
                # a duplicate-reference repair can rename a cancelled charge; there is no
                # user-facing path to it (the field is readonly in every view).
                blocked = set(vals) - {"active", "notes", "name"} - ALLOCATION_FIELDS
                if blocked:
                    raise UserError(
                        f"Charge {line.name} is {line.charge_state} and cannot be modified. "
                        f"Blocked fields: {', '.join(sorted(blocked))}."
                    )
            if line.invoice_state in INVOICED_STATES:
                blocked = set(vals) & FROZEN_PRICING_FIELDS
                if blocked:
                    raise UserError(
                        f"Charge {line.name} is already {line.invoice_state}. "
                        f"Pricing terms cannot change: {', '.join(sorted(blocked))}. "
                        "Raise a new charge or a credit instead."
                    )
                # A prepaid service must still be able to record delivery after
                # invoicing -- but never by SILENT rewriting. Any path that changes
                # a post-invoice quantity must be authorized and must state why.
                touched_qty = set(vals) & POST_INVOICE_PROTECTED_QTY
                if touched_qty:
                    line._assert_group(
                        DELIVERY_GROUPS, "correct delivery on an invoiced charge"
                    )
                    if not (self.env.context.get(DELIVERY_REASON_CTX) or "").strip():
                        raise UserError(
                            f"Charge {line.name} is already {line.invoice_state}. "
                            f"Quantities cannot be edited without a documented reason: "
                            f"{', '.join(sorted(touched_qty))}. Use Record Delivery."
                        )

        result = super().write(vals)
        # (2) CEILINGS were enforced by @api.constrains during super().write().

        # (4) AUDIT -- written here, not in the callers, so that no present or future
        # method (or forged direct write) can mutate cash without leaving a record.
        if touched_allocation:
            reason = self.env.context.get(ALLOCATION_REASON_CTX) or ""
            reference = self.env.context.get(ALLOCATION_REFERENCE_CTX) or ""
            for line in self:
                deltas = []
                for fname in sorted(touched_allocation):
                    old = before[line.id][fname]
                    new = line[fname]
                    if abs(new - old) >= AMOUNT_TOLERANCE:
                        deltas.append(f"{fname} {old:.2f} -> {new:.2f}")
                if not deltas:
                    continue
                line.env["hospital.audit.log"].create_log(
                    patient_id=line.patient_id.id,
                    model_name=line._name,
                    record_id=line.id,
                    action_type="update",
                    description=(
                        f"Cash allocation on charge {line.name} by {self.env.user.display_name}: "
                        + "; ".join(deltas)
                        + (f". Reference: {reference}" if reference else "")
                        + (f". Reason: {reason}" if reason else "")
                    ),
                    old_value=str({f: before[line.id][f] for f in sorted(touched_allocation)}),
                    new_value=str({f: line[f] for f in sorted(touched_allocation)}),
                )
        return result

    def record_delivery(self, qty_delivered, delivery_state=None, reason=None):
        """The ONLY authorized path for changing delivery after invoicing.

        Clinical modules and the billing engine call this instead of writing
        qty_delivered directly. The change is audited, and any resulting
        delivery/invoice variance re-derives adjustment_state so the gap is
        surfaced rather than silently absorbed.
        """
        self.ensure_one()
        if self.charge_state in FROZEN_CHARGE_STATES:
            raise UserError(f"Charge {self.name} is {self.charge_state} and cannot record delivery.")
        if qty_delivered < 0:
            raise ValidationError(f"Charge {self.name}: delivered quantity cannot be negative.")

        post_invoice = self.invoice_state in INVOICED_STATES
        if post_invoice and not (reason or "").strip():
            raise UserError(
                f"Charge {self.name} is already {self.invoice_state}. A post-invoice delivery "
                "correction requires a documented reason."
            )

        old_qty = self.qty_delivered
        vals = {"qty_delivered": qty_delivered}
        if delivery_state:
            vals["delivery_state"] = delivery_state
        elif qty_delivered <= 0:
            vals["delivery_state"] = "not_delivered"
        elif qty_delivered < self.qty_requested:
            vals["delivery_state"] = "partially_delivered"
        else:
            vals["delivery_state"] = "delivered"
        if qty_delivered > 0 and not self.delivered_at:
            vals["delivered_at"] = fields.Datetime.now()

        self.with_context(**{DELIVERY_REASON_CTX: reason or "routine delivery"}).write(vals)

        self.env["hospital.audit.log"].create_log(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type="update",
            description=(
                f"Delivery recorded on charge {self.name}"
                + (f" after invoicing ({self.invoice_state})" if post_invoice else "")
                + (f". Reason: {reason}" if reason else ".")
                + f" Variance now {self.delivery_invoice_variance_qty:+.3f}"
                f" -> {self.adjustment_state}."
            ),
            old_value=str(old_qty),
            new_value=str(qty_delivered),
        )
        return True

    def unlink(self):
        for line in self:
            if line.invoice_state != "not_invoiced" or line.charge_state == "active":
                raise UserError(
                    f"Charge {line.name} cannot be deleted once it is active or invoiced. Cancel it instead."
                )
        return super().unlink()

    # ------------------------------------------------------------------
    # Controlled cash allocation (Phase 1 scaffolding).
    #
    # These are CONVENIENCE wrappers, not the security boundary. They give callers
    # friendly ceiling errors and carry the reason/reference. Authorization,
    # ceilings and the audit log are all enforced inside write(), so a caller who
    # bypasses these methods -- including one forging the context over RPC -- gains
    # nothing: they still face the group check, the constraints, the reason
    # requirement, and the audit entry.
    #
    # They deliberately do NOT create account.payment or account.move; the
    # accounting phase will wrap these, not replace them.
    # ------------------------------------------------------------------
    def _allocation_context(self, reason=None, reference=None):
        ctx = {}
        if reason:
            ctx[ALLOCATION_REASON_CTX] = reason
        if reference:
            ctx[ALLOCATION_REFERENCE_CTX] = reference
        return ctx

    def record_payment_received(self, amount, reference=None, payment_method="cash",
                                note=None):
        """Single-charge cash intake.

        Kept as a stable API, but it no longer writes amount_received (which is now
        derived): it creates a one-allocation payment HEADER, exactly like the wizard.
        There is therefore only one way money can enter -- a receipt allocation.

        Returns the receipt.
        """
        self.ensure_one()
        if amount <= 0:
            raise UserError(f"Charge {self.name}: the amount received must be positive.")
        if self.charge_state != "active":
            raise UserError(
                f"Charge {self.name} is {self.charge_state}. Payment can only be "
                "recorded against an ACTIVE charge."
            )
        # Authorization lives on the allocation model; this raises for a clinical user.
        self.env["hospital.charge.receipt.allocation"]._assert_intake_group(
            "record a payment"
        )

        receipt = self.env["hospital.charge.receipt"].sudo().create({
            "payment_method": payment_method,
            "payment_reference": reference,
            "received_at": fields.Datetime.now(),
            "received_by_id": self.env.user.id,
            "note": note,
            "state": "draft",
            "intake_token": uuid.uuid4().hex,
        })
        self.env["hospital.charge.receipt.allocation"].sudo().create({
            "receipt_id": receipt.id,
            "charge_line_id": self.id,
            "amount": amount,
        })
        audit = self.env["hospital.audit.log"].sudo().create_log(
            patient_id=self.patient_id.id,
            model_name=receipt._name,
            record_id=receipt.id,
            action_type="create",
            description=(
                "Payment receipt %s: %.2f received (%s%s) by %s, allocated to charge %s."
                % (receipt.name, amount, payment_method,
                   " ref %s" % reference if reference else "",
                   self.env.user.display_name, self.name)
            ),
        )
        # Confirm + stamp audit in one write, while still draft.
        receipt.sudo().write({"state": "confirmed", "audit_log_id": audit.id})
        self.sudo()._sync_payment_state()
        return receipt

    def apply_advance_to_invoice(self, amount, invoice_reference=None):
        """Explicitly apply held advance against invoiced amounts.

        Never called automatically merely because an invoice exists -- the
        allocation phase decides what to apply and when.
        """
        self.ensure_one()
        if amount <= 0:
            raise UserError(f"Charge {self.name}: the amount applied must be positive.")
        available = self.amount_prepayment_held
        if amount > available + AMOUNT_TOLERANCE:
            raise UserError(
                f"Charge {self.name}: cannot apply {amount:.2f}; only {available:.2f} "
                "is held as unapplied advance."
            )
        return self.with_context(
            **self._allocation_context(reason="Advance applied", reference=invoice_reference)
        ).write({"amount_applied_to_invoice": self.amount_applied_to_invoice + amount})

    def refund_advance(self, amount, reason):
        """Return unapplied advance (e.g. the prepaid service was cancelled)."""
        self.ensure_one()
        if amount <= 0:
            raise UserError(f"Charge {self.name}: the refund amount must be positive.")
        if not (reason or "").strip():
            raise UserError(f"Charge {self.name}: an advance refund requires a documented reason.")
        available = self.amount_prepayment_held
        if amount > available + AMOUNT_TOLERANCE:
            raise UserError(
                f"Charge {self.name}: cannot refund {amount:.2f} from the advance; "
                f"only {available:.2f} is held."
            )
        return self.with_context(**self._allocation_context(reason=reason)).write(
            {"amount_refunded_from_advance": self.amount_refunded_from_advance + amount}
        )

    def refund_patient_credit(self, amount, reason):
        """Return an over-collection that arose after a credit note."""
        self.ensure_one()
        if amount <= 0:
            raise UserError(f"Charge {self.name}: the refund amount must be positive.")
        if not (reason or "").strip():
            raise UserError(f"Charge {self.name}: a credit refund requires a documented reason.")
        available = self.amount_patient_credit
        if amount > available + AMOUNT_TOLERANCE:
            raise UserError(
                f"Charge {self.name}: cannot refund {amount:.2f} of patient credit; "
                f"only {available:.2f} is owed."
            )
        return self.with_context(**self._allocation_context(reason=reason)).write(
            {"amount_refunded_from_credit": self.amount_refunded_from_credit + amount}
        )

    def adjust_applied_amount(self, amount, reason):
        """Deliberately over-apply cash (applied > received).

        STABLE PLACEHOLDER. The former context-based bypass has been removed: no
        context dictionary can lift the applied<=received ceiling any more. When the
        accounting phase needs a write-on correction, it belongs here -- manager-only,
        reason-required, audited -- not in a raw context flag.
        """
        self.ensure_one()
        self._assert_group(ADJUSTMENT_GROUPS, "book an accounting over-application")
        if not (reason or "").strip():
            raise UserError("An accounting adjustment requires a documented reason.")
        raise UserError(
            "'adjust_applied_amount' is not implemented in Phase 1. Over-application "
            "requires the accounting layer's write-on/write-off policy, which is out of scope."
        )

    def transfer_advance(self, amount, target_billing_account, reason):
        """Move held advance to another billing account.

        STABLE PLACEHOLDER. Not implemented in Phase 1: a cross-account transfer
        needs a destination that can hold cash independently of a charge line
        (an account-level advance ledger), which this phase does not define.
        Implementing it as a pair of line writes would silently invent that policy.
        """
        self.ensure_one()
        self._assert_group(ACCOUNTING_GROUPS, "transfer an advance")
        raise UserError(
            "'transfer_advance' is not implemented in Phase 1. Cross-account advance "
            "transfer requires an account-level advance ledger, which is out of scope."
        )

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def _sync_payment_state(self):
        """Derive the cash lifecycle from what has actually been received.

        Called after an operational receipt. It never touches quantities, delivery
        or invoicing -- payment gates nothing.
        """
        for line in self:
            net_received = line.amount_received - line.amount_refunded
            target = line.amount_estimated
            if net_received <= AMOUNT_TOLERANCE:
                state = "unpaid"
            elif target > 0 and net_received + AMOUNT_TOLERANCE < target:
                state = "partially_paid"
            else:
                state = "paid"
            vals = {"payment_state": state}
            if state == "paid" and not line.paid_at:
                vals["paid_at"] = fields.Datetime.now()
            if line.payment_state != state or vals.get("paid_at"):
                line.sudo().write(vals)
        return True

    def action_record_payment(self):
        """Open the controlled payment-intake wizard. amount_received stays readonly."""
        self.ensure_one()
        self._assert_group(OPERATIONAL_INTAKE_GROUPS, "record a payment")
        if self.charge_state != "active":
            raise UserError(
                f"Charge {self.name} is {self.charge_state}. Payment can only be recorded "
                "against an ACTIVE charge."
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Record Payment / Advance",
            "res_model": "hospital.charge.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_source_charge_id": self.id},
        }

    def action_view_receipts(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Payment Receipts",
            "res_model": "hospital.charge.receipt",
            "view_mode": "list,form",
            "domain": [("charge_id", "=", self.id)],
        }

    def adjust_charge_pricing(self, new_price=None, discount=None, reason=None):
        """The ONLY legitimate way to reprice an existing charge.

        STABLE PLACEHOLDER -- deliberately not implemented. The commercial snapshot is
        frozen at creation precisely so that a workflow re-emission cannot rewrite it;
        the escape hatch must therefore be an explicit, authorized, audited act, not a
        quiet parameter on an idempotent upsert.

        The guards below are enforced NOW, so that when the body is implemented it can
        only ever run under the right conditions.
        """
        self.ensure_one()
        self._assert_group(ACCOUNTING_GROUPS, "reprice a charge")
        if not (reason or "").strip():
            raise UserError("Repricing a charge requires a documented reason.")
        if self.charge_state in FROZEN_CHARGE_STATES:
            raise UserError(
                f"Charge {self.name} is {self.charge_state} and cannot be repriced."
            )
        if self.invoice_state != "not_invoiced":
            raise UserError(
                f"Charge {self.name} is {self.invoice_state}. An invoiced or fiscalized "
                "charge is a historical fact -- issue a credit and a new charge instead."
            )
        if self.amount_received > AMOUNT_TOLERANCE:
            raise UserError(
                f"Charge {self.name} already holds {self.amount_received:.2f} in received "
                "cash/advance. Repricing it needs an explicit reconciliation of that money "
                "(refund the difference, or re-allocate it), which is not implemented."
            )
        raise UserError(
            "'adjust_charge_pricing' is not implemented in this phase. The commercial "
            "snapshot (service, price, discount, tax, billing basis) is immutable once a "
            "charge exists; controlled repricing will be delivered with the accounting "
            "phase, which owns the credit/re-invoice policy."
        )

    def action_activate(self):
        for line in self:
            if line.charge_state != "draft":
                raise UserError(f"Charge {line.name} is not in draft and cannot be activated.")
        return self.write({"charge_state": "active"})

    def action_cancel(self):
        for line in self:
            if line.invoice_state != "not_invoiced":
                raise UserError(
                    f"Charge {line.name} is {line.invoice_state} and cannot be cancelled. "
                    "Credit or reverse it instead."
                )
            if line.charge_state in FROZEN_CHARGE_STATES:
                raise UserError(f"Charge {line.name} is already {line.charge_state}.")
        return self.write({"charge_state": "cancelled", "delivery_state": "not_delivered"})

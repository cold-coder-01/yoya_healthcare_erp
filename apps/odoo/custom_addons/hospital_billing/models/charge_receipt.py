"""Consolidated operational payment intake.

ONE payment event (a receipt header) allocated across ONE OR MANY charges.

This is NOT accounting and NOT fiscalization. It records that cash was physically
taken at the desk so that clinical financial clearance can proceed.

SOURCE OF TRUTH
---------------
    allocation.amount  ->  receipt.amount          (header = sum of its allocations)
    allocation.amount  ->  charge.amount_received  (charge = sum of its CONFIRMED allocations)

Both are computed. Nothing increments a receipt total and a charge total through two
independent writes, so the two can never drift apart. `charge_id` on the header is
retained ONLY as a deprecated migration column and is never read by new logic.
     
Designed so the later accounting/fiscal bridge processes the HEADER once -- one fiscal
transaction, one accounting payment event, with its lines derived from the allocations
-- keyed on the header's idempotency identity, never per charge.
"""

import uuid

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError 

from .charge_line import (
    AMOUNT_TOLERANCE,
    G_ADMIN,
    G_MANAGER,
    OPERATIONAL_INTAKE_GROUPS,
)

# Imported, not restated. This module used to declare its own copy of the intake
# tuple under a different name, so widening one did not widen the other.
INTAKE_GROUPS = OPERATIONAL_INTAKE_GROUPS
OVERPAY_GROUPS = (G_MANAGER, G_ADMIN)

PAYMENT_METHODS = [
    ("cash", "Cash"),
    ("card", "Card"),
    ("mobile_money", "Mobile Money"),
    ("bank_transfer", "Bank Transfer"),
    ("fiscal_terminal", "Fiscal Terminal"),
    ("other", "Other"),
]

# Methods that must carry an external reference to be traceable.
REFERENCE_REQUIRED = ("card", "mobile_money", "bank_transfer", "fiscal_terminal", "other")


class HospitalChargeReceipt(models.Model):
    """The payment HEADER. One cash event, however many charges it settles."""

    _name = "hospital.charge.receipt"
    _description = "Hospital Operational Payment Receipt"
    _order = "received_at desc, id desc"

    name = fields.Char(required=True, readonly=True, copy=False, default="New")

    allocation_ids = fields.One2many(
        "hospital.charge.receipt.allocation", "receipt_id",
        string="Allocations", readonly=True,
    )
    allocation_count = fields.Integer(compute="_compute_amount", store=True)

    # THE money figure. Derived from the allocations; never written independently.
    amount = fields.Float(
        compute="_compute_amount", store=True, readonly=True, digits=(16, 2),
        string="Total Received",
    )

    # --- DEPRECATED --------------------------------------------------
    charge_id = fields.Many2one(
        "hospital.charge.line", readonly=True, ondelete="restrict", index=True,
        string="Charge (deprecated)",
        help="DEPRECATED. Retained only for migration compatibility with receipts "
        "created before allocations existed. New logic must use allocation_ids; this "
        "field is never the source of truth.",
    )

    # Header context, derived from the allocations (all of which must agree).
    billing_account_id = fields.Many2one(
        "hospital.billing.account", compute="_compute_context", store=True,
        readonly=True, index=True,
    )
    encounter_id = fields.Many2one(
        "hospital.encounter", compute="_compute_context", store=True,
        readonly=True, index=True,
    )
    patient_id = fields.Many2one(
        "hospital.patient", compute="_compute_context", store=True,
        readonly=True, index=True,
    )
    company_id = fields.Many2one(
        "res.company", compute="_compute_context", store=True, readonly=True, index=True,
    )
    currency_id = fields.Many2one(
        "res.currency", compute="_compute_context", store=True, readonly=True,
    )
    payer_id = fields.Many2one(
        related="billing_account_id.payer_id", store=True, readonly=True,
    )

    payment_method = fields.Selection(PAYMENT_METHODS, required=True, readonly=True)
    payment_reference = fields.Char(readonly=True)
    received_at = fields.Datetime(required=True, readonly=True, default=fields.Datetime.now)
    received_by_id = fields.Many2one(
        "res.users", required=True, readonly=True, default=lambda self: self.env.user,
    )
    note = fields.Text(readonly=True)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft", required=True, readonly=True, copy=False, index=True,
    )

    # --- Explicitly NOT accounting, NOT fiscal ------------------------
    accounting_posted = fields.Boolean(
        readonly=True, default=False,
        help="Set by the future accounting bridge, which processes this HEADER once.",
    )
    fiscalized = fields.Boolean(
        readonly=True, default=False,
        help="Set by the future fiscal bridge, which issues ONE fiscal receipt for this "
        "header with one item line per allocation.",
    )
    accounting_reference = fields.Char(readonly=True, copy=False)
    fiscal_reference = fields.Char(readonly=True, copy=False)

    audit_log_id = fields.Many2one("hospital.audit.log", readonly=True, copy=False)

    # Idempotency identity of the PAYMENT EVENT. The fiscal/accounting bridges must key
    # their retries on this, never on the individual charges.
    intake_token = fields.Char(readonly=True, copy=False, index=True)

    _sql_constraints = [
        ("receipt_intake_token_unique", "unique(intake_token)",
         "This payment has already been recorded."),
        ("receipt_name_company_unique", "unique(name, company_id)",
         "A receipt with this reference already exists for this company."),
    ]

    # ------------------------------------------------------------------
    # Derived money / context
    # ------------------------------------------------------------------
    @api.depends("allocation_ids.amount")
    def _compute_amount(self):
        for receipt in self:
            receipt.amount = sum(receipt.allocation_ids.mapped("amount"))
            receipt.allocation_count = len(receipt.allocation_ids)

    @api.depends("allocation_ids.charge_line_id")
    def _compute_context(self):
        for receipt in self:
            charges = receipt.allocation_ids.mapped("charge_line_id")
            if not charges:
                # Legacy header mid-migration: fall back to the deprecated column.
                charges = receipt.charge_id
            receipt.billing_account_id = charges[:1].billing_account_id
            receipt.encounter_id = charges[:1].encounter_id
            receipt.patient_id = charges[:1].patient_id
            receipt.company_id = charges[:1].company_id or self.env.company
            receipt.currency_id = charges[:1].currency_id

    @api.depends("name", "amount")
    def _compute_display_name(self):
        for receipt in self:
            receipt.display_name = "%s (%.2f)" % (receipt.name or "New", receipt.amount)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains("payment_method", "payment_reference")
    def _check_reference(self):
        for receipt in self:
            if receipt.payment_method in REFERENCE_REQUIRED and not (
                receipt.payment_reference or ""
            ).strip():
                raise ValidationError(
                    "A %s payment requires an external reference."
                    % dict(PAYMENT_METHODS)[receipt.payment_method]
                )

    @api.constrains("state", "allocation_ids")
    def _check_confirmed_has_allocations(self):
        for receipt in self:
            if receipt.state == "confirmed" and not receipt.allocation_ids:
                raise ValidationError(
                    "Receipt %s cannot be confirmed with no allocations: a payment must "
                    "say which charges it settles." % receipt.name
                )

    @api.constrains("allocation_ids")
    def _check_allocations_agree(self):
        """One payment event belongs to ONE patient / encounter / account / currency."""
        for receipt in self:
            charges = receipt.allocation_ids.mapped("charge_line_id")
            if len(charges) < 2:
                continue
            for fname, label in (
                ("patient_id", "patient"),
                ("encounter_id", "encounter"),
                ("billing_account_id", "billing account"),
                ("company_id", "company"),
                ("currency_id", "currency"),
            ):
                if len(set(charges.mapped(lambda c: c[fname].id))) > 1:
                    raise ValidationError(
                        "Receipt %s allocates across charges from more than one %s. "
                        "A single payment event cannot span %ss."
                        % (receipt.name, label, label)
                    )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "hospital.charge.receipt.sequence"
                ) or "New"
        return super().create(vals_list)

    def write(self, vals):
        accounting_bridge_fields = {
            "accounting_posted",
            "accounting_reference",
            "accounting_move_id",
            "accounting_posted_at",
            "accounting_posted_by_id",
        }
        if not self.env.su and accounting_bridge_fields & set(vals):
            raise AccessError(
                "Receipt accounting state is controlled by the future accounting "
                "bridge and cannot be changed directly."
            )
        # A confirmed receipt is a record of money already taken. Only the downstream
        # bridge flags may change afterwards. ('name' stays writable purely so a
        # duplicate-reference repair can renumber it; no user-facing path exists.)
        bridge_fields = {
            "accounting_posted", "fiscalized",
            "accounting_reference", "fiscal_reference", "state", "name",
            "accounting_move_id", "accounting_posted_at", "accounting_posted_by_id",
            "payment_origin", "payment_provider", "external_device_identity",
            "external_transaction_reference", "fiscal_receipt_number",            "advance_application_ids",
            "bank_reconciliation_state", "expected_bank_journal_id",
            "bank_transaction_reference", "bank_transaction_date",
            "bank_clearing_move_id", "bank_clearing_move_line_id",
            "matched_statement_line_id", "bank_reconciled_move_id",
            "bank_reconciled_at", "bank_reconciled_by",
            "bank_reconciliation_difference",
        }
        for receipt in self:
            blocked = set(vals) - bridge_fields
            if blocked and receipt.state != "draft":
                raise UserError(
                    "Receipt %s is a record of money already taken and cannot be edited: %s"
                    % (receipt.name, ", ".join(sorted(blocked)))
                )
        return super().write(vals)

    def unlink(self):
        raise UserError(
            "Payment receipts cannot be deleted. They record money that was physically "
            "received. Cancel the receipt instead."
        )

    def action_view_allocations(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Allocations",
            "res_model": "hospital.charge.receipt.allocation",
            "view_mode": "list,form",
            "domain": [("receipt_id", "=", self.id)],
        }


class HospitalChargeReceiptAllocation(models.Model):
    """How one payment event was split across the charges it settles."""

    _name = "hospital.charge.receipt.allocation"
    _description = "Hospital Payment Receipt Allocation"
    _order = "receipt_id, id"

    receipt_id = fields.Many2one(
        "hospital.charge.receipt", required=True, ondelete="cascade", index=True,
    )
    charge_line_id = fields.Many2one(
        "hospital.charge.line", required=True, ondelete="restrict", index=True,
    )
    amount = fields.Float(required=True, digits=(16, 2))

    # Consistency mirrors, stored so the constraints below are cheap and searchable.
    service_id = fields.Many2one(
        related="charge_line_id.service_id", store=True, readonly=True,
    )
    description = fields.Char(related="charge_line_id.description", readonly=True)
    patient_id = fields.Many2one(
        related="charge_line_id.patient_id", store=True, readonly=True, index=True,
    )
    encounter_id = fields.Many2one(
        related="charge_line_id.encounter_id", store=True, readonly=True, index=True,
    )
    billing_account_id = fields.Many2one(
        related="charge_line_id.billing_account_id", store=True, readonly=True, index=True,
    )
    company_id = fields.Many2one(
        related="charge_line_id.company_id", store=True, readonly=True, index=True,
    )
    currency_id = fields.Many2one(
        related="charge_line_id.currency_id", store=True, readonly=True,
    )
    receipt_state = fields.Selection(
        related="receipt_id.state", store=True, readonly=True, index=True,
    )

    _sql_constraints = [
        ("allocation_receipt_charge_unique", "unique(receipt_id, charge_line_id)",
         "This receipt already allocates to that charge. Adjust the existing allocation "
         "instead of adding a second one."),
        ("allocation_amount_positive", "CHECK(amount > 0)",
         "An allocation amount must be positive."),
    ]

    @api.constrains("receipt_id", "charge_line_id", "company_id", "currency_id")
    def _check_company_currency(self):
        for alloc in self:
            receipt = alloc.receipt_id
            if receipt.company_id and alloc.company_id != receipt.company_id:
                raise ValidationError(
                    "Allocation to %s belongs to company %s but receipt %s belongs to %s."
                    % (alloc.charge_line_id.name, alloc.company_id.name,
                       receipt.name, receipt.company_id.name)
                )
            if receipt.currency_id and alloc.currency_id != receipt.currency_id:
                raise ValidationError(
                    "Allocation to %s is in %s but receipt %s is in %s. A single payment "
                    "event cannot mix currencies."
                    % (alloc.charge_line_id.name, alloc.currency_id.name,
                       receipt.name, receipt.currency_id.name)
                )

    # ------------------------------------------------------------------
    # Immutability + authorization
    # ------------------------------------------------------------------
    def _assert_intake_group(self, action):
        if self.env.su:
            return
        if not any(self.env.user.has_group(g) for g in INTAKE_GROUPS):
            raise AccessError(
                "You are not authorized to %s. Required: one of %s."
                % (action, ", ".join(g.split(".")[-1] for g in INTAKE_GROUPS))
            )

    @api.constrains("amount", "charge_line_id", "receipt_state")
    def _check_within_patient_responsibility(self):
        """A patient may not be asked to fund the sponsor's share.

        ENFORCE MODE ONLY. Under 'off' and 'shadow' this returns immediately, so
        legacy receipt behaviour -- including deliberate over-collection that the
        existing advance/credit machinery already handles -- is untouched. That
        is what makes shadow safe to switch on mid-day.

        The ceiling is the patient RESIDUAL, not the charge value: with 1000 of
        1500 authorized to a sponsor, a receipt may allocate at most 500 to that
        charge however the cashier reached the number.
        """
        for alloc in self:
            charge = alloc.charge_line_id.sudo()
            if not charge or charge._responsibility_mode() != "enforce":
                continue
            if alloc.receipt_state == "cancelled":
                continue
            ceiling = charge.amount_patient_responsibility
            # Every allocation still in play, not just confirmed ones: this
            # constraint fires while the receipt is still draft, so
            # charge.amount_received does not yet include the row being written.
            allocated = sum(
                charge.allocation_ids.filtered(
                    lambda a: a.receipt_state != "cancelled"
                ).mapped("amount")
            )
            if allocated > ceiling + AMOUNT_TOLERANCE:
                raise ValidationError(
                    "Charge %s: %.2f has been allocated to the patient, but the "
                    "patient is responsible for only %.2f -- a sponsor carries "
                    "%.2f. The cashier collects the patient share, never the "
                    "sponsor's."
                    % (
                        charge.name,
                        allocated,
                        ceiling,
                        charge.amount_sponsor_authorized,
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        self._assert_intake_group("allocate a payment")
        # Serialize against a concurrent sponsor authorization or edit. Without
        # this, a cashier can read a patient residual, have an officer change
        # the split, and write a receipt computed from a state that no longer
        # exists. Same advisory key the responsibility workflow takes.
        Account = self.env["hospital.billing.account"]
        for vals in vals_list:
            charge = self.env["hospital.charge.line"].sudo().browse(
                vals.get("charge_line_id")
            )
            Account._lock_responsibility_scope(charge.billing_account_id.id)
        allocations = super().create(vals_list)
        for alloc in allocations:
            alloc._log_audit("create",
                             "Allocated %.2f of receipt %s to charge %s."
                             % (alloc.amount, alloc.receipt_id.name,
                                alloc.charge_line_id.name))
        return allocations

    def write(self, vals):
        for alloc in self:
            if alloc.receipt_id.state != "draft":
                raise UserError(
                    "Receipt %s is %s. Its allocations record money already taken and "
                    "cannot be edited." % (alloc.receipt_id.name, alloc.receipt_id.state)
                )
        self._assert_intake_group("amend a payment allocation")
        return super().write(vals)

    def unlink(self):
        for alloc in self:
            if alloc.receipt_id.state != "draft":
                raise UserError(
                    "Receipt %s is %s. Its allocations cannot be deleted."
                    % (alloc.receipt_id.name, alloc.receipt_id.state)
                )
        return super().unlink()

    def _log_audit(self, action_type, description):
        self.ensure_one()
        self.env["hospital.audit.log"].sudo().create_log(
            patient_id=self.patient_id.id,
            model_name=self._name,
            record_id=self.id,
            action_type=action_type,
            description=description,
        )


class HospitalChargePaymentWizardLine(models.TransientModel):
    _name = "hospital.charge.payment.wizard.line"
    _description = "Payment Allocation Line"
    _order = "id"

    wizard_id = fields.Many2one(
        "hospital.charge.payment.wizard", required=True, ondelete="cascade",
    )
    charge_line_id = fields.Many2one("hospital.charge.line", required=True, readonly=True)
    charge_name = fields.Char(related="charge_line_id.name", readonly=True, string="Charge")
    service_id = fields.Many2one(
        related="charge_line_id.service_id", readonly=True, string="Service",
    )
    description = fields.Char(related="charge_line_id.description", readonly=True)
    currency_id = fields.Many2one(related="charge_line_id.currency_id", readonly=True)

    amount_due = fields.Float(readonly=True, digits=(16, 2), string="Due")
    amount_available = fields.Float(readonly=True, digits=(16, 2))
    amount = fields.Float(required=True, digits=(16, 2), string="Allocation")


class HospitalChargePaymentWizard(models.TransientModel):
    """One payment event; the cashier sees and controls every allocation."""

    _name = "hospital.charge.payment.wizard"
    _description = "Record Payment / Advance"

    # Exactly one source is set by the launcher.
    source_charge_id = fields.Many2one("hospital.charge.line", readonly=True)
    source_billing_account_id = fields.Many2one("hospital.billing.account", readonly=True)
    source_lab_request_id = fields.Many2one("hospital.laboratory.request", readonly=True)
    source_radiology_request_id = fields.Many2one("hospital.radiology.request", readonly=True)

    line_ids = fields.One2many(
        "hospital.charge.payment.wizard.line", "wizard_id", string="Allocations",
    )

    patient_id = fields.Many2one("hospital.patient", readonly=True)
    encounter_id = fields.Many2one("hospital.encounter", readonly=True)
    billing_account_id = fields.Many2one("hospital.billing.account", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)

    amount_due_total = fields.Float(
        compute="_compute_totals", digits=(16, 2), string="Total Due",
    )
    amount_total = fields.Float(
        compute="_compute_totals", digits=(16, 2), string="Total Allocated",
    )

    payment_method = fields.Selection(PAYMENT_METHODS, required=True, default="cash")
    payment_reference = fields.Char()
    received_at = fields.Datetime(required=True, default=fields.Datetime.now)
    note = fields.Text()

    receipt_id = fields.Many2one("hospital.charge.receipt", readonly=True, copy=False)
    # One token per PAYMENT HEADER: a retry returns the same receipt.
    intake_token = fields.Char(readonly=True, default=lambda self: uuid.uuid4().hex)

    @api.depends("line_ids.amount", "line_ids.amount_due")
    def _compute_totals(self):
        for wiz in self:
            wiz.amount_total = sum(wiz.line_ids.mapped("amount"))
            wiz.amount_due_total = sum(wiz.line_ids.mapped("amount_due"))

    # ------------------------------------------------------------------
    # Building the wizard from its source
    # ------------------------------------------------------------------
    @api.model
    def _payable_charges(self, charges):
        """Active charges that can still legitimately receive PATIENT money.

        Under 'enforce' a fully sponsored charge has a zero patient ceiling and
        therefore drops out of the wizard entirely -- the cashier is never shown
        a line they must not collect against.
        """
        return charges.sudo().filtered(
            lambda c: c.charge_state == "active"
            and self._charge_available(c) > AMOUNT_TOLERANCE
        )

    @api.model
    def _charge_available(self, charge):
        """Cash this charge may still take from the PATIENT.

        Delegates to the charge's own helper so the sponsor/patient arithmetic
        lives in exactly one place. Under 'off' and 'shadow' the helper returns
        the legacy amount_estimated-based figure, so the wizard is unchanged.
        """
        return charge.sudo().get_patient_payable_ceiling()

    @api.model
    def _resolve_source_charges(self, charge_id=None, account_id=None, request_id=None, radiology_request_id=None):
        """The charges a payment from this source is ALLOWED to touch.

        This is the authoritative eligible set. action_confirm validates every
        submitted allocation line against it, so a forged or dropped charge_line_id
        from the browser/RPC cannot slip a foreign charge into the receipt.
        """
        Charge = self.env["hospital.charge.line"].sudo()
        if charge_id:
            return Charge.browse(charge_id)
        if account_id:
            return self.env["hospital.billing.account"].sudo().browse(
                account_id).charge_line_ids
        if request_id:
            return self.env["hospital.laboratory.request"].sudo().browse(
                request_id).charge_line_ids
        if radiology_request_id:
            return self.env["hospital.radiology.request"].sudo().browse(
                radiology_request_id).charge_line_ids
        return Charge

    @api.model
    def _charges_from_context(self):
        ctx = self.env.context
        return self._resolve_source_charges(
            charge_id=ctx.get("default_source_charge_id"),
            account_id=ctx.get("default_source_billing_account_id"),
            request_id=ctx.get("default_source_lab_request_id"),
            radiology_request_id=ctx.get("default_source_radiology_request_id"),
        )

    def _source_charges(self):
        """Eligible set from the STORED source fields -- available at confirm time,
        when the launcher's context defaults are no longer present."""
        self.ensure_one()
        return self._resolve_source_charges(
            charge_id=self.source_charge_id.id,
            account_id=self.source_billing_account_id.id,
            request_id=self.source_lab_request_id.id,
            radiology_request_id=self.source_radiology_request_id.id,
        )

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        charges = self._payable_charges(self._charges_from_context())
        if not charges:
            return values

        first = charges[0].sudo()
        values.setdefault("patient_id", first.patient_id.id)
        values.setdefault("encounter_id", first.encounter_id.id)
        values.setdefault("billing_account_id", first.billing_account_id.id)
        values.setdefault("currency_id", first.currency_id.id)

        lines = []
        for charge in charges:
            c = charge.sudo()
            available = self._charge_available(c)
            due = c.amount_due_for_clearance or available
            lines.append((0, 0, {
                "charge_line_id": c.id,
                "amount_due": due,
                "amount_available": available,
                # DEFAULT = full outstanding clearance. The cashier may lower any line;
                # the allocation is always visible, never applied by a hidden rule.
                "amount": due,
            }))
        values["line_ids"] = lines
        return values

    # ------------------------------------------------------------------
    # Confirm
    # ------------------------------------------------------------------
    def action_confirm(self):
        self.ensure_one()

        # THE authorization boundary for taking money. It belongs HERE, before
        # anything else happens, because this method is the only place a receipt
        # is actually created and confirmed -- and because everything below it
        # runs under sudo().
        #
        # Every other control on this path is weaker than it looks:
        #   - the `groups=` attributes on the launcher buttons are UI only; a
        #     client can call this method over RPC without ever seeing a button,
        #   - the ACL on this transient wizard was, until now, the only thing
        #     standing between an authenticated user and a confirmed receipt,
        #   - _assert_intake_group on the allocation model cannot fire here: the
        #     create below is a .sudo() call, and that guard returns early under
        #     env.su by design.
        #
        # So: assert real group membership as the CALLING user, before the first
        # sudo(). The sudo() calls that follow are still required -- the cashier
        # legitimately may not write receipt state directly -- but they are now
        # preceded by a check that a forged context, a hidden button or a raw
        # RPC call cannot get past.
        #
        # The launchers (charge line, billing account, lab request, radiology
        # request) deliberately do NOT repeat this check. They only open a
        # wizard; no money moves until confirm. One boundary, at the money.
        self.env["hospital.charge.receipt.allocation"]._assert_intake_group(
            "record a payment"
        )

        Receipt = self.env["hospital.charge.receipt"]

        # IDEMPOTENCY, at the PAYMENT-HEADER level. A retry of this same submission
        # returns the same receipt: no second header, no duplicated allocations, no
        # doubled amounts. The unique intake_token enforces it at DB level too.
        if self.receipt_id:
            return self._open_receipt(self.receipt_id)
        existing = Receipt.sudo().search([("intake_token", "=", self.intake_token)], limit=1)
        if existing:
            self.receipt_id = existing
            return self._open_receipt(existing)

        lines = self.line_ids.filtered(lambda l: l.amount > AMOUNT_TOLERANCE)
        if not lines:
            raise UserError("Allocate a positive amount to at least one charge.")

        if self.payment_method in REFERENCE_REQUIRED and not (
            self.payment_reference or ""
        ).strip():
            raise UserError(
                "A %s payment requires an external reference (transaction ID, slip "
                "number, terminal reference...)."
                % dict(PAYMENT_METHODS)[self.payment_method]
            )

        # -------- re-resolve and validate every line's charge identity server-side --
        # The browser/RPC must not be able to drop, forge or duplicate a charge_line_id.
        # We re-derive the eligible set from the wizard's OWN stored source and reject
        # anything that does not belong to it.
        eligible = self._source_charges()
        seen = set()
        for line in lines:
            charge = line.charge_line_id
            if not charge:
                raise UserError(
                    "A payment allocation line has no charge. Re-open the payment "
                    "wizard and try again; do not add rows by hand."
                )
            if charge.id in seen:
                raise UserError(
                    "Charge %s appears on more than one allocation line. Each charge may "
                    "be allocated at most once per payment." % charge.name
                )
            seen.add(charge.id)
            if charge.id not in eligible.ids:
                raise UserError(
                    "Charge %s is not part of this payment's source and cannot be paid "
                    "here. No receipt was created." % charge.name
                )

        charges = lines.mapped("charge_line_id").sudo()

        # Every charge in one payment event must share the same clinical/financial
        # context. Checked here AND constrained on the allocation model.
        for fname, label in (("patient_id", "patient"), ("encounter_id", "encounter"),
                             ("billing_account_id", "billing account"),
                             ("company_id", "company"), ("currency_id", "currency")):
            if len(set(charges.mapped(lambda c: c[fname].id))) > 1:
                raise UserError(
                    "This payment allocates across more than one %s. A single payment "
                    "event cannot span %ss." % (label, label)
                )

        is_manager = any(self.env.user.has_group(g) for g in OVERPAY_GROUPS)
        for line in lines:
            charge = line.charge_line_id.sudo()
            if charge.charge_state != "active":
                raise UserError(
                    "Charge %s is %s. Payment can only be allocated to an ACTIVE charge."
                    % (charge.name, charge.charge_state)
                )
            available = self._charge_available(charge)
            if line.amount > available + AMOUNT_TOLERANCE:
                if not is_manager:
                    raise UserError(
                        "You may allocate at most %.2f to charge %s (%.2f already "
                        "received). Overpayment requires a Billing Manager."
                        % (available, charge.name, charge.amount_received)
                    )
                if not (self.note or "").strip():
                    raise UserError(
                        "An overpayment must be justified: record a note explaining why "
                        "%.2f is being allocated to %s when only %.2f is due."
                        % (line.amount, charge.name, available)
                    )

        # ---------------- atomic: header + every allocation, or nothing -------------
        receipt = Receipt.sudo().create({
            "payment_method": self.payment_method,
            "payment_reference": self.payment_reference,
            "received_at": self.received_at,
            "received_by_id": self.env.user.id,
            "note": self.note,
            "state": "draft",
            "intake_token": self.intake_token,
        })
        self.env["hospital.charge.receipt.allocation"].sudo().create([
            {
                "receipt_id": receipt.id,
                "charge_line_id": line.charge_line_id.id,
                "amount": line.amount,
            }
            for line in lines
        ])

        audit = self.env["hospital.audit.log"].sudo().create_log(
            patient_id=receipt.patient_id.id,
            model_name=receipt._name,
            record_id=receipt.id,
            action_type="create",
            description=(
                "Payment receipt %s: %.2f received (%s%s) by %s, allocated across %d "
                "charge(s): %s."
                % (receipt.name, receipt.amount, receipt.payment_method,
                   " ref %s" % receipt.payment_reference if receipt.payment_reference else "",
                   self.env.user.display_name, len(lines),
                   ", ".join("%s=%.2f" % (l.charge_line_id.name, l.amount) for l in lines))
            ),
            new_value=str({l.charge_line_id.name: l.amount for l in lines}),
        )
        # Confirm and stamp the audit link in ONE write, while the receipt is still
        # draft -- the immutability guard would reject audit_log_id afterwards.
        receipt.sudo().write({"state": "confirmed", "audit_log_id": audit.id})

        # amount_received is COMPUTED from the confirmed allocations -- nothing to
        # increment. Only the cash lifecycle needs re-deriving.
        charges._sync_payment_state()

        self.receipt_id = receipt
        return self._open_receipt(receipt)

    def _open_receipt(self, receipt):
        return {
            "type": "ir.actions.act_window",
            "name": "Payment Receipt",
            "res_model": "hospital.charge.receipt",
            "view_mode": "form",
            "res_id": receipt.id,
            "target": "current",
        }

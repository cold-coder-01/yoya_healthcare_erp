import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# Same money-comparison tolerance used by hospital_billing.
AMOUNT_TOLERANCE = 0.005

QR_PREFIX = "YOYA-FISC:"


class HospitalFiscalTransaction(models.Model):
    """A controlled, immutable fiscal payment request generated from a
    hospital.patient.bill. This is the only object the fiscal terminal is
    ever allowed to see or act on."""

    _name = "hospital.fiscal.transaction"
    _description = "Fiscal POS Transaction"
    _inherit = ["mail.thread"]
    _order = "create_date desc, id desc"

    name = fields.Char(
        readonly=True,
        copy=False,
        default="New",
        index=True,
    )
    bill_id = fields.Many2one(
        "hospital.patient.bill",
        required=False,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    source_model = fields.Char(readonly=True, copy=False, index=True)
    source_record_id = fields.Integer(readonly=True, copy=False)
    source_reference = fields.Char(readonly=True, copy=False)
    source_patient_id = fields.Many2one("hospital.patient", readonly=True, copy=False)
    source_currency_id = fields.Many2one("res.currency", readonly=True, copy=False)

    patient_id = fields.Many2one(
        "hospital.patient",
        compute="_compute_source_context",
        store=True,
        readonly=True,
    )
    cashier_id = fields.Many2one(
        "res.users",
        default=lambda self: self.env.user,
        tracking=True,
    )
    device_id = fields.Many2one(
        "hospital.fiscal.device",
        tracking=True,
        help="Terminal that locked / paid this transaction.",
    )
    device_terminal_type = fields.Selection(
        related="device_id.terminal_type",
        string="Terminal Type",
        readonly=True,
    )
    device_location = fields.Char(
        related="device_id.location_name",
        string="Terminal Location",
        readonly=True,
    )
    device_last_seen = fields.Datetime(
        related="device_id.last_seen",
        string="Terminal Last Seen",
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        compute="_compute_source_context",
        store=True,
        readonly=True,
    )
    # Amount snapshot, frozen when the transaction becomes Ready.
    amount_untaxed = fields.Float(digits=(16, 2), readonly=True)
    amount_discount = fields.Float(digits=(16, 2), readonly=True)
    amount_total = fields.Float(digits=(16, 2), readonly=True)
    amount_paid_before = fields.Float(
        digits=(16, 2),
        readonly=True,
        help="Amount already paid on the bill when this request was prepared.",
    )
    amount_due = fields.Float(digits=(16, 2), readonly=True)
    bill_current_due = fields.Float(
        related="bill_id.amount_due",
        string="Current Bill Due",
        readonly=True,
        help="Live remaining due on the linked bill (0.00 once the bill is settled). "
        "Unlike the frozen snapshot amounts, this follows the bill in real time.",
    )
    amount_payable = fields.Float(
        digits=(16, 2),
        readonly=True,
        tracking=True,
        help="Exact amount the terminal must collect. Locked at Ready.",
    )
    barcode = fields.Char(readonly=True, copy=False, index=True)
    qr_payload = fields.Char(readonly=True, copy=False)
    external_reference = fields.Char(copy=False)
    external_receipt_no = fields.Char(readonly=True, copy=False, index=True)
    external_transaction_id = fields.Char(readonly=True, copy=False, index=True)
    fiscal_paid_at = fields.Datetime(readonly=True, copy=False)
    expires_at = fields.Datetime(readonly=True, copy=False)
    idempotency_key = fields.Char(readonly=True, copy=False, index=True)
    payment_id = fields.Many2one(
        "hospital.patient.bill.payment",
        readonly=True,
        copy=False,
        help="Official hospital payment record created from the terminal callback.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("ready", "Ready"),
            ("locked", "In Progress"),
            ("paid", "Paid"),
            ("failed", "Failed"),
            ("expired", "Expired"),
            ("cancelled", "Cancelled"),
            ("exception", "Exception / Manual Review"),
            ("reversed", "Reversed"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    line_ids = fields.One2many(
        "hospital.fiscal.transaction.line",
        "transaction_id",
        string="Snapshot Lines",
    )
    log_ids = fields.One2many(
        "hospital.fiscal.payment.log",
        "transaction_id",
        string="Logs",
    )
    notes = fields.Text()
    active = fields.Boolean(default=True)

    # ------------------------------------------------------------------
    # ORM overrides
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        sequence = self.env["ir.sequence"]
        for vals in vals_list:
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = (
                    sequence.next_by_code("hospital.fiscal.transaction") or "New"
                )
        transactions = super().create(vals_list)
        for transaction in transactions:
            transaction._create_audit_log(
                "create",
                f"Fiscal transaction {transaction.name} created for "
                f"{transaction.bill_id.name or transaction.source_reference or transaction.source_model or 'source'}.",
            )
        return transactions

    def unlink(self):
        if not self.env.user.has_group(
            "hospital_management.group_hospital_system_administrator"
        ):
            raise UserError(
                "Fiscal transactions cannot be deleted. Cancel or archive them instead."
            )
        return super().unlink()

    @api.depends("name", "bill_id")
    def _compute_display_name(self):
        for transaction in self:
            if transaction.bill_id:
                transaction.display_name = (
                    f"{transaction.name} ({transaction.bill_id.name})"
                )
            else:
                transaction.display_name = transaction.name or "New"


    @api.depends("bill_id.patient_id", "bill_id.currency_id", "source_patient_id", "source_currency_id")
    def _compute_source_context(self):
        for transaction in self:
            transaction.patient_id = transaction.bill_id.patient_id or transaction.source_patient_id
            transaction.currency_id = transaction.bill_id.currency_id or transaction.source_currency_id

    def _source_record(self):
        self.ensure_one()
        if not self.source_model or not self.source_record_id or self.source_model not in self.env:
            return self.env[self._name]
        return self.env[self.source_model].browse(self.source_record_id).exists()

    def _source_handler(self, method_name):
        source = self._source_record()
        if source and hasattr(source, method_name):
            handler = getattr(source, method_name)
            if callable(handler):
                return handler
        return None

    @api.constrains("bill_id", "source_model", "source_record_id", "source_patient_id", "source_currency_id")
    def _check_legacy_or_unified_target(self):
        for transaction in self:
            if transaction.bill_id:
                continue
            if not (transaction.source_model and transaction.source_record_id and transaction.source_patient_id and transaction.source_currency_id):
                raise ValidationError(
                    "Fiscal transaction must have either a legacy patient bill or a complete unified source target."
                )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @api.model
    def _validate_can_prepare(self, bill):
        """Guard everything that must be true before a fiscal payment
        request may be created / finalized for a bill."""
        if bill.state == "draft":
            raise UserError(
                "The bill must be confirmed before preparing a fiscal payment."
            )
        if bill.state == "cancelled":
            raise UserError(
                "Cancelled bills cannot receive fiscal payment requests."
            )
        if bill.state == "paid" or bill.amount_due <= AMOUNT_TOLERANCE:
            raise UserError(
                "This bill has no outstanding amount due. "
                "Fiscal payment requests are only allowed when amount due is greater than zero."
            )
        blocking = self.search(
            [
                ("bill_id", "=", bill.id),
                ("state", "in", ("ready", "locked")),
                ("active", "=", True),
            ],
            limit=1,
        )
        if blocking:
            raise UserError(
                f"Bill {bill.name} already has an active fiscal payment request "
                f"({blocking.name}, state: {blocking.state}). "
                "Cancel or complete it before preparing a new one."
            )

    def _compute_amounts_from_bill(self):
        """Snapshot the bill amounts. Foundation rule: the terminal collects
        the full remaining amount due (patient responsibility after any
        insurance payments already recorded on the bill)."""
        for transaction in self:
            bill = transaction.bill_id
            transaction.write(
                {
                    "amount_untaxed": bill.amount_untaxed,
                    "amount_discount": bill.discount_amount,
                    "amount_total": bill.amount_total,
                    "amount_paid_before": bill.amount_paid,
                    "amount_due": bill.amount_due,
                    "amount_payable": bill.amount_due,
                }
            )

    def _prepare_snapshot_lines(self):
        """Copy the live bill lines into immutable snapshot lines."""
        line_model = self.env["hospital.fiscal.transaction.line"]
        for transaction in self:
            transaction.line_ids.unlink()
            vals_list = []
            for line in transaction.bill_id.line_ids:
                gross = line.quantity * line.unit_price
                vals_list.append(
                    {
                        "transaction_id": transaction.id,
                        "bill_line_id": line.id,
                        "service_name": line.service_id.name or line.description,
                        "service_code": line.service_id.code or False,
                        "source_type": line.source_type or "other",
                        "description": line.description,
                        "quantity": line.quantity,
                        "unit_price": line.unit_price,
                        "discount": line.discount,
                        "subtotal": line.subtotal,
                        "tax_amount": 0.0,
                        "total": line.subtotal,
                        "source_model": line.source_model,
                        "source_record_id": line.source_record_id,
                        "department_name": False,
                    }
                )
            if vals_list:
                line_model.create(vals_list)

    def _validate_amount_callback(self, amount_paid):
        """Return True when the callback amount matches the locked payable
        amount within the project money tolerance."""
        self.ensure_one()
        try:
            amount = float(amount_paid)
        except (TypeError, ValueError):
            return False
        return abs(amount - self.amount_payable) <= AMOUNT_TOLERANCE

    def _get_expiry_minutes(self):
        param = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("hospital_fiscal_bridge.expiry_minutes", "120")
        )
        try:
            return max(1, int(param))
        except (TypeError, ValueError):
            return 120

    def _check_and_apply_expiry(self):
        """Auto-expire a ready/locked transaction whose window has passed.
        Returns True when the transaction expired."""
        self.ensure_one()
        if (
            self.state in ("ready", "locked")
            and self.expires_at
            and fields.Datetime.now() > self.expires_at
        ):
            self.write({"state": "expired"})
            self._create_audit_log(
                "state_change",
                f"Fiscal transaction {self.name} expired automatically.",
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Workflow actions
    # ------------------------------------------------------------------

    def action_confirm_ready(self):
        for transaction in self:
            if transaction.state != "draft":
                raise UserError("Only draft fiscal transactions can be made ready.")
            if transaction.bill_id:
                self._validate_can_prepare(transaction.bill_id)
                transaction._compute_amounts_from_bill()
                transaction._prepare_snapshot_lines()
            else:
                validate = transaction._source_handler("_fiscal_validate_can_prepare_unified")
                compute_amounts = transaction._source_handler("_fiscal_compute_amounts_unified")
                prepare_lines = transaction._source_handler("_fiscal_prepare_snapshot_lines_unified")
                if not (validate and compute_amounts and prepare_lines):
                    raise UserError("Fiscal transaction requires either a legacy bill or a unified source that implements fiscal preparation.")
                validate(transaction)
                compute_amounts(transaction)
                prepare_lines(transaction)
            expiry = fields.Datetime.now() + timedelta(
                minutes=transaction._get_expiry_minutes()
            )
            transaction.write(
                {
                    "state": "ready",
                    "barcode": transaction.name,
                    "qr_payload": f"{QR_PREFIX}{transaction.name}",
                    "expires_at": expiry,
                }
            )
            transaction._create_audit_log(
                "state_change",
                f"Fiscal transaction {transaction.name} moved to Ready for "
                f"fiscal payment. Payable: {transaction.amount_payable:.2f}",
            )

    def action_lock_for_terminal(self, device):
        """Terminal looked the transaction up; payment is now in progress."""
        for transaction in self:
            if transaction.state == "locked" and transaction.device_id == device:
                continue  # idempotent re-lookup by the same terminal
            if transaction.state != "ready":
                raise ValidationError(
                    f"Transaction {transaction.name} is not available for payment "
                    f"(state: {transaction.state})."
                )
            transaction.write({"state": "locked", "device_id": device.id})
            transaction._create_audit_log(
                "state_change",
                f"Fiscal transaction {transaction.name} locked by terminal "
                f"{device.device_code}.",
            )

    def action_mark_failed(self, reason=None):
        for transaction in self:
            if transaction.state == "paid":
                raise UserError("A paid fiscal transaction cannot be marked failed.")
            if transaction.state not in ("ready", "locked"):
                continue  # already terminal-final; keep idempotent
            transaction.write(
                {
                    "state": "failed",
                    "notes": (
                        (transaction.notes or "")
                        + f"\nFailure reason: {reason or 'not provided'}"
                    ).strip(),
                }
            )
            transaction._create_audit_log(
                "state_change",
                f"Fiscal transaction {transaction.name} failed: "
                f"{reason or 'no reason provided'}.",
            )

    def action_cancel(self):
        for transaction in self:
            if transaction.state in ("paid", "reversed"):
                raise UserError(
                    "Paid or reversed fiscal transactions cannot be cancelled."
                )
            transaction.write({"state": "cancelled"})
            transaction._create_audit_log(
                "state_change", f"Fiscal transaction {transaction.name} cancelled."
            )

    def action_expire(self):
        for transaction in self:
            if transaction.state not in ("ready", "locked"):
                raise UserError(
                    "Only ready or in-progress fiscal transactions can be expired."
                )
            transaction.write({"state": "expired"})
            transaction._create_audit_log(
                "state_change",
                f"Fiscal transaction {transaction.name} expired manually.",
            )

    def action_reset_to_ready(self):
        if not (
            self.env.user.has_group("hospital_management.group_hospital_manager")
            or self.env.user.has_group(
                "hospital_management.group_hospital_system_administrator"
            )
            or self.env.user.has_group(
                "hospital_management.group_hospital_accountant"
            )
        ):
            raise UserError(
                "Only managers, accountants or system administrators can reset "
                "fiscal transactions."
            )
        for transaction in self:
            if transaction.state not in ("failed", "expired", "cancelled"):
                raise UserError(
                    "Only failed, expired or cancelled fiscal transactions can be "
                    "reset to ready."
                )
            if transaction.bill_id:
                self._validate_can_prepare(transaction.bill_id)
                # Re-snapshot: the bill may have changed since the first attempt.
                transaction._compute_amounts_from_bill()
                transaction._prepare_snapshot_lines()
            else:
                validate = transaction._source_handler("_fiscal_validate_can_prepare_unified")
                compute_amounts = transaction._source_handler("_fiscal_compute_amounts_unified")
                prepare_lines = transaction._source_handler("_fiscal_prepare_snapshot_lines_unified")
                if not (validate and compute_amounts and prepare_lines):
                    raise UserError("Fiscal transaction requires either a legacy bill or a unified source that implements fiscal preparation.")
                validate(transaction)
                compute_amounts(transaction)
                prepare_lines(transaction)
            expiry = fields.Datetime.now() + timedelta(
                minutes=transaction._get_expiry_minutes()
            )
            transaction.write({"state": "ready", "expires_at": expiry})
            transaction._create_audit_log(
                "state_change",
                f"Fiscal transaction {transaction.name} reset to ready by "
                f"{self.env.user.name}.",
            )

    # ------------------------------------------------------------------
    # Navigation helpers (UI only, no workflow impact)
    # ------------------------------------------------------------------

    def action_view_bill(self):
        self.ensure_one()
        if not self.bill_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": "Patient Bill",
            "res_model": "hospital.patient.bill",
            "res_id": self.bill_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_view_logs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Fiscal Logs",
            "res_model": "hospital.fiscal.payment.log",
            "view_mode": "list,form",
            "domain": [("transaction_id", "=", self.id)],
            "context": {"search_default_group_by_event": 1},
        }

    # ------------------------------------------------------------------
    # Terminal success callback
    # ------------------------------------------------------------------

    @api.model
    def _map_payment_method(self, terminal_method):
        mapping = {
            "cash": "cash",
            "card": "card",
            "pos_card": "card",
            "bank": "bank_transfer",
            "bank_transfer": "bank_transfer",
            "transfer": "bank_transfer",
            "mobile": "mobile_money",
            "mobile_money": "mobile_money",
            "telebirr": "mobile_money",
            "cbe_birr": "mobile_money",
        }
        return mapping.get(str(terminal_method or "").strip().lower(), "other")

    def _find_duplicate_external_reference(self, external_transaction_id, external_receipt_no):
        """Look for another transaction already paid with the same external
        fiscal identifiers (fiscal receipt reuse = fraud/error signal)."""
        self.ensure_one()
        domain = [
            ("id", "!=", self.id),
            ("state", "in", ("paid", "reversed")),
        ]
        ref_domain = []
        if external_transaction_id:
            ref_domain.append(("external_transaction_id", "=", external_transaction_id))
        if external_receipt_no:
            ref_domain.append(("external_receipt_no", "=", external_receipt_no))
        if not ref_domain:
            return self.browse()
        if len(ref_domain) == 2:
            domain += ["|"] + ref_domain
        else:
            domain += ref_domain
        return self.search(domain, limit=1)

    def action_mark_paid_from_terminal(self, payload):
        """Process a terminal success callback.

        Returns a dict:
            status: 'accepted' | 'duplicate' | 'exception' | 'rejected'
            message: human-readable detail
            payment: hospital.patient.bill.payment record (accepted/duplicate)
        """
        self.ensure_one()
        payload = payload or {}
        amount_paid = payload.get("amount_paid")
        external_receipt_no = payload.get("external_receipt_no")
        external_transaction_id = payload.get("external_transaction_id")
        idempotency_key = payload.get("idempotency_key")

        # --- Idempotent replay: same fiscal identifiers on a paid transaction.
        if self.state == "paid":
            same_receipt = external_receipt_no and self.external_receipt_no == external_receipt_no
            same_ext_id = (
                external_transaction_id
                and self.external_transaction_id == external_transaction_id
            )
            same_idem = idempotency_key and self.idempotency_key == idempotency_key
            if same_receipt or same_ext_id or same_idem:
                self._create_audit_log(
                    "update",
                    f"Duplicate success callback ignored for {self.name} "
                    f"(receipt: {external_receipt_no}).",
                )
                return {
                    "status": "duplicate",
                    "message": "Transaction already paid. Duplicate callback ignored.",
                    "payment": self.payment_id,
                }
            # Paid, but with different fiscal identifiers: manual review.
            self._create_audit_log(
                "update",
                f"Success callback with different external references received "
                f"for already-paid transaction {self.name}. Rejected for review.",
            )
            return {
                "status": "rejected",
                "message": "Transaction already paid with different external references.",
                "payment": self.env["hospital.patient.bill.payment"],
            }

        if self.state not in ("ready", "locked"):
            return {
                "status": "rejected",
                "message": f"Transaction is not payable (state: {self.state}).",
                "payment": self.env["hospital.patient.bill.payment"],
            }

        # --- Fiscal receipt reuse across transactions.
        duplicate = self._find_duplicate_external_reference(
            external_transaction_id, external_receipt_no
        )
        if duplicate:
            self.write({"state": "exception"})
            self._create_audit_log(
                "state_change",
                f"External fiscal reference already used by {duplicate.name}. "
                f"Transaction {self.name} moved to exception.",
            )
            return {
                "status": "exception",
                "message": "External receipt/transaction id already used by another "
                "fiscal transaction. Moved to manual review.",
                "payment": self.env["hospital.patient.bill.payment"],
            }

        # --- Amount must match exactly (within money tolerance).
        if not self._validate_amount_callback(amount_paid):
            self.write({"state": "exception"})
            self._create_audit_log(
                "state_change",
                f"Amount mismatch on {self.name}: expected "
                f"{self.amount_payable:.2f}, terminal reported {amount_paid}. "
                "Moved to exception, no payment created.",
            )
            return {
                "status": "exception",
                "message": (
                    f"Amount mismatch: expected {self.amount_payable:.2f}. "
                    "Transaction moved to manual review. No payment was created."
                ),
                "payment": self.env["hospital.patient.bill.payment"],
            }

        paid_at = payload.get("paid_at_dt") or fields.Datetime.now()
        payment_reference = external_receipt_no or external_transaction_id or self.name
        if not self.bill_id:
            self.write(
                {
                    "state": "paid",
                    "external_receipt_no": external_receipt_no,
                    "external_transaction_id": external_transaction_id,
                    "external_reference": payload.get("external_reference") or external_receipt_no,
                    "idempotency_key": idempotency_key,
                    "fiscal_paid_at": paid_at,
                }
            )
            self._create_audit_log(
                "state_change",
                f"Fiscal transaction {self.name} paid for unified source "
                f"{self.source_reference or self.source_record_id}. External receipt: {external_receipt_no or '-'}."
            )
            return {
                "status": "accepted",
                "message": "Fiscal payment recorded for unified source.",
                "payment": self.env["hospital.patient.bill.payment"],
            }

        payment = self.env["hospital.patient.bill.payment"].create(
            {
                "bill_id": self.bill_id.id,
                "amount": self.amount_payable,
                "payment_method": self._map_payment_method(payload.get("payment_method")),
                "payment_reference": payment_reference,
                "payment_date": paid_at,
                "cashier_id": self.cashier_id.id or self.env.user.id,
                "notes": (
                    f"Fiscal payment via {self.device_id.name or 'fiscal terminal'} "
                    f"({self.device_id.device_code or '-'}). "
                    f"Fiscal transaction: {self.name}. "
                    f"External receipt: {external_receipt_no or '-'}. "
                    f"External transaction: {external_transaction_id or '-'}.")
            }
        )
        self.write(
            {
                "state": "paid",
                "payment_id": payment.id,
                "external_receipt_no": external_receipt_no,
                "external_transaction_id": external_transaction_id,
                "external_reference": payload.get("external_reference") or external_receipt_no,
                "idempotency_key": idempotency_key,
                "fiscal_paid_at": paid_at,
            }
        )
        self.bill_id._update_payment_state()
        self._create_audit_log(
            "state_change",
            f"Fiscal transaction {self.name} paid. Payment {payment.name} created "
            f"for {self.amount_payable:.2f}. External receipt: {external_receipt_no or '-'}."
        )
        return {"status": "accepted", "message": "Payment recorded.", "payment": payment}

    # ------------------------------------------------------------------
    # Hospital audit log bridge (same pattern as hospital_billing)
    # ------------------------------------------------------------------

    def _get_safe_audit_action_type(self, requested_action=None):
        """Return an action_type accepted by hospital.audit.log's Selection.

        The audit log only allows its own generic values (create, update,
        state_change, ...), so fiscal events must never be passed through
        raw. Falls back to state_change, then update, then whatever the
        selection offers first."""
        audit_log = self.env["hospital.audit.log"]
        field = audit_log._fields.get("action_type")
        valid_values = []
        if field and getattr(field, "selection", None):
            selection = field.selection
            if callable(selection):
                selection = selection(audit_log)
            valid_values = [item[0] for item in selection]
        for candidate in (requested_action, "state_change", "update"):
            if candidate in valid_values:
                return candidate
        return valid_values[0] if valid_values else False

    def _create_audit_log(self, action_type, description):
        """Best-effort bridge to the hospital audit log. Audit failures are
        logged as warnings and must never block the fiscal workflow."""
        try:
            audit_log = self.env["hospital.audit.log"]
        except KeyError:
            return
        try:
            safe_action_type = self._get_safe_audit_action_type(action_type)
            if not safe_action_type:
                _logger.warning(
                    "Fiscal bridge: no valid audit action_type available; "
                    "skipping audit log for %s (%s)",
                    self.name,
                    description,
                )
                return
            patient_id = self.patient_id.id if self.patient_id else False
            audit_log.with_context(audit_user_id=self.env.user.id).sudo().create_log(
                patient_id=patient_id,
                model_name=self._name,
                record_id=self.id,
                action_type=safe_action_type,
                description=description,
            )
        except Exception as exc:
            _logger.warning(
                "Fiscal bridge: failed to create audit log for %s (%s): %s",
                self.name,
                description,
                exc,
            )

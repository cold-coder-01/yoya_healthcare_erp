"""Shared billing service layer.

Every clinical module (pharmacy, laboratory, radiology, procedure, admission, ...)
must route charge creation through this engine rather than writing charge lines
directly. That keeps idempotency, authorization and state transitions in one place.

Phase 1 implements only account provisioning and idempotent charge upsert.
The remaining methods are declared with their final signatures and raise a
controlled error so callers can be written against the stable API today.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError

from .charge_line import ACCOUNTING_GROUPS, AMOUNT_TOLERANCE, G_RECEPTIONIST

FROZEN_CHARGE_STATES = ("cancelled", "reversed")
LOCKED_ENCOUNTER_STATES = ("closed", "cancelled")
QTY_TOL = 1e-6

# The COMMERCIAL SNAPSHOT. Captured once at charge creation and never rewritten by a
# re-emitted source event -- not even if the caller passes the value explicitly.
# Repricing is a deliberate, authorized, audited act (adjust_charge_pricing), not a
# side effect of a clinical workflow button being pressed twice.
# (currency_id and company_id are related/stored from the billing account and are not
# writable on the charge at all, so they are structurally immutable already.)
SNAPSHOT_FIELDS = {
    "service_id",
    "billing_basis",
    "unit_price",
    "discount",
    "tax_treatment",
    "tax_rate",
    "uom_id",
}

# Lifecycle state the charge has EARNED since it was created.
EARNED_FIELDS = {"authorization_state"}

# Where the charge came from. Never rewritten.
PROVENANCE_FIELDS = {
    "source_model", "source_res_id", "source_line_id", "source_event", "source_key",
}

# Recording a payer authorization is a front-desk/accounting act, not a clinical one.
AUTHORIZE_GROUPS = (G_RECEPTIONIST,) + ACCOUNTING_GROUPS


class HospitalBillingEngine(models.AbstractModel):
    _name = "hospital.billing.engine"
    _description = "Hospital Billing Engine"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @api.model
    def _not_implemented(self, method_name):
        raise UserError(
            f"'{method_name}' is not implemented in Phase 1 of the unified encounter billing "
            "architecture. It will be delivered in a later phase."
        )

    @api.model
    def _build_source_key(self, source_model, source_res_id, source_event, source_line_id=0):
        """Deterministic idempotency key for a source event."""
        return f"{source_model}:{source_res_id}:{source_line_id or 0}:{source_event}"

    # ------------------------------------------------------------------
    # Phase 1 — implemented
    # ------------------------------------------------------------------
    @api.model
    def get_or_create_billing_account(self, encounter):
        """Return the encounter's billing account, creating it if absent.

        Idempotent: the unique constraint on hospital.billing.account.encounter_id
        guarantees at most one account per encounter.
        """
        encounter.ensure_one()
        if encounter.state in LOCKED_ENCOUNTER_STATES:
            raise UserError(
                f"Encounter {encounter.name} is {encounter.state}; no billing account can be opened."
            )
        account = self.env["hospital.billing.account"].search(
            [("encounter_id", "=", encounter.id)], limit=1
        )
        if account:
            return account
        return self.env["hospital.billing.account"].create(
            {
                "encounter_id": encounter.id,
                "payer_type": encounter.payer_type,
                "payer_id": encounter.payer_id.id or False,
            }
        )

    @api.model
    def create_or_update_charge(
        self,
        encounter,
        source_model,
        source_res_id,
        source_event,
        description,
        source_line_id=0,
        service=None,
        qty_requested=1.0,
        unit_price=None,
        discount=0.0,
        uom=None,
        source_key=None,
        extra_vals=None,
    ):
        """Idempotently create or update a charge line for a source event.

        Re-calling with the same ``source_key`` updates the existing charge rather
        than creating a duplicate. Charges that are cancelled, reversed, or already
        invoiced are returned untouched.
        """
        encounter.ensure_one()
        if encounter.state in LOCKED_ENCOUNTER_STATES:
            raise UserError(
                f"Encounter {encounter.name} is {encounter.state} and cannot accept new charges."
            )

        account = self.get_or_create_billing_account(encounter)
        if account.state in ("closed", "cancelled"):
            raise UserError(
                f"Billing account {account.name} is {account.state} and cannot accept new charges."
            )

        key = source_key or self._build_source_key(
            source_model, source_res_id, source_event, source_line_id
        )
        charge_model = self.env["hospital.charge.line"]
        existing = charge_model.search([("source_key", "=", key)], limit=1)

        if unit_price is None:
            unit_price = service.default_price if service else 0.0

        vals = {
            "billing_account_id": account.id,
            "service_id": service.id if service else False,
            "description": description,
            "qty_requested": qty_requested,
            "unit_price": unit_price,
            "discount": discount,
            "uom_id": uom.id if uom else (service.uom_id.id if service and service.uom_id else False),
            "source_model": source_model,
            "source_res_id": source_res_id,
            "source_line_id": source_line_id or 0,
            "source_event": source_event,
            "source_key": key,
        }
        if service:
            # Snapshot the service configuration onto the charge. Later edits to the
            # service must never retroactively alter an existing charge.
            vals["tax_treatment"] = service.tax_treatment
            vals["tax_rate"] = service.tax_rate if service.tax_treatment == "standard" else 0.0
            vals["billing_basis"] = "prepaid" if service.prepayment_required else "delivery"
            if service.coverage_auth_required:
                vals["authorization_state"] = "pending"
        if extra_vals:
            vals.update(extra_vals)

        if not existing:
            # The snapshot is taken HERE, once, and never again.
            return charge_model.create(vals)

        if existing.charge_state in FROZEN_CHARGE_STATES:
            return existing
        if existing.invoice_state != "not_invoiced":
            # An invoiced charge is a historical fiscal fact. A later fulfilment
            # against the same source must raise a new charge, not mutate this one.
            return existing

        # ------------------------------------------------------------------
        # IDEMPOTENCY ON RE-EMIT -- COMPLETE SNAPSHOT IMMUTABILITY.
        #
        # A commercial snapshot is taken ONCE, at creation. Re-emitting a source
        # event must never reprice, reclassify or re-authorize an existing charge.
        #
        # This is what corrupted CHRG000063: action_start_consultation re-runs this
        # upsert as a self-heal, so a later edit to the service silently rewrote
        # billing_basis prepaid -> delivery on a charge the patient had ALREADY PAID.
        # A snapshot that can be rewritten is not a snapshot.
        #
        # Note this holds EVEN WHEN THE CALLER PASSES AN EXPLICIT unit_price/discount.
        # Passing an argument to an idempotent upsert is not authorization to rewrite
        # a commercial term: that requires adjust_charge_pricing(), which will demand
        # accountant/manager rights, a documented reason and an audit entry.
        # ------------------------------------------------------------------
        for frozen in SNAPSHOT_FIELDS | EARNED_FIELDS | PROVENANCE_FIELDS:
            vals.pop(frozen, None)

        existing.write(vals)
        return existing

    # ------------------------------------------------------------------
    # Phase 2A — operational lifecycle
    # ------------------------------------------------------------------
    @api.model
    def get_or_create_encounter(self, appointment):
        """Encounter for an appointment. Idempotent via unique(appointment_id).

        Never backfills history: only the caller's explicit event creates one.
        """
        appointment.ensure_one()
        Encounter = self.env["hospital.encounter"]
        existing = Encounter.search([("appointment_id", "=", appointment.id)], limit=1)
        if existing:
            return existing
        return Encounter.create({
            "patient_id": appointment.patient_id.id,
            "appointment_id": appointment.id,
            "encounter_type": "outpatient",
            "department_id": appointment.department_id.id or False,
            "primary_doctor_id": appointment.doctor_id.id or False,
            "opened_at": appointment.appointment_date or fields.Datetime.now(),
        })

    @api.model
    def activate_charge(self, charge):
        """Draft -> Active. Called when the CLINICAL document is confirmed.

        Idempotent and non-destructive: it only ever promotes draft, so it can never
        reset authorization, delivery, payment or invoice state, and it silently
        skips a cancelled or reversed charge.
        """
        for line in charge:
            if line.charge_state == "draft":
                line.sudo().write({"charge_state": "active"})
        return charge

    @api.model
    def authorize_charge(self, charge, authorized_by=None, reference=None):
        """Record payer authorization for a charge. Idempotent."""
        charge.ensure_one()
        charge._assert_group(AUTHORIZE_GROUPS, "authorize a charge")
        if charge.authorization_state == "authorized":
            return charge
        if charge.charge_state in FROZEN_CHARGE_STATES:
            raise UserError(f"Charge {charge.name} is {charge.charge_state} and cannot be authorized.")
        charge.sudo().write({
            "authorization_state": "authorized",
            "authorized_at": fields.Datetime.now(),
        })
        self.env["hospital.audit.log"].create_log(
            patient_id=charge.patient_id.id, model_name=charge._name, record_id=charge.id,
            action_type="update",
            description=(
                f"Charge {charge.name} authorized by "
                f"{(authorized_by or self.env.user).display_name}"
                + (f" (ref {reference})" if reference else "") + "."
            ),
        )
        return charge

    @api.model
    def check_financial_clearance(self, encounter, service=None, persist=False, charges=None):
        """Is this encounter cleared for service DELIVERY to commence?

        Returns a dict; it does not raise. The caller decides whether to block.

        ``charges``: restrict the check to these charge lines. Callers gating a
        SPECIFIC service (a consultation, the tests on one lab request) must pass
        them, so that an unrelated unpaid charge elsewhere on the account cannot
        block care that is itself paid for. Omit to check the whole account.

        Reads through sudo(): a doctor legitimately triggers this check but may not
        read the cash fields it inspects.

        PURE BY DEFAULT. It only persists financial_clearance_state when the caller
        explicitly asks (persist=True, i.e. from a workflow action). Computes and
        form reads call it with persist=False -- a compute must never write.
        """
        encounter.ensure_one()
        enc = encounter.sudo()
        account = enc.billing_account_id

        def _persist(state):
            if persist and account and account.financial_clearance_state != state:
                account.write({"financial_clearance_state": state})

        def _scope():
            """The charge lines this decision is about."""
            if charges is not None:
                return charges.sudo().filtered(
                    lambda c: c.charge_state in ("draft", "active")
                )
            return account.charge_line_ids.filtered(
                lambda c: c.charge_state in ("draft", "active")
            )

        # Emergency bypass short-circuits everything. It is already audited on the
        # encounter (reason + authorizer + timestamp are mandatory there).
        if enc.emergency_bypass:
            _persist("emergency_bypass")
            return {
                "cleared": True, "state": "emergency_bypass",
                "amount_due": 0.0,
                "reason": "Emergency bypass authorized by %s" % (
                    enc.emergency_bypass_authorized_by.display_name or "n/a"),
            }

        if not account:
            return {"cleared": True, "state": "not_required", "amount_due": 0.0,
                    "reason": "No billing account; nothing to clear."}

        # Third-party payers: clearance is authorization/credit, never cash.
        # NOTE: this records that a payer has AUTHORIZED the charge. It is not a
        # validation of the insurance policy itself -- no eligibility, benefit or
        # coverage-limit checking exists yet.
        if enc.payer_type != "self_pay":
            pending = _scope().filtered(lambda c: c.authorization_state == "pending")
            if pending:
                _persist("pending")
                return {
                    "cleared": False, "state": "pending", "amount_due": 0.0,
                    "reason": "Payer authorization pending for: %s" % ", ".join(
                        pending.mapped("description")),
                }
            _persist("credit_authorized")
            return {"cleared": True, "state": "credit_authorized", "amount_due": 0.0,
                    "reason": "Payer credit authorized; no cash required."}

        # Self-pay: only PREPAID services demand cash before delivery.
        due = sum(_scope().mapped("amount_due_for_clearance"))
        if due > AMOUNT_TOLERANCE:
            _persist("pending")
            return {"cleared": False, "state": "pending", "amount_due": due,
                    "reason": "Prepayment of %.2f is required before service." % due}
        _persist("cleared")
        return {"cleared": True, "state": "cleared", "amount_due": 0.0,
                "reason": "No outstanding pre-service payment."}

    @api.model
    def mark_charge_in_progress(self, charge):
        """Service has commenced. Idempotent."""
        charge.ensure_one()
        if charge.charge_state in FROZEN_CHARGE_STATES:
            raise UserError(f"Charge {charge.name} is {charge.charge_state}; it cannot start.")
        if charge.delivery_state in ("in_progress", "partially_delivered", "delivered"):
            return charge
        vals = {"delivery_state": "in_progress"}
        if not charge.service_started_at:
            vals["service_started_at"] = fields.Datetime.now()
        if charge.charge_state == "draft":
            vals["charge_state"] = "active"
        charge.sudo().write(vals)
        return charge

    @api.model
    def mark_charge_delivered(self, charge, qty_delivered=None):
        """Care was rendered. Routed through record_delivery() so it is audited
        and any delivery/invoice variance is raised rather than absorbed.

        Idempotent: re-delivering the same quantity is a no-op.
        """
        charge.ensure_one()
        if charge.charge_state in FROZEN_CHARGE_STATES:
            raise UserError(
                f"Charge {charge.name} is {charge.charge_state}; delivery cannot be recorded."
            )
        qty = charge.qty_requested if qty_delivered is None else qty_delivered
        if charge.delivery_state == "delivered" and abs(charge.qty_delivered - qty) < QTY_TOL:
            return charge
        charge.sudo().record_delivery(qty, reason="Service delivered")
        return charge

    @api.model
    def cancel_charge(self, charge, reason=None):
        """Cancel an undelivered charge. NEVER deletes.

        Delivered care is never cancelled -- it happened. Such a charge must be
        credited or reversed by the accounting phase instead.
        """
        charge.ensure_one()
        if charge.charge_state in FROZEN_CHARGE_STATES:
            return charge  # idempotent
        if charge.qty_delivered > QTY_TOL or charge.delivery_state in (
            "delivered", "partially_delivered"
        ):
            raise UserError(
                f"Charge {charge.name} has been delivered and cannot be cancelled. "
                "Credit or reverse it instead."
            )
        charge.sudo().write({"cancel_reason": reason or "Cancelled"})
        charge.sudo().action_cancel()
        self.env["hospital.audit.log"].create_log(
            patient_id=charge.patient_id.id, model_name=charge._name, record_id=charge.id,
            action_type="state_change",
            description=f"Charge {charge.name} cancelled. Reason: {reason or 'n/a'}",
        )
        return charge

    # ------------------------------------------------------------------
    # Later phases — stable signatures, controlled failure
    # ------------------------------------------------------------------
    @api.model
    def reverse_charge(self, charge, reason=None):
        return self._not_implemented("reverse_charge")

    @api.model
    def allocate_payer(self, billing_account):
        return self._not_implemented("allocate_payer")

    @api.model
    def create_invoice(self, billing_account, charges=None, request_token=None):
        return self._not_implemented("create_invoice")

"""Shared billing service layer.

Every clinical module (pharmacy, laboratory, radiology, procedure, admission, ...)
must route charge creation through this engine rather than writing charge lines
directly. That keeps idempotency, authorization and state transitions in one place.

Phase 1 implements only account provisioning and idempotent charge upsert.
The remaining methods are declared with their final signatures and raise a
controlled error so callers can be written against the stable API today.
"""

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

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
        # payer_type and payer_id are deliberately NOT passed. They used to be
        # COPIED here, once, and never resynchronised -- which is exactly how the
        # account's classification could drift away from the encounter's while
        # check_financial_clearance kept reading the encounter and the invoice
        # engine kept reading the account. They are now stored related fields on
        # hospital.billing.account and derive themselves from encounter_id.
        return self.env["hospital.billing.account"].create(
            {"encounter_id": encounter.id}
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

        # Emergency bypass short-circuits everything, ahead of the mode read. It
        # is an INDEPENDENT authorized route and is never merged with payer
        # sponsorship: a bypass says care proceeds without payment being settled,
        # which is a different claim from a sponsor having agreed to pay.
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

        mode = enc.company_id.sudo().payer_responsibility_mode or "off"

        # WHICH DOMAIN OWNS THIS VISIT.
        #
        # Under 'enforce', a visit that participates in the NEW responsibility
        # domain must be answered by the responsibility engine and by nothing
        # else. Letting the legacy whole-bill waiver answer first would mean an
        # encounter with a merely DRAFTED sponsor share -- one nobody
        # authorized -- still cleared for zero cash, because payer_type happened
        # to read 'insurance'. That is the exact bypass Phase 2B refused to
        # create by writing payer_type, and it must not reappear here.
        #
        # A visit that does NOT participate keeps the legacy waiver untouched,
        # so existing sponsored data goes on working with no migration.
        new_domain = mode == "enforce" and self._participates_in_responsibility(
            enc, _scope()
        )

        # Third-party payers: clearance is authorization/credit, never cash.
        #
        # LEGACY. Driven by encounter.payer_type, which Phase 2B/3 deliberately
        # does not write and this engine deliberately does not read as a source
        # of any sponsor split. Untouched under 'off' and 'shadow', and untouched
        # under 'enforce' for any visit outside the new domain.
        if enc.payer_type != "self_pay" and not new_domain:
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

        if mode == "enforce":
            decision = self._responsibility_clearance(enc, _scope())
            if decision is not None:
                _persist(decision["state"])
                return decision

        # Self-pay: only PREPAID services demand cash before delivery.
        #
        # Reached in every mode when no sponsor share is in play. Under 'off' and
        # 'shadow' amount_due_for_clearance is the legacy whole-charge figure, so
        # this arm is unchanged; under 'enforce' with no authorized sponsor the
        # patient residual EQUALS amount_estimated, so it is unchanged there too.
        due = sum(_scope().mapped("amount_due_for_clearance"))
        if due > AMOUNT_TOLERANCE:
            _persist("pending")
            return {"cleared": False, "state": "pending", "amount_due": due,
                    "reason": "Prepayment of %.2f is required before service." % due}
        _persist("cleared")
        return {"cleared": True, "state": "cleared", "amount_due": 0.0,
                "reason": "No outstanding pre-service payment."}

    @api.model
    def _participates_in_responsibility(self, encounter, charges):
        """Is this visit inside the NEW payer responsibility domain?

        Deterministic, and read from new-domain fields ONLY -- never inferred
        from the legacy payer_type, and never from a business rule about what
        kind of visit "ought" to be sponsored. Two signals, either sufficient:

          1. the visit carries a payer eligibility (encounter.patient_payer_id),
             which is the Front Desk act that puts a visit into this domain; or
          2. some live charge already carries a sponsor share, drafted or
             authorized (responsibility_state != 'self_pay').

        Signal 1 matters on its own: an eligibility recorded with no share yet
        means the sponsor carries NOTHING, so the patient carries everything.
        Falling back to the legacy waiver there would clear the visit for zero
        cash on the strength of an identity that promises no money.

        Signal 2 catches the reverse case -- a share exists but the eligibility
        was later cleared off the encounter -- so a row can never be orphaned
        into a domain that no longer governs it.
        """
        if encounter.sudo().patient_payer_id:
            return True
        return bool(
            charges.sudo().filtered(
                lambda c: c.responsibility_state != "self_pay"
            )
        )

    @api.model
    def _responsibility_clearance(self, encounter, charges):
        """ENFORCE-mode decision, or None to fall through to the cash arm.

        Returns None -- not a verdict -- whenever no sponsor share is in play,
        so a self-pay visit under 'enforce' takes exactly the same code path it
        takes under 'off'. That is what makes enforce safe to switch on for a
        hospital whose visits are mostly self-pay.

        Order is deterministic and fails CLOSED:

            1. a sponsor share exists but is only PROPOSED  -> blocked
            2. patient cash still due                       -> blocked
            3. patient share is zero, sponsor carries it all -> sponsor_cleared
            4. mixed and settled                             -> cleared
        """
        sponsored = charges.filtered(
            lambda c: c.responsibility_state in ("proposed", "authorized")
        )
        if not sponsored:
            return None

        # 1. An unauthorized proposal is not a promise to pay. A visit must never
        #    be cleared merely because an eligibility was selected at the desk.
        proposed = sponsored.filtered(
            lambda c: c.responsibility_state == "proposed"
        )
        if proposed:
            return {
                "cleared": False,
                "state": "pending",
                "amount_due": sum(charges.mapped("amount_due_for_clearance")),
                "reason": (
                    "Sponsor responsibility is recorded but NOT authorized for: %s. "
                    "An authorized sponsor share is required before the patient "
                    "share can be treated as final."
                    % ", ".join(proposed.mapped("description"))
                ),
            }

        # 2. Whatever the patient still owes, on the patient share alone.
        due = sum(charges.mapped("amount_due_for_clearance"))
        if due > AMOUNT_TOLERANCE:
            return {
                "cleared": False,
                "state": "pending",
                "amount_due": due,
                "reason": (
                    "Patient responsibility of %.2f is required before service; "
                    "the sponsor carries %.2f."
                    % (due, sum(charges.mapped("amount_sponsor_authorized")))
                ),
            }

        # 3. Fully sponsored: no cash was required and none was taken. This is a
        #    financially valid state for the doctor-start gate, and it is NOT
        #    'cleared' -- nothing was paid.
        patient_share = sum(charges.mapped("amount_patient_responsibility"))
        if patient_share <= AMOUNT_TOLERANCE:
            return {
                "cleared": True,
                "state": "sponsor_cleared",
                "amount_due": 0.0,
                "reason": (
                    "Fully sponsored: %.2f authorized to %s. No patient cash is "
                    "required."
                    % (
                        sum(charges.mapped("amount_sponsor_authorized")),
                        encounter.patient_payer_id.sudo().display_name or "the sponsor",
                    )
                ),
            }

        # 4. Mixed, and the patient's part has been settled.
        return {
            "cleared": True,
            "state": "cleared",
            "amount_due": 0.0,
            "reason": (
                "Patient responsibility settled; %.2f carried by the sponsor."
                % sum(charges.mapped("amount_sponsor_authorized"))
            ),
        }

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
    def allocate_payer(
        self,
        billing_account,
        charge=None,
        amount=None,
        reason=None,
        request_token=None,
        authorize=False,
        authorization_reference=None,
    ):
        """Record -- and optionally authorize -- a sponsor share for one charge.

        RECORDS a decision; it does not CALCULATE one. There is no coverage
        percentage, copay rate or benefit schedule anywhere in this system, so
        the amount is supplied by an authorized officer. Deriving it would mean
        inventing the rule.

        Idempotent through ``request_token``: a retry returns the existing row
        rather than adding a second share, exactly as
        hospital.billing.account.record_operational_payment() does with
        intake_token. Callers that omit the token get no replay protection.

        Returns the hospital.charge.responsibility record.
        """
        Responsibility = self.env["hospital.charge.responsibility"]
        Responsibility._assert_authority("allocate sponsor responsibility")

        account = billing_account
        if account:
            account.ensure_one()
        if charge is None:
            raise UserError(
                "allocate_payer requires the charge the sponsor share applies "
                "to. Responsibility is per charge, because invoicing, delivery "
                "and cash allocation are all per charge."
            )
        charge.ensure_one()
        if account and charge.sudo().billing_account_id != account:
            raise UserError(
                "Charge %s does not belong to billing account %s."
                % (charge.name, account.name)
            )
        account = account or charge.sudo().billing_account_id

        # Serialize against the cashier and against a concurrent authorization
        # BEFORE reading anything we are about to decide on.
        self.env["hospital.billing.account"]._lock_responsibility_scope(account.id)

        if request_token:
            existing = Responsibility.sudo().search(
                [("request_token", "=", request_token)], limit=1
            )
            if existing:
                same_request = (
                    existing.charge_id == charge
                    and abs(existing.amount - float(amount or 0.0)) <= AMOUNT_TOLERANCE
                )
                if not same_request:
                    raise ValidationError(
                        "This responsibility request token has already been used "
                        "for a different charge or amount."
                    )
                if authorize and existing.state == "draft":
                    existing.action_authorize(
                        authorization_reference=authorization_reference
                    )
                return existing

        if amount is None:
            raise UserError(
                "allocate_payer requires an explicit sponsor amount. No coverage "
                "rule exists from which one could be derived."
            )
        amount = float(amount)

        encounter = charge.sudo().encounter_id
        eligibility = encounter.patient_payer_id
        if not eligibility:
            raise UserError(
                "Encounter %s has no payer eligibility selected, so no sponsor "
                "can carry any part of its charges."
                % encounter.name
            )

        record = Responsibility.create(
            {
                "charge_id": charge.id,
                "patient_payer_id": eligibility.id,
                "amount": amount,
                "reason": reason or False,
                # STORED, not merely searched. Omitting it made every retry miss
                # the lookup above and create a second share.
                "request_token": request_token or False,
            }
        )
        if authorize:
            record.action_authorize(
                authorization_reference=authorization_reference
            )
        return record

    @api.model
    def create_invoice(self, billing_account, charges=None, request_token=None):
        return self._not_implemented("create_invoice")

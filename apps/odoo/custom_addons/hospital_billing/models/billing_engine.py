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
            created = charge_model.create(vals)
            # HOOK 1 of 3. Every clinical module routes charge creation through
            # this method, so this is the one place that sees a new charge
            # whatever raised it. If the visit already carries an eligibility,
            # the agreement's own decision is applied immediately -- so a lab or
            # pharmacy charge is already split before the cashier ever reads it.
            #
            # Idempotent and silent when there is no payer, so a self-pay visit
            # pays nothing for this call.
            self.resolve_charge_coverage(created)
            return created

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
    # BENEFIT COVERAGE EVALUATION
    # ------------------------------------------------------------------
    @api.model
    def evaluate_charge_coverage(self, charge, patient_payer=None, on_date=None):
        """What the AGREEMENT PERMITS a sponsor to carry for one charge.

        READ-ONLY, AND THAT IS THE POINT. It creates nothing, authorizes
        nothing and writes nothing -- not a responsibility row, not a
        reservation, not a state change. It answers "what does the contract
        allow", and the Insurance/Credit workflow separately decides whether to
        accept that answer by calling allocate_payer().

        Keeping the two apart is what preserves auditability. A rule edited next
        year must never restate what a sponsor accepted last year, so the
        historical fact lives on hospital.charge.responsibility with its own
        frozen snapshot, and this method only ever describes policy AS IT
        STANDS TODAY.

        Basis: charge.amount_estimated -- the same figure the responsibility
        engine divides and the cash gate reads. A second billing basis here
        would let the permitted share and the residual disagree.

        Returns a dict; it never raises for a business outcome. A denial is a
        result with a reason_code, not an exception, because the caller is
        usually rendering it rather than reacting to it.
        """
        charge.ensure_one()
        record = charge.sudo()
        charge_amount = max(0.0, record.amount_estimated or 0.0)

        def result(**overrides):
            payload = {
                "charge_id": record.id,
                "charge_amount": charge_amount,
                "currency_id": record.currency_id.id or False,
                "matched_rule_id": False,
                "coverage_state": "not_covered",
                "excluded": False,
                "requires_authorization": False,
                "calculated_sponsor_amount": 0.0,
                "limit_available": None,
                "permitted_sponsor_amount": 0.0,
                "patient_residual": charge_amount,
                "reason_code": "no_coverage",
                "reason": "",
            }
            payload.update(overrides)
            # THE INVARIANTS, enforced at the single exit rather than trusted at
            # each branch: no negative amount, no sponsor share exceeding the
            # charge, and a residual that is always the arithmetic complement of
            # what was permitted.
            permitted = max(0.0, min(payload["permitted_sponsor_amount"], charge_amount))
            payload["permitted_sponsor_amount"] = permitted
            payload["calculated_sponsor_amount"] = max(
                0.0, min(payload["calculated_sponsor_amount"], charge_amount)
            )
            payload["patient_residual"] = max(0.0, charge_amount - permitted)
            return payload

        # --- eligibility and agreement must both be live -------------------
        eligibility = patient_payer or record.encounter_id.sudo().patient_payer_id
        if not eligibility:
            return result(
                reason_code="no_eligibility",
                reason="This visit carries no payer eligibility, so the patient "
                "is responsible for the full amount.",
            )
        eligibility = eligibility.sudo()
        agreement = eligibility.agreement_id

        if eligibility.patient_id != record.patient_id:
            return result(
                reason_code="eligibility_patient_mismatch",
                reason="Eligibility %s belongs to a different patient."
                % eligibility.display_name,
            )
        if agreement.company_id != record.company_id:
            return result(
                reason_code="company_mismatch",
                reason="Agreement %s belongs to another company."
                % agreement.display_name,
            )
        if agreement.currency_id and record.currency_id and (
            agreement.currency_id != record.currency_id
        ):
            return result(
                reason_code="currency_mismatch",
                reason="Agreement %s is denominated in %s but this charge is in %s."
                % (
                    agreement.display_name,
                    agreement.currency_id.name,
                    record.currency_id.name,
                ),
            )
        # is_valid_today already composes the eligibility's own state and window
        # WITH its agreement's. An expired contract or a suspended member grants
        # nothing, and that predicate is the one Front Desk selection already
        # trusts -- restating it here would let the two drift.
        if not eligibility.is_valid_today:
            return result(
                reason_code="eligibility_not_valid",
                reason="Eligibility %s is not valid today (eligibility '%s', "
                "agreement '%s')."
                % (eligibility.display_name, eligibility.state, agreement.state),
            )

        # --- resolve the rule ---------------------------------------------
        rule = agreement.match_benefit_rule(record.service_id)
        requires_auth = bool(agreement.sudo().authorization_required)

        if rule:
            requires_auth = requires_auth or bool(rule.authorization_required)
            if rule.coverage_type == "excluded":
                return result(
                    matched_rule_id=rule.id,
                    excluded=True,
                    coverage_state="excluded",
                    requires_authorization=requires_auth,
                    reason_code="service_excluded",
                    reason="This agreement explicitly excludes %s."
                    % (record.service_id.display_name or record.description),
                )
            eligible = rule._sponsor_eligible_for(charge_amount)
            state = "covered"
            reason_code = "rule_matched"
            reason = "Benefit rule %s applies." % rule.display_name
        else:
            policy = agreement.sudo().default_coverage_policy
            if policy == "not_covered":
                return result(
                    coverage_state="not_covered",
                    requires_authorization=requires_auth,
                    reason_code="default_not_covered",
                    reason="No benefit rule matches and this agreement covers "
                    "nothing by default.",
                )
            if policy == "default_percentage":
                eligible = max(
                    0.0,
                    min(
                        charge_amount
                        * (agreement.sudo().default_coverage_percent or 0.0)
                        / 100.0,
                        charge_amount,
                    ),
                )
                state = "covered"
                reason_code = "default_percentage"
                reason = "No benefit rule matches; the agreement default applies."
            else:
                # manual_authorization -- and THE upgrade-safe path. Every
                # agreement that predates benefit rules lands here and permits
                # nothing automatically, leaving the officer's manual decision
                # exactly as authoritative as it was before this module changed.
                return result(
                    coverage_state="manual_authorization",
                    requires_authorization=True,
                    reason_code="default_manual",
                    reason="No benefit rule matches. An Insurance/Credit Officer "
                    "must decide this share manually.",
                )

        # --- cap by the remaining ceiling ---------------------------------
        remaining = agreement.remaining_benefit_for(
            patient_payer=eligibility,
            encounter=record.encounter_id,
            on_date=on_date,
        )
        permitted = eligible
        if remaining is not None:
            permitted = min(eligible, remaining)
            if permitted <= AMOUNT_TOLERANCE and eligible > AMOUNT_TOLERANCE:
                state = "limit_exhausted"
                reason_code = "limit_exhausted"
                reason = (
                    "The benefit ceiling for this agreement is exhausted; the "
                    "patient carries the full amount."
                )
            elif permitted + AMOUNT_TOLERANCE < eligible:
                state = "limit_capped"
                reason_code = "limit_capped"
                reason = (
                    "Coverage of %.2f was reduced to %.2f by the remaining "
                    "benefit ceiling." % (eligible, permitted)
                )

        return result(
            matched_rule_id=rule.id if rule else False,
            coverage_state=state,
            requires_authorization=requires_auth,
            calculated_sponsor_amount=eligible,
            limit_available=remaining,
            permitted_sponsor_amount=permitted,
            reason_code=reason_code,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # AUTOMATIC COVERAGE RESOLUTION
    #
    # THE PROBLEM THIS SOLVES. An agreement that says "consultation, 80%,
    # no prior authorization" has already made the decision. Routing that
    # through a human queue means an officer clicks Approve on the same 80%
    # several hundred times a day, and until they do, the cashier asks the
    # patient for the full 300 that the contract says the sponsor owes 240 of.
    #
    # So: whenever the agreement contains enough information to decide, the
    # server decides. Only genuine judgement reaches the Insurance/Credit desk.
    #
    # WHAT COUNTS AS "ENOUGH INFORMATION"
    #   permitted > 0 and no authorization required  -> authorize permitted
    #   permitted == 0 for a stated reason           -> authorize ZERO (a denial)
    #   authorization required                       -> leave it for an officer
    #   no policy at all (default manual)            -> leave it for an officer
    #
    # A zero authorization is a real decision, not an absence of one: the
    # patient carries the full charge, no benefit is consumed, and the visit
    # leaves the review queue. See hospital.charge.responsibility.
    # ------------------------------------------------------------------
    #
    # Evaluator reason codes whose outcome is settled without a human. Each
    # names a decision the CONTRACT made: an explicit exclusion, a policy of
    # covering nothing, or a ceiling that is spent. 'default_manual' is
    # deliberately absent -- it means no policy matched at all, which is the one
    # case that genuinely needs judgement.
    DETERMINISTIC_ZERO_REASONS = frozenset(
        {
            "service_excluded",
            "default_not_covered",
            "limit_exhausted",
        }
    )

    @api.model
    def charge_requires_manual_decision(self, charge, patient_payer=None):
        """Does this charge genuinely need an Insurance/Credit officer?

        THE QUEUE PREDICATE. Used by the officer worklist so the desk shows
        exceptions rather than every sponsored charge in the hospital.
        """
        record = charge.sudo()
        if record.charge_state not in ("draft", "active"):
            return False
        # A live row means somebody or something already decided.
        if record.responsibility_state != "self_pay":
            return False

        encounter = record.encounter_id
        eligibility = patient_payer or encounter.patient_payer_id
        if not eligibility or not eligibility.sudo().is_valid_today:
            # No sponsor domain at all: this is a self-pay charge, not a
            # pending insurance decision.
            return False

        coverage = self.evaluate_charge_coverage(record, eligibility)
        return self._coverage_needs_officer(coverage)

    @api.model
    def _coverage_needs_officer(self, coverage):
        """One place decides deterministic-vs-manual, so the queue and the
        auto-resolver can never disagree about the same charge."""
        if coverage["reason_code"] == "default_manual":
            return True
        if coverage["requires_authorization"] and (
            coverage["permitted_sponsor_amount"] > AMOUNT_TOLERANCE
        ):
            # There is something to authorize and the contract says a human
            # must. When the permitted amount is zero there is nothing for the
            # officer to approve, so the flag changes no outcome.
            return True
        return False

    @api.model
    def resolve_charge_coverage(self, charge, patient_payer=None):
        """Apply the agreement's own decision to one charge, if it has one.

        IDEMPOTENT AND SAFE TO CALL REPEATEDLY. It returns early when a live
        responsibility row already exists, so re-running it after every charge
        update, payer change and triage handoff costs a read and changes
        nothing.

        Returns the responsibility record it created, or an empty recordset when
        the decision belongs to an officer (or there is no sponsor at all).
        """
        Responsibility = self.env["hospital.charge.responsibility"]
        empty = Responsibility.browse()

        record = charge.sudo()
        if record.charge_state not in ("draft", "active"):
            return empty

        encounter = record.encounter_id
        eligibility = patient_payer or encounter.patient_payer_id
        if not eligibility:
            return empty
        eligibility = eligibility.sudo()
        if not eligibility.is_valid_today:
            # An expired or suspended member is not a denial: the front desk may
            # still fix the eligibility. Leave the charge alone rather than
            # recording a zero the officer would have to unpick.
            return empty

        account = record.billing_account_id
        if not account:
            return empty

        # THE SAME LOCK the cashier and the officer take. Everything below reads
        # live benefit availability, so it has to be serialized against anything
        # that could consume it between the read and the write.
        self.env["hospital.billing.account"]._lock_responsibility_scope(account.id)

        # Re-read AFTER the lock: another visit may have consumed the member's
        # remaining benefit while this transaction was waiting for it.
        record.invalidate_recordset(
            ["responsibility_state", "amount_sponsor_authorized"]
        )
        if record.responsibility_state != "self_pay":
            # Already decided -- by an officer, or by a previous run of this
            # method. Authorized rows are frozen and are never rewritten here.
            return empty

        # A charge whose sponsor decision is frozen by a receipt or an invoice
        # must not gain one now: the patient figure it would change has already
        # been acted upon.
        if account.sudo().amount_received > AMOUNT_TOLERANCE:
            return empty
        if record.invoice_state != "not_invoiced":
            return empty

        coverage = self.evaluate_charge_coverage(record, eligibility)
        if self._coverage_needs_officer(coverage):
            return empty

        permitted = coverage["permitted_sponsor_amount"]
        reason_code = coverage["reason_code"]

        if permitted <= AMOUNT_TOLERANCE:
            if reason_code not in self.DETERMINISTIC_ZERO_REASONS:
                # Zero for a reason nobody stated. Refusing to guess is the
                # whole discipline here: leave it visible rather than record a
                # denial the contract never made.
                return empty
            amount = 0.0
        else:
            amount = permitted

        # A SYSTEM reason, not a human one. action_authorize requires a
        # documented reason because a human decision that changes the patient's
        # bill must be explainable; an automatic one is explained by naming the
        # rule that produced it, which is exactly as auditable and does not
        # invite an operator to type "ok" several hundred times a day.
        reason = "Automatic (%s): %s" % (
            reason_code or "agreement_policy",
            coverage["reason"] or "resolved from the agreement's benefit policy",
        )

        # Deterministic token: re-running this for the same charge and the same
        # eligibility cannot create a second row even if the early return above
        # is somehow bypassed. The unique index on request_token is the backstop.
        token = "auto:%s:%s" % (record.id, eligibility.id)

        return self.allocate_payer(
            account,
            charge=record,
            amount=amount,
            reason=reason,
            request_token=token,
            authorize=True,
            authorization_reference=None,
        )

    @api.model
    def resolve_account_coverage(self, billing_account):
        """Resolve every live charge on one visit. Idempotent.

        THE RECONCILIATION ENTRY POINT. Charges and payer identity arrive in
        either order -- a consultation charge is raised by action_confirm before
        the front desk has chosen an eligibility, while a lab charge is raised
        long after -- so resolution cannot be a single event at charge creation.
        This is called from both sides and again at the triage handoff, and
        being idempotent is what makes that safe rather than merely tolerable.
        """
        account = billing_account
        if not account:
            return self.env["hospital.charge.responsibility"].browse()
        account.ensure_one()

        encounter = account.sudo().encounter_id
        if not encounter or not encounter.patient_payer_id:
            return self.env["hospital.charge.responsibility"].browse()

        resolved = self.env["hospital.charge.responsibility"].browse()
        for charge in account.sudo().charge_line_ids:
            if charge.charge_state in ("draft", "active"):
                resolved |= self.resolve_charge_coverage(charge)
        return resolved

    # ------------------------------------------------------------------
    # INSURANCE / CREDIT AUTHORIZATION
    # ------------------------------------------------------------------
    @api.model
    def authorize_visit_coverage(
        self, billing_account, decisions, request_token=None
    ):
        """Authorize sponsor shares for several charges as ONE decision.

        THE EVALUATOR SAYS WHAT THE AGREEMENT PERMITS. THIS SAYS WHAT THE
        OFFICER AUTHORIZED. The two are kept apart deliberately: a benefit rule
        edited next year must never restate what a sponsor accepted today, so
        the permitted figure is recomputed on demand while the authorized figure
        is a frozen row on hospital.charge.responsibility.

        ``decisions``: [{"charge_id": int, "amount": float, "reason": str}, ...]

        WHY THE BROWSER'S NUMBER IS NEVER TRUSTED
        -----------------------------------------
        The officer's page may have been open for minutes while another visit
        consumed the same member ceiling. Every charge is therefore RE-EVALUATED
        inside the lock, and the requested amount is validated against that live
        figure -- not against whatever the client believed when it rendered. An
        officer who requests 4,000 against a ceiling that now has 2,000 left is
        refused outright rather than silently capped: silently giving someone a
        different number than they authorized is worse than making them look
        again.

        ATOMIC. One lock, one transaction, no partial success. Either every
        decision in the batch is authorized or none is, because a half-applied
        batch leaves the officer unable to tell what they actually approved.

        ZERO IS A LEGITIMATE DECISION -- it is how the desk records "the sponsor
        will not cover this". It needs a reason like any other authorization,
        consumes no benefit, and leaves the patient carrying the full charge.
        See the denial note on hospital.charge.responsibility.

        Returns the hospital.charge.responsibility records, one per decision.
        """
        account = billing_account
        account.ensure_one()

        Responsibility = self.env["hospital.charge.responsibility"]
        Responsibility._assert_authority("authorize sponsor responsibility")

        if not decisions:
            raise UserError("No charges were selected for authorization.")

        # ONE lock for the whole batch, taken before the first read. The same
        # key the cashier takes, so an officer authorizing and a cashier
        # collecting cannot interleave and compute a residual from a state
        # neither of them ever saw.
        self.env["hospital.billing.account"]._lock_responsibility_scope(account.id)

        encounter = account.sudo().encounter_id
        eligibility = encounter.patient_payer_id
        if not eligibility:
            raise UserError(
                "Encounter %s has no payer eligibility selected. There is no "
                "sponsor to authorize." % encounter.name
            )

        results = Responsibility.browse()
        for index, decision in enumerate(decisions):
            charge = self.env["hospital.charge.line"].browse(
                decision.get("charge_id")
            ).exists()
            if not charge:
                raise UserError(
                    "Charge %s does not exist." % decision.get("charge_id")
                )
            if charge.sudo().billing_account_id != account:
                raise UserError(
                    "Charge %s does not belong to this visit." % charge.sudo().name
                )

            requested = decision.get("amount")
            if requested is None:
                raise UserError(
                    "Charge %s has no authorized amount." % charge.sudo().name
                )
            requested = float(requested)
            if requested < 0.0:
                raise UserError(
                    "Charge %s: an authorized sponsor amount cannot be negative."
                    % charge.sudo().name
                )

            reason = (decision.get("reason") or "").strip()

            # LIVE re-evaluation, inside the lock. This is the whole point.
            coverage = self.evaluate_charge_coverage(charge, eligibility)
            permitted = coverage["permitted_sponsor_amount"]

            if requested > permitted + AMOUNT_TOLERANCE:
                raise UserError(
                    "Charge %s: %.2f cannot be authorized because the agreement "
                    "currently permits at most %.2f (%s).\n\n"
                    "The available benefit may have changed since this page was "
                    "opened. Reload the visit and decide again."
                    % (charge.sudo().name, requested, permitted, coverage["reason"])
                )

            # A reduction or a denial is a departure from what the agreement
            # allows, so it has to be explained. Accepting the full permitted
            # amount does not -- the rule already documents that.
            if requested + AMOUNT_TOLERANCE < permitted and not reason:
                raise UserError(
                    "Charge %s: authorizing %.2f instead of the permitted %.2f "
                    "requires a documented reason."
                    % (charge.sudo().name, requested, permitted)
                )
            if not reason:
                reason = "Authorized in full per %s." % (
                    coverage["reason"] or "the agreement's benefit policy"
                )

            # A live row for this charge already exists. Authorized rows are
            # frozen, so a CHANGE is cancel + recreate -- never an in-place
            # rewrite of a decision someone already made.
            existing = Responsibility.sudo().search(
                [
                    ("charge_id", "=", charge.id),
                    ("state", "in", ["draft", "authorized"]),
                ],
                limit=1,
            )
            if existing:
                if existing.state == "authorized":
                    if abs(existing.amount - requested) <= AMOUNT_TOLERANCE:
                        # Same decision, already recorded. Idempotent.
                        results |= existing
                        continue
                    raise UserError(
                        "Charge %s already carries an authorized sponsor share of "
                        "%.2f. Cancel it before authorizing a different amount: "
                        "an authorized decision is never rewritten in place."
                        % (charge.sudo().name, existing.amount)
                    )
                # A draft proposal: carry the officer's figure onto it.
                existing.write({"amount": requested, "reason": reason})
                existing.action_authorize(
                    authorization_reference=decision.get("authorization_reference")
                )
                results |= existing
                continue

            # Per-charge token so a replayed batch cannot double-allocate, while
            # each charge still gets its own unique key.
            token = (
                "%s:%s" % (request_token, charge.id) if request_token else None
            )
            results |= self.allocate_payer(
                account,
                charge=charge,
                amount=requested,
                reason=reason,
                request_token=token,
                authorize=True,
                authorization_reference=decision.get("authorization_reference"),
            )

        return results

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

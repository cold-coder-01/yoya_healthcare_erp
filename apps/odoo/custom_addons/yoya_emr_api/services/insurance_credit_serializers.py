"""Officer-safe serializers for the Insurance/Credit Desk.

WHAT AN OFFICER MAY SEE, AND WHY IT DIFFERS FROM THE CASHIER
------------------------------------------------------------
The Cashier serializer is built to hide commercial terms: a cashier collects a
residual and has no business knowing a coverage rate. The officer is the
opposite case -- deciding a sponsor share is impossible without seeing the rule
that produced it, so the matched rule's DISPLAY NAME and the evaluator's reason
are exposed here deliberately.

That is not a licence to expose the contract. Still absent, permanently:

  limit_amount / member_limit_amount   The agreement's own ceiling. The officer
                                       needs REMAINING benefit for this member,
                                       which is what limit_available carries;
                                       the contract's headline figure is not an
                                       operational number.
  payment_terms_days, tariff_mode      Commercial terms of the contract itself.
  agreement.notes, rule.notes          Internal commercial commentary.
  amount_invoiced / amount_credited /
  amount_applied_to_invoice /
  receivable_balance                   groups=ACCOUNTING_READ. An officer
                                       authorizes exposure; they do not book it.

Every figure below comes from the evaluator or from stored operational fields.
Nothing is recomputed here, so the desk cannot disagree with the engine.
"""

from .api_response import (
    date_value,
    datetime_value,
    float_value,
    selection_value,
)
from .reception_scope import insurance_credit_capability_flags

UNDECIDED_RESPONSIBILITY_STATE = "self_pay"


def charge_needs_decision(charge):
    """Does this charge genuinely need an OFFICER, not merely a decision?

    THIS PREDICATE WAS NARROWED. It used to mean "no live sponsor row exists",
    which put every sponsored charge in the queue -- including the ones the
    agreement had already decided. An officer approving the same 80% several
    hundred times a day is not review, it is data entry, and while they worked
    through it the cashier was asking those patients for the full amount.

    It now defers to the engine, which answers the narrower question: is there
    anything here a human must judge? Deterministic outcomes (a stated
    percentage with no prior authorization, an explicit exclusion, a spent
    ceiling, a not-covered default) are resolved automatically and never
    appear. Only a prior-authorization requirement or an agreement with no
    policy at all reaches the desk.

    Both sides read ONE implementation -- billing_engine._coverage_needs_officer
    -- so the queue and the auto-resolver cannot disagree about a charge.
    """
    return charge.env["hospital.billing.engine"].sudo(
    ).charge_requires_manual_decision(charge)


def serialize_officer_patient(patient):
    return {
        "id": patient.id,
        "name": patient.name,
        "identification_code": patient.identification_code,
        "age": patient.age,
        "gender": selection_value(patient.gender),
    }


def serialize_officer_eligibility(eligibility):
    """Member identity only. The same allowlist shape the front desk uses."""
    if not eligibility:
        return None
    agreement = eligibility.agreement_id
    return {
        "id": eligibility.id,
        "reference": eligibility.name,
        "payer_name": eligibility.payer_id.display_name or None,
        "agreement_number": agreement.agreement_number or None,
        "member_reference": eligibility.member_reference or None,
        "membership_number": eligibility.membership_number or None,
        "policy_number": eligibility.policy_number or None,
        "relationship_to_principal": selection_value(
            eligibility.relationship_to_principal
        ),
        "effective_from": date_value(eligibility.effective_from),
        "effective_to": date_value(eligibility.effective_to),
        "is_valid_today": bool(eligibility.is_valid_today),
        "state": eligibility.state,
    }


def serialize_officer_charge(env, charge, eligibility):
    """One charge, with what the agreement permits and what is authorized.

    The evaluator runs per charge. It is read-only and creates nothing, so
    rendering the queue can never move money.
    """
    coverage = env["hospital.billing.engine"].sudo().evaluate_charge_coverage(
        charge, eligibility
    )
    matched = env["hospital.payer.benefit.rule"].sudo().browse(
        coverage["matched_rule_id"]
    ) if coverage["matched_rule_id"] else None

    live = charge.responsibility_ids.filtered(
        lambda r: r.state in ("draft", "authorized")
    )[:1]

    return {
        "id": charge.id,
        "name": charge.name,
        "description": charge.description,
        "service": charge.service_id.display_name or None,
        "service_type": selection_value(charge.service_id.service_type),
        "amount": float_value(charge.amount_estimated),
        # What the agreement permits, today, under live benefit availability.
        "coverage_state": coverage["coverage_state"],
        "excluded": bool(coverage["excluded"]),
        "requires_authorization": bool(coverage["requires_authorization"]),
        "calculated_sponsor_amount": float_value(
            coverage["calculated_sponsor_amount"]
        ),
        "permitted_sponsor_amount": float_value(
            coverage["permitted_sponsor_amount"]
        ),
        "limit_available": (
            float_value(coverage["limit_available"])
            if coverage["limit_available"] is not None
            else None
        ),
        "reason_code": coverage["reason_code"],
        "reason": coverage["reason"],
        "matched_rule": matched.display_name if matched else None,
        # What has actually been decided.
        "authorized_sponsor_amount": float_value(charge.amount_sponsor_authorized),
        "patient_responsibility": float_value(charge.amount_patient_responsibility),
        "responsibility_state": selection_value(charge.responsibility_state),
        "responsibility_id": live.id if live else None,
        "responsibility_state_detail": live.state if live else None,
        "needs_decision": charge_needs_decision(charge),
        # Frozen once cash is taken or the charge is invoiced. The desk shows
        # WHY rather than offering an action that will be refused.
        "decision_frozen_reason": (
            live._payment_freeze_reason() if live else None
        ),
    }


def serialize_officer_visit_detail(env, appointment):
    """THE canonical officer payload: queue row, detail and authorize response."""
    encounter = appointment.encounter_id
    account = encounter.billing_account_id if encounter else None
    eligibility = encounter.patient_payer_id if encounter else None

    charges = []
    if account:
        charges = [
            serialize_officer_charge(env, charge, eligibility)
            for charge in account.charge_line_ids.filtered(
                lambda c: c.charge_state in ("draft", "active")
            )
        ]

    pending = [row for row in charges if row["needs_decision"]]
    return {
        "appointment": {
            "id": appointment.id,
            "appointment_code": appointment.appointment_code,
            "appointment_date": datetime_value(appointment.appointment_date),
            "state": appointment.state,
        },
        "patient": serialize_officer_patient(appointment.patient_id),
        "encounter": {
            "id": encounter.id,
            "name": encounter.name,
            "state": encounter.state,
        } if encounter else None,
        "eligibility": serialize_officer_eligibility(eligibility),
        "charges": charges,
        "summary": {
            "charge_count": len(charges),
            "pending_count": len(pending),
            "gross_amount": float_value(
                sum(row["amount"] for row in charges)
            ),
            "permitted_sponsor_total": float_value(
                sum(row["permitted_sponsor_amount"] for row in pending)
            ),
            "authorized_sponsor_total": float_value(
                account.amount_sponsor_authorized if account else 0.0
            ),
            "patient_responsibility_total": float_value(
                account.amount_patient_responsibility if account else 0.0
            ),
            "review_status": resolve_review_status(encounter, account, charges),
        },
        "capabilities": insurance_credit_capability_flags(env),
    }


REVIEW_STATUSES = (
    "no_payer",
    "review_required",
    "partially_authorized",
    "authorized",
    "not_covered",
    "blocked",
)


def resolve_review_status(encounter, account, charges):
    """Derived, never stored. One decision, made on the server.

    Composed from facts that already exist -- the selected eligibility, the
    charge-level responsibility states and the evaluator -- so it cannot drift
    out of sync with any of them the way a persisted status would.
    """
    if not encounter or not account:
        return "no_payer"
    if not encounter.patient_payer_id:
        return "no_payer"
    if not encounter.patient_payer_id.sudo().is_valid_today:
        # An expired or suspended member cannot be evaluated, let alone
        # authorized. Naming it 'blocked' sends it to the right desk.
        return "blocked"
    if not charges:
        return "no_payer"

    pending = [row for row in charges if row["needs_decision"]]
    if not pending:
        decided = [row for row in charges if not row["needs_decision"]]
        if decided and all(
            row["authorized_sponsor_amount"] <= 0.0 for row in decided
        ):
            # Every decision was a denial or a zero-coverage outcome.
            return "not_covered"
        return "authorized"
    if len(pending) < len(charges):
        return "partially_authorized"
    return "review_required"


def serialize_officer_worklist_row(env, appointment, stage):
    """One queue row. Identity plus the single number that matters.

    ``stage`` is passed IN, resolved under sudo() by the controller: the derived
    stage traverses evaluation_ids into hospital.patient.evaluation, which the
    Insurance/Credit Officer holds no ACL on. Reading it as the calling user
    would raise, exactly as it does for the Cashier.
    """
    encounter = appointment.encounter_id
    account = encounter.billing_account_id if encounter else None
    eligibility = encounter.patient_payer_id if encounter else None
    patient = appointment.patient_id

    pending = 0
    permitted_total = 0.0
    if account:
        for charge in account.charge_line_ids:
            if charge_needs_decision(charge):
                pending += 1
                coverage = env["hospital.billing.engine"].sudo(
                ).evaluate_charge_coverage(charge, eligibility)
                permitted_total += coverage["permitted_sponsor_amount"]

    return {
        "appointment_id": appointment.id,
        "appointment_code": appointment.appointment_code,
        "appointment_date": datetime_value(appointment.appointment_date),
        "stage": stage,
        "patient": {
            "id": patient.id,
            "name": patient.name,
            "identification_code": patient.identification_code,
        },
        "payer_name": (
            eligibility.sudo().payer_id.display_name if eligibility else None
        ),
        "member_reference": (
            eligibility.sudo().member_reference if eligibility else None
        ),
        "pending_charge_count": pending,
        "permitted_sponsor_total": float_value(permitted_total),
        "authorized_sponsor_total": float_value(
            account.amount_sponsor_authorized if account else 0.0
        ),
        "patient_responsibility_total": float_value(
            account.amount_patient_responsibility if account else 0.0
        ),
        "review_status": "review_required" if pending else "authorized",
    }

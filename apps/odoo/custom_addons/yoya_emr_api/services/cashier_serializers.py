"""Cashier-safe serializers for operational payment intake.

Least privilege by construction, and deliberately NOT a reuse of
reception_serializers.serialize_visit_detail.

Reusing the reception payload here is exactly what produced confirmed
receipts sitting behind HTTP 403 responses: the payment succeeded, then the
RESPONSE raised AccessError on a clinical relation, the endpoint decorator
turned that into JSON, and Odoo committed the write anyway.

A Hospital Cashier holds read access to hospital.patient,
hospital.appointment, hospital.encounter, hospital.billing.account,
hospital.charge.line, hospital.billing.service, hospital.charge.receipt and
hospital.charge.receipt.allocation -- and to nothing clinical. Every field
read below is either a plain stored column on one of those models, or a
compute_sudo money field whose read group (OPERATIONAL_MONEY_READ, defined in
hospital_billing.charge_line) already includes the Cashier.

Deliberately NOT read here, and why:

  appointment.clinical_queue_stage   Non-stored compute. Traverses
                                     evaluation_ids into
                                     hospital.patient.evaluation, which the
                                     Cashier must not read. This is the field
                                     that caused the 403.
  encounter.danger_sign_ids          hospital.emergency.danger.sign.
  appointment.doctor_id,
  appointment.department_id,
  appointment.triage_destination_id,
  encounter.payer_id                 Serializing these reads display_name out
                                     of models the Cashier cannot read.
  receipt.accounting_move_id (m2o)   Only its truthiness is exposed. Reading
                                     display_name would require account.move.
  amount_invoiced / amount_credited /
  amount_applied_to_invoice /
  amount_refunded* / net_invoiced_amount /
  receivable_balance                 groups=ACCOUNTING_READ, which excludes
                                     the Cashier by design.

If a future cashier workspace needs a clinical field, that is a permission
decision to be taken explicitly -- not something to be smuggled in by
widening this serializer.
"""
from odoo.addons.hospital_billing.models.charge_line import ACCOUNTING_GROUPS

from .api_response import (
    date_value,
    datetime_value,
    float_value,
    m2o_value,
    selection_value,
)
from .reception_scope import may_record_payment


def serialize_cashier_patient(patient):
    """Identity only -- what a cashier needs to confirm who is paying."""
    return {
        "id": patient.id,
        "identification_code": patient.identification_code,
        "name": patient.name,
        "date_of_birth": date_value(patient.date_of_birth),
        # Stored compute on hospital.patient: a plain column read.
        "age": patient.age,
        "gender": selection_value(patient.gender),
        "phone": patient.phone or None,
        "mobile": patient.mobile or None,
    }


def serialize_cashier_appointment(appointment):
    """Visit identity only. No queue stage, no doctor, no department."""
    return {
        "id": appointment.id,
        "appointment_code": appointment.appointment_code,
        "appointment_date": datetime_value(appointment.appointment_date),
        "state": appointment.state,
        "visit_type": selection_value(appointment.visit_type),
    }


def serialize_cashier_encounter(encounter):
    if not encounter:
        return None
    return {
        "id": encounter.id,
        "name": encounter.name,
        "state": encounter.state,
        "encounter_type": selection_value(encounter.encounter_type),
        "payer_type": selection_value(encounter.payer_type),
    }


def serialize_cashier_charge_line(charge):
    """Operational money view of one charge. No accounting buckets.

    The responsibility keys are the OPERATIONAL split for this visit, not the
    payer's commercial position. They are safe here for the same reason
    `outstanding` is: the Cashier is inside OPERATIONAL_MONEY_READ, which is the
    read group on every one of these fields. Contract terms -- limit_amount,
    member_limit_amount, limit_scope, tariff_mode, payment_terms_days -- live
    behind PAYER_COMMERCIAL_READ and appear nowhere in this module.
    """
    return {
        "id": charge.id,
        "name": charge.name,
        "description": charge.description,
        "service": m2o_value(charge.service_id),
        "amount": float_value(charge.amount_estimated),
        "received": float_value(charge.amount_received),
        "outstanding": float_value(charge.amount_due_for_clearance),
        "charge_state": selection_value(charge.charge_state),
        "payment_state": selection_value(charge.payment_state),
        "operational_funding_state": selection_value(
            charge.operational_funding_state
        ),
        "sponsor_responsibility": float_value(charge.amount_sponsor_responsibility),
        "sponsor_authorized": float_value(charge.amount_sponsor_authorized),
        "patient_responsibility": float_value(charge.amount_patient_responsibility),
        "responsibility_state": selection_value(charge.responsibility_state),
    }


def serialize_financial_block(encounter, account):
    """THE canonical financial contract the future Cashier Desk consumes.

    One object, one source. Every figure is read from a stored server-side
    aggregate rather than assembled from parts, so the client never does
    money arithmetic and cannot arrive at a different total than the server.

    `responsibility_mode` is reported explicitly and is not optional: a client
    that does not know whether the split is authoritative cannot honestly label
    what it shows. Under 'off' and 'shadow' the split is present but
    `financial_clearance_state` still reflects legacy behaviour, and
    `responsibility_advisory` says so in one boolean.
    """
    mode = (encounter.company_id.sudo().payer_responsibility_mode or "off") if encounter else "off"
    if not account:
        return {
            "currency": None,
            "responsibility_mode": mode,
            "responsibility_advisory": mode != "enforce",
            "amount_estimated": 0.0,
            "patient_responsibility": 0.0,
            "sponsor_responsibility": 0.0,
            "sponsor_authorized": 0.0,
            "sponsor_outstanding": 0.0,
            "patient_paid": 0.0,
            "patient_outstanding": 0.0,
            "responsibility_state": None,
            "financial_clearance_state": None,
            "financially_cleared": False,
        }
    return {
        "currency": account.currency_id.name or None,
        "responsibility_mode": mode,
        # True while the split is recorded but NOT driving the cash gate, so the
        # UI can show it without implying the patient may pay the smaller figure.
        "responsibility_advisory": mode != "enforce",
        # The total being split. There is no separate 'responsibility_total':
        # amount_estimated IS it, and a second name for one number is how two
        # sources of truth begin.
        "amount_estimated": float_value(account.amount_estimated),
        "patient_responsibility": float_value(account.amount_patient_responsibility),
        "sponsor_responsibility": float_value(account.amount_sponsor_responsibility),
        "sponsor_authorized": float_value(account.amount_sponsor_authorized),
        # OPERATIONAL sponsor exposure, not an accounting receivable: no sponsor
        # invoice, claim or settlement exists yet, so nothing reduces it.
        "sponsor_outstanding": float_value(account.amount_sponsor_outstanding),
        # PATIENT CASH ONLY. amount_received is the sum of confirmed receipt
        # allocations and never contains a sponsor figure.
        "patient_paid": float_value(account.amount_received),
        "patient_outstanding": float_value(account.amount_patient_outstanding),
        "responsibility_state": selection_value(account.responsibility_state),
        "financial_clearance_state": selection_value(
            account.financial_clearance_state
        ),
        "financially_cleared": bool(
            encounter.reception_clearance_ok if encounter else False
        ),
    }


def serialize_cashier_account(account):
    """Account-level operational totals. Every field here is
    OPERATIONAL_MONEY_READ or ungrouped -- none is ACCOUNTING_READ."""
    if not account:
        return None
    return {
        "id": account.id,
        "name": account.name,
        "amount_estimated": float_value(account.amount_estimated),
        "amount_received": float_value(account.amount_received),
        "amount_due_for_clearance": float_value(account.amount_due_for_clearance),
        "amount_outstanding": float_value(account.amount_outstanding),
        "amount_prepayment_held": float_value(account.amount_prepayment_held),
        "amount_patient_credit": float_value(account.amount_patient_credit),
        "operational_funding_state": selection_value(
            account.operational_funding_state
        ),
        # Stored mirror, only relabelled by check_financial_clearance(persist=True).
        # Reported alongside the LIVE clearance below, never instead of it.
        "financial_clearance_state": selection_value(
            account.financial_clearance_state
        ),
    }


def serialize_cashier_allocation(allocation):
    return {
        "id": allocation.id,
        "charge_line_id": allocation.charge_line_id.id,
        "amount": float_value(allocation.amount),
    }


def serialize_cashier_receipt(receipt):
    """The receipt the cashier just created, plus its allocation breakdown.

    accounting/fiscal facts are reported as plain booleans so the cashier can
    see that intake did NOT post anything. accounting_move_id is exposed only
    as a boolean: its display_name lives in account.move.
    """
    accounting = {
        "posted": bool(receipt.accounting_posted),
        "fiscalized": bool(receipt.fiscalized),
        "has_accounting_move": bool(
            receipt.accounting_move_id
            if "accounting_move_id" in receipt._fields
            else False
        ),
    }
    allocations = [
        serialize_cashier_allocation(allocation)
        for allocation in receipt.allocation_ids
    ]
    return {
        "id": receipt.id,
        "name": receipt.name,
        "amount": float_value(receipt.amount),
        "payment_method": receipt.payment_method,
        "payment_reference": receipt.payment_reference or None,
        "note": receipt.note or None,
        "received_at": datetime_value(receipt.received_at),
        "received_by": m2o_value(receipt.received_by_id),
        "state": receipt.state,
        "intake_token": receipt.intake_token,
        "allocations": allocations,
        "allocated_total": float_value(sum(receipt.allocation_ids.mapped("amount"))),
        "accounting": accounting,
    }


def serialize_cashier_clearance(encounter):
    """LIVE encounter-wide clearance.

    Sourced from yoya_reception_bridge's reception_* fields, which are
    compute_sudo=True in their own module and call the billing engine with
    persist=False and no charge filter. They are financial, not clinical, and
    their compute_sudo is pre-existing -- nothing is elevated from here.
    """
    if not encounter:
        return {
            "required": 0.0,
            "received": 0.0,
            "outstanding": 0.0,
            "ok": False,
            "state": None,
            "message": None,
        }
    return {
        "required": float_value(encounter.reception_required_amount),
        "received": float_value(encounter.reception_paid_amount),
        "outstanding": float_value(encounter.reception_outstanding_amount),
        "ok": bool(encounter.reception_clearance_ok),
        "state": selection_value(encounter.reception_clearance_state),
        "message": encounter.reception_clearance_message or None,
    }


def cashier_permitted_actions(env):
    """What THIS user may do next. Honest about the accounting boundary."""
    may_post_accounting = any(
        env.user.has_group(group) for group in ACCOUNTING_GROUPS
    )
    return {
        "record_payment": may_record_payment(env),
        # Posting a confirmed receipt to accounting is an accountant act. A
        # cashier sees False here and the server enforces the same boundary.
        "post_receipt_accounting": may_post_accounting,
    }


# ----------------------------------------------------------------------
# THE CASHIER LANE.
#
# Which of four operational situations a visit is in, decided SERVER-SIDE from
# the authoritative figures. The desk renders the lane; it never derives it.
#
# 'blocked' is the one that matters: a sponsor share exists but nobody has
# authorized it, so the patient must not be asked for the full amount and the
# Cashier cannot fix it either. The visit stays VISIBLE -- hiding it would
# strand a patient invisibly between two desks -- but the payment form is
# closed and the desk names the role that can unblock it.
# ----------------------------------------------------------------------
CASHIER_LANES = ("collect", "partial", "blocked", "cleared")


def resolve_collectability(encounter, account):
    """MAY the cashier take money for this visit, and if not, why not.

    SERVER-AUTHORITATIVE AND COMPLETE. The desk renders this verdict; it never
    infers one from amounts. Deriving collectability client-side from figures
    alone cannot distinguish "nothing to pay because it is settled" from
    "nothing to pay because nobody authorized the sponsor yet" -- two states
    that look identical in numbers and mean opposite things at the window.

    Order is deterministic and fails closed.
    """
    if not encounter or not account:
        return {
            "collectable": False,
            "lane": "cleared",
            "reason": "This visit has no billing account.",
            "reason_code": "no_billing_account",
        }

    # Emergency bypass is an independent authorized route. Care proceeds without
    # payment being settled; it is not a collection job and never enters the
    # normal payment lane.
    if encounter.emergency_bypass:
        return {
            "collectable": False,
            "lane": "cleared",
            "reason": "Emergency bypass authorized. No payment is required before care.",
            "reason_code": "emergency_bypass",
        }

    # Live clearance, never the stored mirror. Covers self-pay paid, mixed
    # settled, and fully sponsored (sponsor_cleared) in one test.
    if encounter.reception_clearance_ok:
        return {
            "collectable": False,
            "lane": "cleared",
            "reason": "This visit is financially cleared. Nothing is due from the patient.",
            "reason_code": "already_cleared",
        }

    # A sponsor share exists but nobody has authorized it. Only reachable under
    # 'enforce': in off/shadow the responsibility engine gates nothing, so a
    # proposed share never blocks and the visit is an ordinary collection.
    mode = encounter.company_id.sudo().payer_responsibility_mode or "off"
    if mode == "enforce" and account.responsibility_state == "proposed":
        return {
            "collectable": False,
            "lane": "blocked",
            "reason": (
                "A sponsor share has been recorded but not authorized. The "
                "patient must not be charged the full amount until an "
                "Insurance/Credit Officer authorizes it."
            ),
            "reason_code": "sponsor_authorization_pending",
        }

    if account.amount_patient_outstanding <= 0.0:
        # Not cleared, yet nothing outstanding: some other charge-level gate is
        # holding the visit. Say so rather than offering a zero collection.
        return {
            "collectable": False,
            "lane": "blocked",
            "reason": "Nothing is currently collectable from the patient on this visit.",
            "reason_code": "nothing_outstanding",
        }

    partial = account.amount_received > 0.0
    return {
        "collectable": True,
        "lane": "partial" if partial else "collect",
        "reason": None,
        "reason_code": None,
    }


def resolve_cashier_lane(encounter, account):
    """Lane only, for queue rows that do not need the full verdict."""
    return resolve_collectability(encounter, account)["lane"]


def serialize_cashier_worklist_row(appointment, stage):
    """One queue row. Identity, lane and the single number that matters.

    ``stage`` is passed IN, already resolved under sudo() by the controller.
    It is not read from the appointment here: front_desk_stage is a non-stored
    compute that traverses evaluation_ids into hospital.patient.evaluation,
    which a Hospital Cashier holds no ACL on. Reading it as the cashier raises
    AccessError -- the same failure mode this module's header documents for
    clinical_queue_stage.
    """
    encounter = appointment.encounter_id
    account = encounter.billing_account_id if encounter else None
    patient = appointment.patient_id
    return {
        "appointment_id": appointment.id,
        "appointment_code": appointment.appointment_code,
        "appointment_date": datetime_value(appointment.appointment_date),
        "stage": stage,
        "lane": resolve_cashier_lane(encounter, account),
        "patient": {
            "id": patient.id,
            "name": patient.name,
            "identification_code": patient.identification_code,
        },
        "encounter_name": encounter.name if encounter else None,
        # The one figure a cashier scans the queue for.
        "patient_outstanding": float_value(
            account.amount_patient_outstanding if account else 0.0
        ),
        "patient_paid": float_value(account.amount_received if account else 0.0),
        "responsibility_state": selection_value(
            account.responsibility_state if account else False
        ),
        "financially_cleared": bool(
            encounter.reception_clearance_ok if encounter else False
        ),
    }


def serialize_cashier_visit_detail(env, appointment):
    """THE canonical cashier visit payload.

    Returned by the detail endpoint AND embedded in every payment response, so
    the desk has exactly one financial shape to render and a post-payment
    refresh is a re-render rather than a second mapping.
    """
    encounter = appointment.encounter_id
    account = encounter.billing_account_id if encounter else None
    return {
        "appointment": serialize_cashier_appointment(appointment),
        "patient": serialize_cashier_patient(appointment.patient_id),
        "encounter": serialize_cashier_encounter(encounter),
        "billing_account": serialize_cashier_account(account),
        "financial": serialize_financial_block(encounter, account),
        # The verdict, not the ingredients. See resolve_collectability.
        "collectability": resolve_collectability(encounter, account),
        "charge_lines": [
            serialize_cashier_charge_line(line)
            for line in (account.charge_line_ids if account else [])
        ],
        "clearance": serialize_cashier_clearance(encounter),
        "permitted_actions": cashier_permitted_actions(env),
    }


def serialize_cashier_payment_result(env, appointment, receipt):
    """The complete cashier-safe payment response.

    Reads nothing a Hospital Cashier cannot read. Call it inside the same
    savepoint as the payment mutation so that a failure here rolls the money
    back instead of committing it behind an error response.

    It is the canonical visit payload PLUS the receipt just created, so the
    client can replace its whole visit state from one response.
    """
    payload = serialize_cashier_visit_detail(env, appointment)
    payload["receipt"] = serialize_cashier_receipt(receipt)
    return payload

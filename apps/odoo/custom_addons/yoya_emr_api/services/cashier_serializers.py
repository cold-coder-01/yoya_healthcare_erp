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
    """Operational money view of one charge. No accounting buckets."""
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


def serialize_cashier_payment_result(env, appointment, receipt):
    """The complete cashier-safe payment response.

    Reads nothing a Hospital Cashier cannot read. Call it inside the same
    savepoint as the payment mutation so that a failure here rolls the money
    back instead of committing it behind an error response.
    """
    encounter = appointment.encounter_id
    account = encounter.billing_account_id if encounter else None
    return {
        "appointment": serialize_cashier_appointment(appointment),
        "patient": serialize_cashier_patient(appointment.patient_id),
        "encounter": serialize_cashier_encounter(encounter),
        "billing_account": serialize_cashier_account(account),
        "receipt": serialize_cashier_receipt(receipt),
        "charge_lines": [
            serialize_cashier_charge_line(line)
            for line in (account.charge_line_ids if account else [])
        ],
        "clearance": serialize_cashier_clearance(encounter),
        "permitted_actions": cashier_permitted_actions(env),
    }

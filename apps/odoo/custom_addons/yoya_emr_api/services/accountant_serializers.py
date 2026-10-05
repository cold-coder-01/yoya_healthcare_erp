"""Accountant Desk payloads.

Finance identity, the stay's workflow FACTS (state, dates, location,
responsible physician) and the server's delivered-basis settlement -- the same
serialize_inpatient_settlement() block the Cashier and the Admissions Desk
read, so the three desks can never show different figures. No diagnosis, no
admission reason, no discharge summary, no clinical result.

PAYMENT IN and REFUND OUT are separate lists: a refund is never shown as a
negative charge or a negative receipt.
"""
from .api_response import datetime_value, float_value
from .cashier_serializers import _inpatient_identity, serialize_inpatient_settlement

ACCOUNTING_LANE_LABELS = {
    "refund_due": "Refund due",
    "needs_review": "Needs review",
    "refunded": "Refunded",
}

# Stated plainly, never implied: an operational refund is recorded, its GL
# cash-out journal is not posted by this system yet.
REFUND_ACCOUNTING_NOTE = (
    "Operational refund recorded. Accounting journal posting pending."
)


def _identity(facts):
    payload = _inpatient_identity(facts)
    payload["admission"]["physician"] = facts["physician"]
    return payload


def serialize_accountant_row(facts):
    summary = facts["summary"]
    row = _identity(facts)
    row.update({
        "lane": facts["accounting_lane"],
        "lane_label": ACCOUNTING_LANE_LABELS[facts["accounting_lane"]],
        "financial_state": summary["financial_state"],
        "currency": facts["currency"],
        "refundable_balance": float_value(summary["refundable_balance"]),
        "remaining_due": float_value(summary["remaining_due"]),
        "refunded_total": float_value(facts["refunded_total"]),
        "last_refund_at": datetime_value(facts["last_refund_at"]),
    })
    return row


def _payment_in(row):
    return {
        "id": row["id"],
        "direction": "in",
        "kind": row["kind"],
        "reference": row["reference"],
        "amount": float_value(row["amount"]),
        "payment_method": row["payment_method"],
        "payment_reference": row["payment_reference"],
        "actor": row["actor"],
        "at": datetime_value(row["at"]),
        "state": row["state"],
        "accounting_posted": row["accounting_posted"],
    }


def _refund_out(row):
    return {
        "id": row["id"],
        "direction": "out",
        "kind": "refund",
        "reference": row["reference"],
        "amount": float_value(row["amount"]),
        "reason": row["reason"],
        "refundable_before": (
            float_value(row["refundable_before"]) if row["refundable_before"] is not None else None
        ),
        "refundable_after": (
            float_value(row["refundable_after"]) if row["refundable_after"] is not None else None
        ),
        "actor": row["actor"],
        "at": datetime_value(row["at"]),
        "state": row["state"],
        "accounting_posted": False,
        "accounting_note": REFUND_ACCOUNTING_NOTE,
    }


def serialize_accountant_refund_verdict(env, facts, may_refund):
    """Whether a refund may be recorded now, and up to how much -- the
    server's refundable balance, never the browser's arithmetic."""
    lane = facts["accounting_lane"]
    amount = float_value(facts["summary"]["refundable_balance"])
    if lane != "refund_due":
        reason = {
            "needs_review": "The settlement needs review before anything is refunded.",
            "refunded": "The refund has been recorded. Nothing remains refundable.",
        }.get(lane, "Nothing is refundable on this account.")
        return {"refund_due": False, "may_record": False, "max_amount": 0.0, "reason": reason}
    if not may_refund:
        return {
            "refund_due": True, "may_record": False, "max_amount": amount,
            "reason": "Refunds are recorded by the Accountant, Hospital Manager or System Administrator.",
        }
    return {"refund_due": True, "may_record": True, "max_amount": amount, "reason": None}


def serialize_accountant_detail(env, facts, review_messages, may_refund):
    payload = _identity(facts)
    payload.update({
        "lane": facts["accounting_lane"],
        "lane_label": ACCOUNTING_LANE_LABELS.get(facts["accounting_lane"]),
        "currency": facts["currency"],
        "settlement": serialize_inpatient_settlement(facts["summary"], review_messages),
        "payments_in": [_payment_in(row) for row in facts["payments_in"]],
        "refunds_out": [_refund_out(row) for row in facts["refunds"]],
        "refunded_total": float_value(facts["refunded_total"]),
        "refund": serialize_accountant_refund_verdict(env, facts, may_refund),
    })
    return payload

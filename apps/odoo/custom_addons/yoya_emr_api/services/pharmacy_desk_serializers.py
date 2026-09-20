"""Pharmacy Desk payloads: the counter's own view of its work. READ ONLY.

WHY THIS IS NOT medication_serializers.py
-----------------------------------------
That is the DOCTOR's contract: one coarse status per prescription, derived from
the dispense state. The counter asks different questions -- what is waiting to
be prepared, what is waiting on the cashier, what can be handed over now, what
is stuck and why -- and it must not trust a state alone to answer them.

LANES ARE DERIVED FROM FACTS, NOT FROM `state`
----------------------------------------------
The UAT database holds dispenses whose state contradicts their evidence: a
`dispensed` record with no delivered or consumed quantity, a `partial` record
under a prescription the header already calls dispensed. So pharmacy_desk_lane()
reads the state AND the per-line quantities (prescribed, intended, billing
delivered, inventory consumed), the billing facts hospital_billing owns and the
stock facts hospital_inventory owns, and puts every record that does not add up
in `anomaly` -- never quietly in `completed`.

ONE function decides the lane of the row, the lane filter and the lane counts,
so the three can never disagree. Nothing derived is written back to Odoo, and no
record is repaired.

THE PRESCRIPTION HEADER IS NOT DELIVERY TRUTH. Nothing in the runtime moves
hospital.prescription.state past `confirmed` (see medication_serializers), so a
confirmed prescription with a fully dispensed dispense is the ordinary finished
shape and is `completed`. What IS contradictory -- a header already `dispensed`
while its dispense is not, or a cancelled/draft header under a live dispense --
is an anomaly.

CONFIDENTIALITY, STATED ONCE AND CHECKABLE
------------------------------------------
Nothing below reads hospital.charge.line, hospital.charge.receipt,
hospital.billing.account, any invoice, fiscal or accounting model, any
amount-bearing field of the dispense (unified_amount_*, amount_payable) or of
its lines (unit_price, price_subtotal), or hospital.audit.log. Billing crosses
this boundary ONLY as booleans returned by hospital_billing's
_pharmacy_desk_billing_facts(): whether the pending increment is financially
blocked, whether each medicine is billable, whether a charge exists and covers
the intent. No clearance message is read -- every one hospital_billing writes
names amounts. The payload is identical for every role that can open the desk.

Stock crosses as booleans too (_pharmacy_desk_stock_facts): mapped or not,
sufficient or not. No batch, lot, expiry or on-hand figure is serialized.

PROVENANCE. `pharmacist_id` is never serialized. Before Pharmacy Slice 0 the
composition stamped the PRESCRIBING DOCTOR there, and it has never recorded who
validated a delivery, so presenting it as "dispensed by" would state something
the record does not know.
"""
import logging

from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY
from odoo.tools import float_compare

from .api_response import date_value, datetime_value

_logger = logging.getLogger(__name__)

QTY_DIGITS = 3

# ---------------------------------------------------------------------------
# The lane vocabulary. NONE OF THESE ARE DATABASE VALUES.
# ---------------------------------------------------------------------------
PHARMACY_DESK_LANE_LABELS = {
    "awaiting_preparation": "Awaiting preparation",
    "awaiting_clearance": "Awaiting clearance",
    "ready_to_validate": "Ready to validate",
    "partially_supplied": "Partially supplied",
    "blocked": "Blocked",
    "anomaly": "Needs review",
    "completed": "Completed",
    "cancelled": "Cancelled",
}

# The default queue, in the order the work flows. Blocked and anomaly are part
# of it on purpose: a stuck record hidden behind a filter stays stuck.
PHARMACY_DESK_ACTIVE_LANES = (
    "awaiting_preparation",
    "awaiting_clearance",
    "ready_to_validate",
    "partially_supplied",
    "blocked",
    "anomaly",
)

# Every lane a caller may ask for. Anything else is a 400.
PHARMACY_DESK_LANES = PHARMACY_DESK_ACTIVE_LANES + ("completed", "cancelled")

KNOWN_DISPENSE_STATES = ("draft", "ready", "partial", "dispensed", "cancelled")

# Operational blockers: the record is consistent, but the next step cannot run
# until configuration or stock changes. A key the client can style, and a fixed
# sentence so the client never invents wording. NO AMOUNTS, ever.
BLOCK_MESSAGES = {
    "billing_context_missing": (
        "This dispense has no visit or encounter to bill against, so it cannot "
        "be billed or validated. Ask a hospital manager to review it."
    ),
    "billing_mapping_missing": (
        "At least one medicine is not mapped to an active pharmacy billing "
        "service, so it cannot be billed. Ask a hospital manager to correct the "
        "medicine configuration."
    ),
    "inventory_mapping_missing": (
        "At least one medicine is not linked to a valid pharmacy inventory item, "
        "so its stock cannot be consumed. Ask a hospital manager to correct the "
        "medicine configuration."
    ),
    "pharmacy_store_missing": (
        "No active Pharmacy Store location is configured, so no stock can be "
        "consumed. Ask a hospital manager to configure it."
    ),
    "stock_insufficient": (
        "The Pharmacy Store does not hold enough usable stock for the quantity "
        "intended for at least one medicine."
    ),
}

# Contradictory or malformed records. Shown for review; never classified as
# finished and never repaired by this desk.
ANOMALY_MESSAGES = {
    "unknown_state": "This dispense is in a state the Pharmacy Desk does not recognise.",
    "no_lines": "This dispense has no medicine lines.",
    "no_prescription": "This dispense is not linked to a prescription.",
    "identity_mismatch": "This dispense and its prescription name different patients.",
    "prescription_cancelled": "The prescription behind this dispense has been cancelled, but the dispense has not.",
    "prescription_not_confirmed": "The prescription behind this dispense is not confirmed.",
    "prescription_header_conflict": (
        "The prescription is marked dispensed, but this dispense has not been "
        "fully dispensed."
    ),
    "invalid_prescribed_quantity": "At least one medicine line has no prescribed quantity.",
    "intended_out_of_range": (
        "At least one medicine line intends a quantity below zero or above what "
        "was prescribed."
    ),
    "delivery_exceeds_intended": (
        "At least one medicine line records more delivered or consumed than was "
        "intended."
    ),
    "delivery_evidence_mismatch": (
        "The billing and stock records of at least one medicine line disagree "
        "about how much was handed over."
    ),
    "delivery_charge_missing": (
        "At least one medicine line records a delivery without the billing "
        "record that should accompany it."
    ),
    "delivered_on_draft": "A draft dispense already records delivered medication.",
    "delivered_on_ready": (
        "A dispense that has not been validated already records delivered "
        "medication."
    ),
    "ready_without_intent": "This dispense is marked ready but intends to hand over nothing.",
    "partial_without_delivery": (
        "This dispense is marked partially dispensed, but no delivered medication "
        "is recorded."
    ),
    "partial_fully_delivered": (
        "Every medicine on this dispense has been fully delivered, but it is "
        "still marked partially dispensed."
    ),
    "dispensed_unreconciled": (
        "This dispense is marked dispensed, but its delivered and consumed "
        "quantities do not show the full prescription handed over."
    ),
    "cancelled_with_delivery": "This dispense is cancelled but records delivered medication.",
    "unreadable_record": "This dispense could not be read completely.",
}
REVIEW_SUFFIX = " Review is required before workflow actions can continue."

PRIORITY_LABELS = {"routine": "Routine", "urgent": "Urgent", "emergency": "Emergency"}
PRIORITY_RANK = {"emergency": 0, "urgent": 1, "routine": 2}


def lane_label(lane):
    return PHARMACY_DESK_LANE_LABELS.get(lane, lane)


def reason_message(lane, reason):
    if not reason:
        return None
    if lane == "anomaly":
        return ANOMALY_MESSAGES.get(reason, ANOMALY_MESSAGES["unreadable_record"]) + REVIEW_SUFFIX
    return BLOCK_MESSAGES.get(reason)


def _gt(a, b):
    return float_compare(a or 0.0, b or 0.0, precision_digits=QTY_DIGITS) > 0


def _eq(a, b):
    return float_compare(a or 0.0, b or 0.0, precision_digits=QTY_DIGITS) == 0


def _selection_label(record, field_name):
    value = record[field_name]
    if not value:
        return None
    labels = dict(record._fields[field_name]._description_selection(record.env))
    return labels.get(value, value)


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------
def dispense_facts(dispense):
    """Everything the lane needs, read once. Booleans and quantities only.

    Billing and stock facts are ASKED OF THE MODULES THAT OWN THEM. If either
    module is absent the corresponding facts read as "not configured", which
    routes the record to `blocked` rather than to a lane that would promise it
    can proceed.
    """
    if hasattr(dispense, "_pharmacy_desk_billing_facts"):
        billing = dispense._pharmacy_desk_billing_facts()
    else:
        billing = {"unified": False, "billing_context": False, "billing_blocked": False, "lines": {}}
    if hasattr(dispense, "_pharmacy_desk_stock_facts"):
        stock = dispense._pharmacy_desk_stock_facts()
    else:
        stock = {"store_configured": False, "lines": {}}

    lines = []
    for line in dispense.line_ids.sorted(key=lambda l: (l.sequence, l.id)):
        prescribed = line.prescribed_quantity or 0.0
        intended = line.dispensed_quantity or 0.0
        billed = line.billing_delivered_quantity if "billing_delivered_quantity" in line._fields else 0.0
        consumed = line.inventory_consumed_quantity if "inventory_consumed_quantity" in line._fields else 0.0
        billed = billed or 0.0
        consumed = consumed or 0.0
        # Consumption is the stock truth of what left the shelf; in unified
        # billing the billing high-water must agree with it (checked in the lane).
        delivered = consumed
        b_line = billing["lines"].get(line.id, {})
        s_line = stock["lines"].get(line.id, {})
        lines.append({
            "line": line,
            "prescribed": prescribed,
            "intended": intended,
            "billed": billed,
            "consumed": consumed,
            "delivered": delivered,
            "remaining": max(prescribed - delivered, 0.0),
            "pending": max(intended - delivered, 0.0),
            "billing_mapped": bool(b_line.get("billing_mapped")),
            "charge_linked": bool(b_line.get("charge_linked")),
            "charge_covers_intent": bool(b_line.get("charge_covers_intent")),
            "inventory_mapped": bool(s_line.get("inventory_mapped")),
            "stock_basis": s_line.get("stock_basis"),
            "stock_sufficient": s_line.get("stock_sufficient"),
        })
    return {
        "unified": bool(billing.get("unified")),
        "billing_context": bool(billing.get("billing_context")),
        "billing_blocked": bool(billing.get("billing_blocked")),
        "store_configured": bool(stock.get("store_configured")),
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# The lane
# ---------------------------------------------------------------------------
def _blocker(facts, scope, check_stock):
    """The first operational blocker over the lines in `scope`, or None."""
    if not scope:
        return None
    if not facts["billing_context"]:
        return "billing_context_missing"
    if any(not item["billing_mapped"] for item in scope):
        return "billing_mapping_missing"
    if any(not item["inventory_mapped"] for item in scope):
        return "inventory_mapping_missing"
    if not facts["store_configured"]:
        return "pharmacy_store_missing"
    if check_stock and any(item["stock_sufficient"] is False for item in scope):
        return "stock_insufficient"
    return None


def pharmacy_desk_lane(dispense, facts):
    """(lane, reason) for one dispense. THE one lane definition.

    Mutually exclusive by construction: every path returns exactly once, and
    the order of the checks is the precedence -- cancelled, then record
    integrity (anomaly), then the state's own evidence rules, then operational
    blockers, then clearance.
    """
    state = dispense.state
    lines = facts["lines"]
    any_delivered = any(_gt(i["delivered"], 0) or _gt(i["billed"], 0) for i in lines)

    if state == "cancelled":
        return ("anomaly", "cancelled_with_delivery") if any_delivered else ("cancelled", None)
    if state not in KNOWN_DISPENSE_STATES:
        return "anomaly", "unknown_state"
    if not lines:
        return "anomaly", "no_lines"

    prescription = dispense.prescription_id
    if not prescription:
        return "anomaly", "no_prescription"
    if prescription.patient_id != dispense.patient_id:
        return "anomaly", "identity_mismatch"
    if prescription.state == "cancelled":
        return "anomaly", "prescription_cancelled"
    if prescription.state == "draft":
        return "anomaly", "prescription_not_confirmed"
    if prescription.state == "dispensed" and state != "dispensed":
        return "anomaly", "prescription_header_conflict"

    for item in lines:
        if not _gt(item["prescribed"], 0):
            return "anomaly", "invalid_prescribed_quantity"
        if _gt(0, item["intended"]) or _gt(item["intended"], item["prescribed"]):
            return "anomaly", "intended_out_of_range"
        if _gt(item["billed"], item["intended"]) or _gt(item["consumed"], item["intended"]):
            return "anomaly", "delivery_exceeds_intended"
        if facts["unified"]:
            if not _eq(item["billed"], item["consumed"]):
                return "anomaly", "delivery_evidence_mismatch"
            if _gt(item["billed"], 0) and not item["charge_linked"]:
                return "anomaly", "delivery_charge_missing"
        elif _gt(item["billed"], 0):
            return "anomaly", "delivery_evidence_mismatch"

    pending = [i for i in lines if _gt(i["pending"], 0)]

    if state == "draft":
        if any_delivered:
            return "anomaly", "delivered_on_draft"
        # Stock is NOT a blocker here: the pharmacist decides how much to
        # prepare, and partial supply is the routine answer to a short shelf.
        remaining = [i for i in lines if _gt(i["remaining"], 0)]
        blocker = _blocker(facts, remaining, check_stock=False)
        return ("blocked", blocker) if blocker else ("awaiting_preparation", None)

    if state == "ready":
        if any_delivered:
            return "anomaly", "delivered_on_ready"
        if not pending:
            return "anomaly", "ready_without_intent"
    elif state == "partial":
        if not any_delivered:
            return "anomaly", "partial_without_delivery"
        if all(not _gt(i["remaining"], 0) for i in lines):
            return "anomaly", "partial_fully_delivered"
        if not pending:
            return "partially_supplied", None
    elif state == "dispensed":
        if any(not _eq(i["delivered"], i["prescribed"]) or not _eq(i["intended"], i["prescribed"]) for i in lines):
            return "anomaly", "dispensed_unreconciled"
        return "completed", None

    # ready, or partial with a pending increment.
    blocker = _blocker(facts, pending, check_stock=True)
    if blocker:
        return "blocked", blocker
    if facts["billing_blocked"]:
        return "awaiting_clearance", None
    return "ready_to_validate", None


def classify(dispense):
    """(lane, reason, facts) -- NEVER RAISES for a malformed record.

    A record whose facts cannot be read (a dangling reference, a related row
    the caller's rules hide) is shown as `anomaly/unreadable_record` rather than
    taking the whole queue down. Concurrency errors are re-raised so Odoo's
    retry can replay the request.
    """
    try:
        facts = dispense_facts(dispense)
        lane, reason = pharmacy_desk_lane(dispense, facts)
        return lane, reason, facts
    except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
        raise
    except Exception:  # noqa: BLE001 -- mapped to a fixed anomaly code
        _logger.warning(
            "Pharmacy desk could not classify dispense id=%s", dispense.id, exc_info=True
        )
        return "anomaly", "unreadable_record", None


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def _safe(read, default=None):
    try:
        return read()
    except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
        raise
    except Exception:  # noqa: BLE001 -- an unreadable relation renders as absent
        return default


def serialize_patient_identity(patient):
    """Identity only: name, chart number, age and sex. Nothing else."""
    if not patient:
        return None
    return {
        "id": patient.id,
        "name": patient.name,
        "mrn": patient.identification_code or None,
        "age": patient.age or None,
        "gender": patient.gender or None,
    }


def serialize_prescription_summary(prescription):
    if not prescription:
        return None
    return {
        "id": prescription.id,
        "code": prescription.name,
        "state": prescription.state,
        "state_label": _selection_label(prescription, "state"),
        "date": date_value(prescription.prescription_date),
    }


def serialize_prescriber(dispense):
    """The prescribing doctor: the PRESCRIPTION's physician, the clinical
    authority, falling back to the copy on the dispense. Name only."""
    doctor = dispense.prescription_id.physician_id or dispense.physician_id
    if not doctor:
        return None
    return {"id": doctor.id, "name": doctor.name}


def serialize_medicine(medicine):
    if not medicine:
        return None
    return {
        "id": medicine.id,
        "name": medicine.name,
        "code": medicine.code or None,
        "strength": medicine.strength or None,
        "dosage_form": medicine.dosage_form or None,
        "dosage_form_label": _selection_label(medicine, "dosage_form"),
    }


def serialize_line(item):
    """One medicine line as the counter needs it. Quantities and verdicts only."""
    line = item["line"]
    return {
        "id": line.id,
        "medicine": _safe(lambda: serialize_medicine(line.medicine_id)),
        "dosage": line.dosage or None,
        "frequency": line.frequency or None,
        "duration": line.duration or None,
        "route": line.route or None,
        "instruction": (line.instruction or "").strip() or None,
        "prescribed_quantity": item["prescribed"],
        # The pharmacist's CUMULATIVE intended quantity.
        "intended_quantity": item["intended"],
        # billing_delivered_quantity: a QUANTITY whose delivery billing recorded.
        "delivered_quantity": item["billed"],
        "consumed_quantity": item["consumed"],
        "remaining_quantity": item["remaining"],
        "pending_increment": item["pending"],
        # The least cumulative intent the model will accept: what has already
        # been supplied. Bounds for the Prepare input; the model re-checks.
        "minimum_intended_quantity": max(item["billed"], item["consumed"]),
        "billing_mapped": item["billing_mapped"],
        "charge_linked": item["charge_linked"],
        "inventory_mapped": item["inventory_mapped"],
        "stock_basis": item["stock_basis"],
        "stock_sufficient": item["stock_sufficient"],
    }


# ---------------------------------------------------------------------------
# Rows and detail
# ---------------------------------------------------------------------------
# The lanes from which the next preparation may be made. `blocked` joins them
# only for stock and store: the pharmacist's answer to a short shelf IS a
# smaller preparation. A mapping or billing-context block is not fixable at the
# counter, and preparing would bill a medicine that cannot be supplied.
PREPARE_LANES = (
    "awaiting_preparation",
    "awaiting_clearance",
    "ready_to_validate",
    "partially_supplied",
)
PREPARE_BLOCK_REASONS = ("stock_insufficient", "pharmacy_store_missing")


def record_capabilities(dispense, lane, reason, facts, may_mutate):
    """What the desk may attempt on THIS record. Derived here, never in the
    browser, from the same classification the lane comes from. The model
    re-checks everything under its locks; these only decide what is OFFERED."""
    if not may_mutate or not facts:
        return {"can_prepare": False, "can_validate": False}
    remaining = any(_gt(item["remaining"], 0) for item in facts["lines"])
    can_prepare = (
        dispense.state in ("draft", "ready", "partial")
        and remaining
        and (lane in PREPARE_LANES or (lane == "blocked" and reason in PREPARE_BLOCK_REASONS))
    )
    return {"can_prepare": bool(can_prepare), "can_validate": lane == "ready_to_validate"}


def serialize_queue_row(dispense, classified=None, may_mutate=False):
    lane, reason, facts = classified or classify(dispense)
    lines = facts["lines"] if facts else []
    payload = {
        "id": dispense.id,
        "dispense_code": dispense.name,
        # The AUTHORITATIVE value travels next to the derived one, always.
        "state": dispense.state,
        "state_label": _safe(lambda: _selection_label(dispense, "state")),
        "lane": lane,
        "lane_label": lane_label(lane),
        "reason": reason,
        "reason_message": reason_message(lane, reason),
        "priority": dispense.priority or "routine",
        "priority_label": PRIORITY_LABELS.get(dispense.priority, "Routine"),
        "dispense_date": datetime_value(dispense.dispense_date),
        "patient": _safe(lambda: serialize_patient_identity(dispense.patient_id)),
        "prescription": _safe(lambda: serialize_prescription_summary(dispense.prescription_id)),
        "prescriber": _safe(lambda: serialize_prescriber(dispense)),
        # A BOOLEAN VERDICT. Never an amount, never a payer, never a message.
        "billing_blocked": bool(facts and facts["billing_blocked"]),
        "stock_short": any(i["stock_sufficient"] is False for i in lines),
        "line_count": len(lines),
        "lines_complete": sum(1 for i in lines if not _gt(i["remaining"], 0)),
        "medicines_summary": _safe(
            lambda: " · ".join(i["line"].medicine_id.name for i in lines) or None
        ),
        "workflow_revision": dispense.workflow_revision or 0,
    }
    payload.update(record_capabilities(dispense, lane, reason, facts, may_mutate))
    return payload


def serialize_dispense_detail(dispense, may_mutate=False):
    """One dispense in full: a SUPERSET of the queue row."""
    classified = classify(dispense)
    payload = serialize_queue_row(dispense, classified, may_mutate)
    facts = classified[2]
    payload.update({
        "notes": _safe(lambda: (dispense.notes or "").strip() or None),
        "ordered_from_consultation": _safe(
            lambda: bool("consultation_id" in dispense.prescription_id._fields
                         and dispense.prescription_id.consultation_id),
            False,
        ),
        "unified_billing": bool(facts and facts["unified"]),
        "pharmacy_store_configured": bool(facts and facts["store_configured"]),
        "lines": [serialize_line(item) for item in facts["lines"]] if facts else [],
    })
    return payload


def serialize_worklist(rows, summary, filters, meta, capabilities):
    return {
        "rows": rows,
        "summary": summary,
        "filters": filters,
        "meta": meta,
        "capabilities": capabilities,
    }


def serialize_session(env, roles, capabilities):
    user = env.user
    return {
        "user": {"id": user.id, "name": user.name},
        "company": {"id": user.company_id.id, "name": user.company_id.name} if user.company_id else None,
        "roles": roles,
        "capabilities": capabilities,
        # Stated in the payload so no client infers it from absent flags: true
        # only for a caller who may attempt neither desk action.
        "read_only": not (
            capabilities.get("prepare_dispense") or capabilities.get("validate_dispense")
        ),
    }

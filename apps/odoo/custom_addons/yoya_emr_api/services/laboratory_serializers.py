"""Doctor laboratory payloads: the clinical order, and nothing priced.

CONFIDENTIALITY, STATED ONCE AND CHECKABLE.
Nothing below reads hospital.charge.line, hospital.billing.account,
hospital.charge.receipt, hospital.payer or hospital.patient.payer. The ONE
billing-derived value that crosses this boundary is
`hospital.laboratory.request.billing_blocked` -- a BOOLEAN verdict, computed by
hospital_billing, carrying no amount, no payer and no allocation. Its sibling
`billing_clearance_message` is deliberately NOT read: it is written for a
cashier and can name sums.

That is the same discipline doctor_serializers applies to the consultation
clearance verdict, and for the same reason: a clinician acts on "can this
proceed", never on "how much".

THE CATALOGUE IS CLINICAL ONLY. hospital.laboratory.test carries
`billing_service_id` once hospital_billing is installed, which is the mapping
that decides what a test costs. It is never serialized, and neither is anything
reachable through it.
"""
from .api_response import date_value, datetime_value, selection_value

# A doctor types three letters and wants the shortlist, not the whole
# catalogue. Anything larger is a scroll nobody reads and a query nobody
# intended, so the cap is enforced in SQL and the client cannot widen it.
CATALOGUE_DEFAULT_LIMIT = 20
CATALOGUE_MAX_LIMIT = 50

# The clinical status vocabulary, DERIVED FROM REAL BACKEND STATE -- never
# invented. hospital.laboratory.request.state has exactly six values; the only
# split that is not one-to-one is `requested`, which means two genuinely
# different things to a doctor depending on whether the encounter has cleared
# financially:
#
#   requested + billing_blocked  -> the desk is waiting on the patient/payer
#   requested + cleared          -> the bench may draw the sample
#
# That distinction is the whole reason a doctor looks at this list, and it is
# read from hospital_billing's own clearance verdict rather than guessed.
STATUS_LABELS = {
    "draft": "Draft",
    "awaiting_clearance": "Awaiting clearance",
    "ready_for_collection": "Ready for collection",
    "collected": "Sample collected",
    "result_pending": "Result pending",
    "result_available": "Result available",
    "cancelled": "Cancelled",
}

_STATE_STATUS = {
    "draft": "draft",
    "sample_collected": "collected",
    "in_progress": "result_pending",
    "completed": "result_available",
    "cancelled": "cancelled",
}


def laboratory_status(request):
    """One clinical status key for a request. No money, no payer, no amount."""
    state = request.state
    if state == "requested":
        # billing_blocked is compute_sudo and returns a bare boolean.
        return "awaiting_clearance" if request.billing_blocked else "ready_for_collection"
    return _STATE_STATUS.get(state, "draft")


def serialize_laboratory_test(test):
    """One catalogue entry. Clinical and reference fields only."""
    if not test:
        return None
    return {
        "id": test.id,
        "name": test.name,
        "code": test.code or None,
        "category": selection_value(test.category),
        # What the patient has to give. Genuinely useful when ordering, and
        # already modelled on the test itself.
        "sample_type": selection_value(test.sample_type),
    }


def serialize_ordered_test(line):
    """An ordered test, named from its catalogue entry."""
    test = line.test_id
    return {
        "id": line.id,
        "test_id": test.id,
        "name": test.name,
        "code": test.code or None,
        "sample_type": selection_value(line.sample_type or test.sample_type),
    }


def serialize_order_diagnosis(diagnosis):
    """The cited indication, if any. Reuses the diagnosis's own disease."""
    if not diagnosis:
        return None
    disease = diagnosis.disease_id
    return {
        "id": diagnosis.id,
        "name": disease.name or diagnosis.display_name,
        "code": disease.code or None,
    }


def serialize_laboratory_order(request):
    """One laboratory order as a clinician needs to see it."""
    status = laboratory_status(request)
    return {
        "id": request.id,
        "request_code": request.name,
        "tests": [serialize_ordered_test(line) for line in request.line_ids],
        "diagnosis": serialize_order_diagnosis(request.diagnosis_id),
        "clinical_indication": request.clinical_notes or None,
        "priority": selection_value(request.priority),
        "status": status,
        "status_label": STATUS_LABELS.get(status, "Draft"),
        "ordered_at": date_value(request.request_date),
        "created_at": datetime_value(request.create_date),
        # The ordered set is frozen by the model the moment the request leaves
        # draft, and the Doctor Desk confirms on submission -- so an order is
        # never editable here. Stated explicitly rather than omitted, so the
        # client is not left inferring it.
        "editable": False,
        "cancellable": request.doctor_can_cancel(),
    }


def serialize_laboratory_orders(requests, can_order):
    """THE laboratory response shape, for reads and for every mutation.

    Mutations return the WHOLE list rather than the single changed order:
    placing an order and cancelling one both change what the rest of the list
    means to the doctor, and a single-row response would leave the desk
    patching an array it cannot fully reason about.
    """
    return {
        "orders": [serialize_laboratory_order(request) for request in requests],
        # The consultation is open, so new orders may be placed. Distinct from
        # per-order `cancellable`, which is about one request's own workflow.
        "can_order": bool(can_order),
    }

"""Doctor radiology payloads: the clinical order, and nothing priced.

CONFIDENTIALITY, STATED ONCE AND CHECKABLE.
Nothing below reads hospital.charge.line, hospital.billing.account,
hospital.charge.receipt, hospital.payer or hospital.patient.payer. The ONE
billing-derived value that crosses this boundary is
`hospital.radiology.request.billing_blocked` -- a BOOLEAN verdict, computed by
hospital_billing, carrying no amount, no payer and no allocation. Its sibling
`billing_clearance_message` is deliberately NOT read: it is written for a
cashier and can name sums.

That is the same discipline laboratory_serializers applies, and for the same
reason: a clinician acts on "can this proceed", never on "how much".

THE CATALOGUE IS CLINICAL ONLY. hospital.radiology.exam carries
`billing_service_id` once hospital_billing is installed, which is the mapping
that decides what a scan costs. It is never serialized, and neither is anything
reachable through it.

THE RESULT BOUNDARY. A released radiology report is a narrative -- findings,
impression, recommendations -- and none of it appears here. This slice reports
only THAT a study has completed, never what it said. Reading the report is the
Results slice, with its own endpoint and its own confidentiality argument.
"""
from .api_response import date_value, datetime_value, selection_value

# A doctor types three letters and wants the shortlist, not the whole
# catalogue. Anything larger is a scroll nobody reads and a query nobody
# intended, so the cap is enforced in SQL and the client cannot widen it.
CATALOGUE_DEFAULT_LIMIT = 20
CATALOGUE_MAX_LIMIT = 50

# The clinical status vocabulary, DERIVED FROM REAL BACKEND STATE -- never
# invented. hospital.radiology.request.state has exactly six values, and TWO of
# them mean something different to a doctor depending on whether the encounter
# has cleared financially:
#
#   requested + billing_blocked  -> the desk is waiting on the patient/payer
#   requested + cleared          -> imaging may schedule it
#   scheduled + billing_blocked  -> booked, but money still blocks the scan
#   scheduled + cleared          -> booked and payable-clear; the scan may start
#
# WHY BOTH STATES SPLIT, AND WHY LABORATORY ONLY SPLITS ONE. Radiology raises
# its charges at confirmation (draft -> requested) but does not reach its
# clearance gate until Mark In Progress (scheduled -> in_progress). The unpaid
# window therefore spans TWO states, not one, and a status derived only from
# `scheduled` would have called a confirmed, unpaid, prepayment-required study
# "Requested" -- which reads as nothing-owed to the doctor who ordered it.
#
# NO KEY BELOW CLAIMS THE SERVICE IS READY. `awaiting_scheduling` and
# `scheduled` both describe a booking position, not a green light: the
# authoritative gate is action_mark_in_progress(), which re-checks clearance
# whatever this says.
STATUS_LABELS = {
    "draft": "Draft",
    "awaiting_clearance": "Awaiting clearance",
    "awaiting_scheduling": "Awaiting scheduling",
    "scheduled": "Scheduled",
    "in_progress": "Imaging in progress",
    "result_available": "Result available",
    "cancelled": "Cancelled",
}

# The states that need no financial qualification: the gate is either already
# behind them or no longer relevant.
_STATE_STATUS = {
    "draft": "draft",
    "in_progress": "in_progress",
    "completed": "result_available",
    "cancelled": "cancelled",
}

# What `billing_blocked` is allowed to change. Restated from
# hospital_billing.radiology_billing.BILLING_BLOCKED_STATES rather than imported,
# because this module must not import from hospital_billing at all -- that is
# the boundary the confidentiality note above describes. The two lists agreeing
# is asserted by test_doctor_radiology_api, so a change on either side surfaces
# as a failing test rather than as a wrong badge.
_CLEARABLE_STATES = {
    "requested": "awaiting_scheduling",
    "scheduled": "scheduled",
}


def radiology_status(request):
    """One clinical status key for a request. No money, no payer, no amount."""
    state = request.state
    if state in _CLEARABLE_STATES:
        # billing_blocked is compute_sudo and returns a bare boolean.
        if request.billing_blocked:
            return "awaiting_clearance"
        return _CLEARABLE_STATES[state]
    return _STATE_STATUS.get(state, "draft")


def serialize_radiology_exam(exam):
    """One catalogue entry. Clinical and reference fields only.

    `modality` and `body_part` are what a doctor picks BY -- a chest film and a
    chest CT answer different questions -- and `contrast_required` is patient
    preparation the ordering clinician has to know about before the patient
    leaves the room. All three are on the exam already; none is billing.
    """
    if not exam:
        return None
    return {
        "id": exam.id,
        "name": exam.name,
        "code": exam.code or None,
        "modality": selection_value(exam.modality),
        "body_part": exam.body_part or None,
        "contrast_required": bool(exam.contrast_required),
    }


def serialize_ordered_exam(line):
    """An ordered study, named from its catalogue entry.

    body_part and contrast_required are read from the LINE, falling back to the
    exam. The line carries its own copies -- written from the catalogue when the
    order is placed -- so an exam retitled or re-scoped next year does not
    rewrite what was ordered last year.
    """
    exam = line.exam_id
    return {
        "id": line.id,
        "exam_id": exam.id,
        "name": exam.name,
        "code": exam.code or None,
        "modality": selection_value(line.modality or exam.modality),
        "body_part": line.body_part or exam.body_part or None,
        "contrast_required": bool(line.contrast_required or exam.contrast_required),
        "special_instruction": line.special_instruction or None,
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


def serialize_radiology_order(request):
    """One radiology order as a clinician needs to see it.

    `has_result` REPORTS EXISTENCE, NEVER CONTENT. A doctor chasing an imaging
    order needs to know a report has landed; reading it is the Results slice.
    It is derived from the request's own completion state rather than from
    result_ids, so it can never become a foothold for serializing a report:
    `completed` is set by hospital_billing's _sync_completion_from_results()
    only once every active examination has been RELEASED, which is precisely
    the moment a report exists to read.
    """
    status = radiology_status(request)
    return {
        "id": request.id,
        "request_code": request.name,
        "exams": [serialize_ordered_exam(line) for line in request.line_ids],
        "diagnosis": serialize_order_diagnosis(request.diagnosis_id),
        "clinical_indication": request.clinical_indication or None,
        "instructions": request.instructions or None,
        "priority": selection_value(request.priority),
        "status": status,
        "status_label": STATUS_LABELS.get(status, "Draft"),
        "ordered_at": date_value(request.request_date),
        "created_at": datetime_value(request.create_date),
        "has_result": status == "result_available",
        # The ordered set is frozen by hospital_billing the moment the request
        # leaves draft, and the Doctor Desk confirms on submission -- so an
        # order is never editable here. Stated explicitly rather than omitted,
        # so the client is not left inferring it.
        "editable": False,
        "cancellable": request.doctor_can_cancel(),
    }


def serialize_radiology_orders(requests, can_order):
    """THE radiology response shape, for reads and for every mutation.

    Mutations return the WHOLE list rather than the single changed order:
    placing an order and cancelling one both change what the rest of the list
    means to the doctor, and a single-row response would leave the desk
    patching an array it cannot fully reason about.
    """
    return {
        "orders": [serialize_radiology_order(request) for request in requests],
        # The consultation is open, so new orders may be placed. Distinct from
        # per-order `cancellable`, which is about one request's own workflow.
        "can_order": bool(can_order),
    }

"""Radiology Desk payloads: the imaging department's own view of its work.

WHY THIS IS NOT radiology_serializers.py OR result_serializers.py
-----------------------------------------------------------------
Those are the DOCTOR's contracts. radiology_serializers collapses the whole
second half of imaging into `in_progress` and `result_available`, and
result_serializers shows a report only once it is RELEASED -- both correct for a
clinician, who asks "has my study come back". The imaging department asks the
opposite questions: which studies can be scheduled, which are waiting for a
report, which report is waiting to be validated. Folding those together would
hide the distinction this desk exists to show, and widening the Doctor contract
would put unreleased reports in front of clinicians. So the two are separate,
and neither module is touched by this one.

WHAT IS SHARED, AND IT IS THE PART THAT MATTERS: every lane here is DERIVED from
`hospital.radiology.request.state`, `hospital.radiology.result.state` and
hospital_billing's `billing_blocked` boolean. No persisted state is invented and
nothing derived is ever written back.

CONFIDENTIALITY, STATED ONCE AND CHECKABLE.
Nothing below reads hospital.charge.line, hospital.charge.receipt,
hospital.billing.account, hospital.payer, hospital.patient.payer, any invoice,
journal, fiscal or stock model, or hospital.audit.log. The ONE billing-derived
value that crosses this boundary is `billing_blocked` -- a BOOLEAN computed by
hospital_billing, carrying no amount, no payer and no allocation.
`billing_clearance_message`, `charge_line_ids` and `receipt_ids` are never read:
the first is written for a cashier and hospital_billing's radiology refusal
names the patient-payable amount for EVERY role, so a manager viewing this desk
would otherwise see sums a technician does not. The payload is identical for
every role that can open the desk.

WHAT A RADIOLOGY ROLE CAN READ, WHICH SHAPES THIS FILE.
After Radiology Slice 0A a Radiology Technician or Radiologist holds read on the
six radiology models, hospital.patient and hospital.doctor -- and NOTHING on
hospital.encounter, hospital.appointment or hospital.consultation. So no field
below dereferences those three. `ordered_from_consultation` reads only whether
the many2one is set, which needs no access to the consultation itself. A
serializer that reached for `request.encounter_id.name` would 403 for exactly
the roles this desk serves while working perfectly for the manager who tested
it -- the trap lab_desk_serializers documents for the laboratory bench.

REPORT TEXT IS OPERATIONAL HERE AND ONLY HERE. The desk may read draft, entered
and validated report text because reporting is its work. That never reaches the
Doctor Desk, whose own serializers still release nothing before `released`.
"""
from .api_response import date_value, datetime_value, m2o_value, selection_value

# ---------------------------------------------------------------------------
# The lane vocabulary
# ---------------------------------------------------------------------------
# NONE OF THESE ARE DATABASE VALUES. They are display lanes derived per request
# by rad_desk_lane() and never written back to Odoo; `state` travels beside
# `lane` on every payload so a client always has the authoritative value too.
#
# `draft` is a lane only in the sense that a draft request must still render
# safely if its id is opened directly. It is NOT a worklist lane and cannot be
# asked for: the Doctor Desk confirms on submission, so a draft request is an
# Odoo-side artefact, not imaging work.
RAD_DESK_LANE_LABELS = {
    "awaiting_clearance": "Awaiting clearance",
    "to_schedule": "To schedule",
    "ready_to_start": "Ready to start",
    "awaiting_report": "Awaiting report",
    "awaiting_validation": "Awaiting validation",
    "awaiting_release": "Awaiting release",
    "completed": "Completed",
    "anomaly": "Anomaly",
    "cancelled": "Cancelled",
    "draft": "Draft",
}

# The default queue: work that is in front of the department, in the order the
# work flows. Anomaly is part of it deliberately -- a request that needs review
# is the department's business, and hiding it behind a filter is how it stays
# broken. Completed and cancelled are reachable by name, never by default.
RAD_DESK_ACTIVE_LANES = (
    "awaiting_clearance",
    "to_schedule",
    "ready_to_start",
    "awaiting_report",
    "awaiting_validation",
    "awaiting_release",
    "anomaly",
)

# Every lane a caller may ask for. Anything else is a 400 rather than a quiet
# empty queue. `draft` is absent on purpose.
RAD_DESK_LANES = RAD_DESK_ACTIVE_LANES + ("completed", "cancelled")

# The request states each lane can contain. THE ONLY BRIDGE between the lane
# vocabulary and SQL, so a lane filter narrows in the database; the split inside
# a state (clearance, result state, conflict) is resolved per row afterwards by
# rad_desk_lane(). `anomaly` is not listed: its SQL shape needs the conflicted
# request ids too, and controllers/radiology._lane_domain builds it.
RAD_DESK_LANE_STATES = {
    "awaiting_clearance": ("requested", "scheduled"),
    "to_schedule": ("requested",),
    "ready_to_start": ("scheduled",),
    "awaiting_report": ("in_progress",),
    "awaiting_validation": ("in_progress",),
    "awaiting_release": ("in_progress",),
    "completed": ("completed",),
    "cancelled": ("cancelled",),
}

# The states in which a request can land in `anomaly`. in_progress through a
# released-but-not-completed report; the other three only through a result
# conflict. Cancelled and draft never.
RAD_DESK_ANOMALY_STATES = ("requested", "scheduled", "in_progress", "completed")

# Why a request is an anomaly. A key, so the client can style it, and a
# sentence, so the client never has to invent wording for a clinical-ops fact.
RESULT_CONFLICT_MESSAGE = (
    "Multiple active radiology reports were found for this request. "
    "Review is required before workflow actions can continue."
)
ANOMALY_MESSAGES = {
    "result_conflict": RESULT_CONFLICT_MESSAGE,
    "released_not_completed": (
        "A released radiology report exists, but the request has not completed. "
        "Review is required before workflow actions can continue."
    ),
    "unknown_state": (
        "This request is in a state the Radiology Desk does not recognise. "
        "Review is required before workflow actions can continue."
    ),
}

PRIORITY_LABELS = {
    "routine": "Routine",
    "urgent": "Urgent",
    "stat": "STAT",
}


def lane_label(lane):
    return RAD_DESK_LANE_LABELS.get(lane, lane)


def priority_label(priority):
    return PRIORITY_LABELS.get(priority, PRIORITY_LABELS["routine"])


def selection_label(record, field_name):
    """The model's own wording for a selection value, or None.

    Read from the field definition rather than restated, so a modality or state
    added to hospital_radiology surfaces here with its real label instead of as
    a blank or a stale copy.
    """
    if not record:
        return None
    value = record[field_name]
    if not value:
        return None
    labels = dict(record._fields[field_name]._description_selection(record.env))
    return labels.get(value, value)


def selection_options(model, field_name):
    """Every value of a selection field, as the model defines it."""
    return [
        {"value": value, "label": label}
        for value, label in model._fields[field_name]._description_selection(model.env)
    ]


def _text(value):
    """Free text, or None. Whitespace-only is not documentation."""
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


# ---------------------------------------------------------------------------
# The operational result, and the lane
# ---------------------------------------------------------------------------
def operational_results(request):
    """Every result the desk would work from: active and not cancelled.

    Read through the `result_ids` One2many, which applies the caller's record
    rules and the ORM's active_test (so an archived result is not counted) and
    is prefetched across a whole worklist in one read. Sorted by id so the same
    database always yields the same order.

    NEVER NARROWED TO "THE NEWEST". The model allows several results per
    request; choosing one silently would let a future workflow action land on a
    record the request's completion rule does not count. More than one is a
    conflict, and the caller says so.
    """
    results = request.result_ids.filtered(lambda result: result.state != "cancelled")
    return results.sorted(key=lambda result: result.id)


def rad_desk_lane(request, results=None):
    """(lane, anomaly_reason) for one request. THE one lane definition.

    The worklist rows, the lane filter and the lane summary all call this, so
    the badge, the row and the filter can never disagree about where a request
    belongs.

    `billing_blocked` is consulted ONLY in `requested` and `scheduled`, the two
    states hospital_billing computes it for (BILLING_BLOCKED_STATES). Reading its
    False anywhere else as "cleared" would be reporting a decision nobody made.

    A RESULT CONFLICT OUTRANKS EVERYTHING BUT CANCELLED AND DRAFT. However far
    the request has got, more than one active report means the next workflow
    action would have to guess which one it acts on, so the request is shown
    for review rather than in the lane its state alone suggests.
    """
    if results is None:
        results = operational_results(request)
    state = request.state

    if state == "cancelled":
        return "cancelled", None
    if state == "draft":
        return "draft", None
    if len(results) > 1:
        return "anomaly", "result_conflict"

    if state in ("requested", "scheduled"):
        if request.billing_blocked:
            return "awaiting_clearance", None
        return ("to_schedule" if state == "requested" else "ready_to_start"), None

    if state == "in_progress":
        result_state = results.state if results else None
        if result_state in (None, "draft"):
            return "awaiting_report", None
        if result_state == "entered":
            return "awaiting_validation", None
        if result_state == "validated":
            return "awaiting_release", None
        # `released`: every released report should have completed the request
        # through hospital_billing's _sync_completion_from_results. One that did
        # not is exactly the legacy shape the discovery found, and it is shown
        # for review -- never quietly as awaiting release or as completed.
        return "anomaly", "released_not_completed"

    if state == "completed":
        return "completed", None

    return "anomaly", "unknown_state"


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def serialize_patient_identity(patient):
    """Identity only: the name, the chart number, age and sex.

    'mrn' is the vocabulary every other desk uses for
    hospital.patient.identification_code. Age and sex are what a radiographer
    checks a patient against before a study. Nothing else: no phone, no
    address, no next of kin, no allergy, no diagnosis history.
    """
    if not patient:
        return None
    return {
        "id": patient.id,
        "name": patient.name,
        "mrn": patient.identification_code or None,
        "age": patient.age or None,
        "gender": selection_value(patient.gender),
    }


def active_lines(request):
    """Ordered studies that were not cancelled.

    hospital.radiology.request.line carries its own active/cancelled state; a
    cancelled study is not work and must not appear as if it were.
    """
    return request.line_ids.filtered(lambda line: line.state != "cancelled")


def _line_modality(line):
    exam = line.exam_id
    if line.modality:
        return line.modality, selection_label(line, "modality")
    if exam and exam.modality:
        return exam.modality, selection_label(exam, "modality")
    return None, None


def serialize_exam_summary(request):
    """The one-line "what is this study" a queue row needs.

    The FIRST active exam stands for the row, and the count says whether there
    are more. Built server-side so the queue and the detail panel can never
    disagree about what was ordered.
    """
    lines = active_lines(request)
    first = lines[:1]
    modality, modality_label = _line_modality(first) if first else (None, None)
    exam = first.exam_id if first else None
    return {
        "exam_count": len(lines),
        "first_exam": (
            {"id": exam.id, "name": exam.name, "code": exam.code or None}
            if exam
            else None
        ),
        "modality": modality,
        "modality_label": modality_label,
        "body_part": (first.body_part or exam.body_part or None) if first else None,
        "exams_summary": " · ".join(line.exam_id.name for line in lines) or None,
    }


def has_report_text(result):
    """Whether anyone wrote anything on this report.

    The same test the Doctor Desk applies to a released report, restated rather
    than imported so this module does not depend on the Doctor contract.
    hospital.radiology has no completeness constraint: an empty report can be
    entered, validated and released, and the UAT database already holds one.
    """
    if not result:
        return False
    if _text(result.findings) or _text(result.impression) or _text(result.recommendations):
        return True
    return any(_text(line.result_summary) or _text(line.notes) for line in result.line_ids)


def serialize_result_summary(result):
    """What a queue row says about the one operational report. No text."""
    if not result:
        return None
    return {
        "id": result.id,
        "name": result.name,
        "state": result.state,
        "state_label": selection_label(result, "state"),
        "result_date": date_value(result.result_date),
        # DISPLAY NAME ONLY. No id, no login, no email.
        "radiologist": result.radiologist_id.name or None,
        "has_report": has_report_text(result),
        "image_count": len(result.image_ids),
    }


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------
def serialize_queue_row(request):
    """One Radiology Desk queue row. Read-only, clinical and operational only.

    NO REPORT TEXT. A queue needs to know that a report exists and where it
    stands; findings and impression belong to the detail of the one request the
    user selected.
    """
    results = operational_results(request)
    lane, anomaly_reason = rad_desk_lane(request, results)
    conflict = len(results) > 1
    payload = {
        "id": request.id,
        "request_code": request.name,
        # The AUTHORITATIVE value travels next to the derived one, always.
        "state": request.state,
        "lane": lane,
        "lane_label": lane_label(lane),
        "anomaly_reason": anomaly_reason,
        "priority": selection_value(request.priority),
        "priority_label": priority_label(request.priority),
        "request_date": date_value(request.request_date),
        "created_at": datetime_value(request.create_date),
        # A BOOLEAN VERDICT. Never an amount, never a payer, never a message.
        "billing_blocked": bool(request.billing_blocked),
        "active": bool(request.active),
        "patient": serialize_patient_identity(request.patient_id),
        # hospital.doctor, which Radiology roles may read. A physician with no
        # linked user still has a name, so legacy rows render.
        "ordering_physician": m2o_value(request.physician_id),
        "result": None if conflict else serialize_result_summary(results[:1]),
        "result_conflict": conflict,
        "result_count": len(results),
    }
    payload.update(serialize_exam_summary(request))
    return payload


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------
def serialize_ordered_exam(line):
    """One ordered study, as the department has to perform it.

    body_part and contrast are read from the LINE, falling back to the exam:
    the line keeps what the catalogue said on the day it was ordered.
    """
    exam = line.exam_id
    modality, modality_label = _line_modality(line)
    return {
        # The request LINE id: what a result line's request_line_id points at.
        "request_line_id": line.id,
        "exam_id": exam.id,
        "code": exam.code or None,
        "name": exam.name,
        "modality": modality,
        "modality_label": modality_label,
        "body_part": line.body_part or exam.body_part or None,
        "contrast_required": bool(line.contrast_required or exam.contrast_required),
        "special_instruction": _text(line.special_instruction),
        "state": line.state,
        "sequence": line.sequence,
    }


def serialize_result_line(line):
    exam = line.exam_id
    modality, modality_label = _line_modality(line)
    return {
        "id": line.id,
        "request_line_id": line.request_line_id.id or None,
        "exam": {"id": exam.id, "name": exam.name, "code": exam.code or None},
        "modality": modality,
        "modality_label": modality_label,
        "body_part": line.body_part or exam.body_part or None,
        "contrast_used": bool(line.contrast_used),
        "result_summary": _text(line.result_summary),
        "notes": _text(line.notes),
    }


def serialize_image_metadata(image):
    """One attached clinical file, as METADATA ONLY.

    NO BYTES AND NO URL, in Slice 1 at all. `file` is never read, and no
    /web/content path, attachment id or access token is built: the id is the
    hospital.radiology.image id, and a byte endpoint for this desk is a later
    slice with its own security argument. The mimetype and size are the values
    the model derived from the file's own bytes at upload.
    """
    return {
        "id": image.id,
        "name": image.name,
        "caption": _text(image.caption),
        "image_type": image.image_type,
        "image_type_label": selection_label(image, "image_type"),
        "filename": image.filename,
        "mimetype": image.mimetype or None,
        "file_size": image.file_size or 0,
        "uploaded_by": image.uploaded_by_id.name or None,
        "uploaded_at": datetime_value(image.uploaded_at),
        "sequence": image.sequence,
    }


def serialize_operational_result(result):
    """The one operational report, in full, for the desk that writes it.

    Includes draft, entered and validated text: this is the department's own
    workstation. The Doctor Desk is unaffected -- its serializers are separate
    and still release nothing before `released`.
    """
    if not result:
        return None
    images = result.image_ids.sorted(key=lambda image: (image.sequence, image.id))
    payload = serialize_result_summary(result)
    payload.update(
        {
            "findings": _text(result.findings),
            "impression": _text(result.impression),
            "recommendations": _text(result.recommendations),
            "lines": [serialize_result_line(line) for line in result.line_ids],
            "images": [serialize_image_metadata(image) for image in images],
        }
    )
    return payload


def serialize_request_detail(request):
    """One request, in full, for the detail panel.

    A SUPERSET of the queue row, so the panel never merges two shapes and a
    selected row renders immediately from what the queue already returned.

    A CONFLICT IS STATED, NOT RESOLVED. With more than one operational report,
    `result` is null, `result_conflict` is true and `review_message` says so in
    words; no report is presented as authoritative.
    """
    payload = serialize_queue_row(request)
    results = operational_results(request)
    lines = active_lines(request)
    conflict = len(results) > 1
    payload.update(
        {
            "clinical_indication": _text(request.clinical_indication),
            "instructions": _text(request.instructions),
            "completed_at": datetime_value(request.completed_at),
            # Whether the Doctor Desk placed this order. Reads only that the
            # many2one is set; the consultation itself is not readable by the
            # roles this desk serves, and nothing about it is serialized.
            "ordered_from_consultation": bool(request.consultation_id),
            "exams": [serialize_ordered_exam(line) for line in lines],
            "cancelled_exam_count": len(request.line_ids) - len(lines),
            "result": None if conflict else serialize_operational_result(results[:1]),
            "review_message": ANOMALY_MESSAGES.get(payload["anomaly_reason"]),
        }
    )
    return payload


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------
def serialize_worklist(requests, summary, filters, meta, capabilities):
    """THE worklist response shape."""
    return {
        "rows": [serialize_queue_row(request) for request in requests],
        "summary": summary,
        "filters": filters,
        "meta": meta,
        "capabilities": capabilities,
    }


def serialize_session(env, roles, capabilities):
    """Who is signed in, which Radiology Desk role they hold, and whether the
    desk opens for them. Identity and those four flags only: no group list and
    no statement about any other workstation.
    """
    user = env.user
    return {
        "user": {"id": user.id, "name": user.name},
        "company": m2o_value(user.company_id),
        "roles": roles,
        "capabilities": capabilities,
    }

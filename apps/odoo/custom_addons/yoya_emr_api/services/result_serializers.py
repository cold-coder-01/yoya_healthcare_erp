"""Doctor results payloads: released clinical findings, and nothing priced.

RELEASED ONLY. THIS IS THE RULE THE WHOLE MODULE EXISTS TO KEEP.
`result` is populated from a result whose own state is 'released' and from no
other. Draft, entered, VALIDATED and cancelled results serialize as absent, and
the request reports `pending`.

Validated is excluded deliberately, and it is the tempting mistake. Laboratory
freezes content at validation, so a validated result looks final -- but
hospital.laboratory.result.action_release() names RELEASE as the clinical
handoff boundary, and the laboratory holds validated results back on purpose.
Radiology is stronger still: its write() freezes only at 'released', so a
validated radiology report is STILL EDITABLE, and publishing one would show a
doctor a paragraph the radiologist may be in the middle of rewriting.

AVAILABILITY IS NOT THE REQUEST'S STATE. `request.state == 'completed'` is not
used anywhere below. A released result can legitimately sit on a request that
is still in_progress: laboratory completion matches ordered lines to result
lines through request_line_id, and legacy result lines carry NULL there, so
those requests never reach 'completed' however finished the work is. Deriving
availability from the request would hide real, released, clinically valid
findings. Availability is the existence of a released result, full stop.

CONFIDENTIALITY, STATED ONCE AND CHECKABLE.
Nothing below reads hospital.charge.line, hospital.charge.receipt,
hospital.billing.account, hospital.payer, hospital.patient.payer, any invoice,
journal, fiscal or stock model, or hospital.audit.log. The ONE billing-derived
value that crosses this boundary is `billing_blocked` -- a BOOLEAN verdict
computed by hospital_billing, carrying no amount, no payer and no allocation --
and it is read ONLY on the pending side, where "the desk is waiting on the
patient" is the answer to "why has nothing come back yet". Its sibling
`billing_clearance_message` is deliberately NOT read: it is written for a
cashier and can name sums. That is the same discipline laboratory_serializers
and radiology_serializers already apply.

NO USER ACCOUNTS. The radiologist's DISPLAY NAME is serialized because a
clinician rings the reporter about an impression. No id, no login, no email.
The laboratory technician is not serialized at all: the bench operator is not
a clinical contact for the ordering doctor. Validator identity and validation
timestamps do not exist as fields on either model, and this module does not
mine the audit log to invent them.

READ-ONLY, INCLUDING THE LINKAGE. hospital_billing's _resolve_request_line()
HEALS a NULL request_line_id by writing it back. This module must never do
that: a GET that repairs data is a mutation with no audit trail and no
transaction the caller asked for. The fallback below resolves in memory only,
and reports the mapping as unresolved rather than guessing.
"""
from datetime import date

from .api_response import date_value, datetime_value, selection_value

# The Results vocabulary. THREE keys, each backed by real state:
#
#   cancelled  the request was cancelled
#   available  a released result exists
#   pending    everything else
#
# There is deliberately no 'reviewed'/'seen'/'acknowledged'. No model records
# that a doctor has read a result, so the desk would be asserting something no
# record supports.
RESULT_STATUS_LABELS = {
    "pending": "Pending",
    "available": "Result available",
    "cancelled": "Cancelled",
}

# The bench's own words for an abnormality. Sent alongside the key so the client
# never has to invent wording for a clinical judgement, and so a flag added to
# the model surfaces as an unknown key rather than as a silently blank cell.
ABNORMAL_FLAG_LABELS = {
    "normal": "Normal",
    "low": "Low",
    "high": "High",
    "critical": "Critical",
    "abnormal": "Abnormal",
}

# Mirrors hospital.radiology.exam.modality. Restated rather than imported
# because this module must not import hospital_radiology; a test pins the two
# together so a modality added there surfaces as a failing test, not a blank.
MODALITY_LABELS = {
    "xray": "X-Ray",
    "ultrasound": "Ultrasound",
    "ct": "CT Scan",
    "mri": "MRI",
    "fluoroscopy": "Fluoroscopy",
    "mammography": "Mammography",
    "other": "Other",
}


def _text(value):
    """Clinical free text, or None. Whitespace-only is not documentation."""
    if not value:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _modality_label(modality):
    """The exam's own selection wording, or None."""
    return MODALITY_LABELS.get(modality) if modality else None


def released_results(request):
    """Released, active results of one request, newest first.

    DETERMINISTIC WHEN THERE IS MORE THAN ONE, which both schemas allow: the
    request/result relation is 1:N on each side, and repeat testing is
    explicitly modelled as a NEW result rather than as extra lines.

    Sorted explicitly by (result_date, id) descending rather than trusting the
    order the One2many happened to return, so the same database always produces
    the same answer. The caller reports the newest and COUNTS the rest -- an
    older released result is never silently dropped, and never silently merged
    into the newer one's lines, which would attribute one occurrence's values
    to another's ordered test.

    Filtered as the CALLER: record rules already decide which results this
    doctor may see, and this adds no sudo.
    """
    if not request:
        return request
    results = request.result_ids.filtered(
        lambda result: result.state == "released" and result.active
    )
    return results.sorted(
        key=lambda r: (r.result_date or date.min, r.id), reverse=True
    )


def result_status(request, released):
    """One Results-level status key. Never derived from request.state alone."""
    if request.state == "cancelled":
        return "cancelled"
    return "available" if released else "pending"


def serialize_result_diagnosis(diagnosis):
    """The cited indication, if any. Same shape the order serializers use."""
    if not diagnosis:
        return None
    disease = diagnosis.disease_id
    return {
        "id": diagnosis.id,
        "name": disease.name or diagnosis.display_name,
        "code": disease.code or None,
    }


def _resolve_ordered_line(result_line, ordered_lines, key):
    """The ordered line this result line reports on, or None. NEVER WRITES.

    `request_line_id` is authoritative when set. When it is NULL -- every row
    predating the column -- exactly one ordered line matching the same
    test/exam is an unambiguous match and is used; zero or several is NOT, and
    returns None.

    REFUSING TO GUESS IS THE POINT. hospital_billing's resolver raises on an
    ambiguous match rather than picking the first, because delivering a
    sibling's charge is worse than stopping. The read-only equivalent of
    stopping is reporting the mapping as unresolved, so the desk can show the
    value without claiming which ordered test it answers.
    """
    if result_line.request_line_id:
        return result_line.request_line_id
    candidates = ordered_lines.filtered(
        lambda line: line[key] == result_line[key]
    )
    return candidates if len(candidates) == 1 else None


# ----------------------------------------------------------------------
# Laboratory
# ----------------------------------------------------------------------
def serialize_pending_test(line):
    """An ordered test still waiting on the bench."""
    test = line.test_id
    return {
        "id": line.id,
        "test_id": test.id,
        "name": test.name,
        "code": test.code or None,
        "sample_type": selection_value(line.sample_type or test.sample_type),
    }


def serialize_result_line(line, ordered_lines):
    """One reported test.

    VALUES TRAVEL VERBATIM. `result_value` and `reference_range` are Char on
    the model and hold whatever the bench typed: '92', '< 0.01', 'Negative',
    'No growth after 48h', '70/100'. Nothing here parses, rounds, ranges or
    re-derives them, and nothing here decides abnormality -- `abnormal_flag` is
    the laboratory's own judgement and is passed through with its label. A
    client that recomputed high/low from a string would be a second opinion
    with no clinician behind it.
    """
    test = line.test_id
    ordered = _resolve_ordered_line(line, ordered_lines, "test_id")
    flag = line.abnormal_flag or None
    return {
        "id": line.id,
        "test_id": test.id,
        "name": test.name,
        "code": test.code or None,
        "sample_type": selection_value(line.sample_type),
        "value": line.result_value or None,
        "unit": line.unit or None,
        "reference_range": line.reference_range or None,
        "abnormal_flag": flag,
        "abnormal_flag_label": ABNORMAL_FLAG_LABELS.get(flag) if flag else None,
        "notes": _text(line.notes),
        # Which ordered test this reports on. null means the linkage could not
        # be resolved without guessing -- the value is still shown, the claim
        # about WHICH order it answers is withheld.
        "request_line_id": ordered.id if ordered else None,
        "ordered": bool(ordered),
    }


def serialize_laboratory_result(result):
    """One released laboratory result."""
    ordered_lines = result.request_id.line_ids
    return {
        "id": result.id,
        "result_code": result.name,
        "result_date": date_value(result.result_date),
        "interpretation": _text(result.interpretation),
        "remarks": _text(result.remarks),
        "lines": [
            serialize_result_line(line, ordered_lines) for line in result.line_ids
        ],
    }


def serialize_laboratory_review(request):
    """One laboratory order, as the Results tab reviews it."""
    released = released_results(request)
    current = released[:1]
    status = result_status(request, current)
    return {
        "request_id": request.id,
        "request_code": request.name,
        "priority": selection_value(request.priority),
        "status": status,
        "status_label": RESULT_STATUS_LABELS[status],
        # The request's own workflow wording, as SECONDARY context: "sample
        # collected" tells a doctor chasing a pending result where it actually
        # is. It never overrides the three-state Results vocabulary above.
        "workflow_status": selection_value(request.state),
        "clinical_indication": _text(request.clinical_notes),
        "diagnosis": serialize_result_diagnosis(request.diagnosis_id),
        "ordered_at": date_value(request.request_date),
        "created_at": datetime_value(request.create_date),
        # A bare boolean, pending side only. Why nothing has come back yet is a
        # question the pending side has to answer; no amount, payer or
        # allocation crosses.
        "billing_blocked": (
            bool(request.billing_blocked) if status == "pending" else False
        ),
        "result": serialize_laboratory_result(current) if current else None,
        # Older released results, counted rather than dropped. Repeat testing
        # is modelled as a new result, so this is a real shape, and a doctor
        # must not be told the newest one is the only one.
        "superseded_count": max(len(released) - 1, 0),
        "pending_tests": (
            []
            if current
            else [serialize_pending_test(line) for line in request.line_ids]
        ),
    }


# ----------------------------------------------------------------------
# Radiology
# ----------------------------------------------------------------------
def _active_exam_lines(request):
    """Ordered studies that were not cancelled.

    hospital.radiology.request.line carries its own active/cancelled state --
    laboratory request lines do not -- so a cancelled study must not appear in
    a pending list as work still to come.
    """
    return request.line_ids.filtered(lambda line: line.state != "cancelled")


def serialize_pending_exam(line):
    """An ordered study still waiting on the imaging bench."""
    exam = line.exam_id
    modality = line.modality or exam.modality
    return {
        "id": line.id,
        "exam_id": exam.id,
        "name": exam.name,
        "code": exam.code or None,
        "modality": selection_value(modality),
        "modality_label": _modality_label(modality),
        "body_part": line.body_part or exam.body_part or None,
        "contrast_required": bool(line.contrast_required or exam.contrast_required),
    }


def serialize_reported_exam(line, ordered_lines):
    """One reported study inside a released report."""
    exam = line.exam_id
    ordered = _resolve_ordered_line(line, ordered_lines, "exam_id")
    modality = line.modality or exam.modality
    return {
        "id": line.id,
        "exam_id": exam.id,
        "name": exam.name,
        "code": exam.code or None,
        "modality": selection_value(modality),
        "modality_label": _modality_label(modality),
        "body_part": line.body_part or exam.body_part or None,
        "contrast_used": bool(line.contrast_used),
        "summary": _text(line.result_summary),
        "notes": _text(line.notes),
        "request_line_id": ordered.id if ordered else None,
        "ordered": bool(ordered),
    }


# What a doctor may DO with a file, derived from the mimetype the server itself
# sniffed at upload. Never from the extension: hospital.radiology.image refuses
# a file whose bytes disagree with its name, and re-deciding from the name here
# would reintroduce exactly the question that validation already settled.
_IMAGE_KIND = {
    "image/jpeg": "image",
    "image/png": "image",
    "application/pdf": "pdf",
}


def image_kind(mimetype):
    """"image" or "pdf" -- what the client should render, not what it is."""
    return _IMAGE_KIND.get(mimetype or "", "image")


def serialize_radiology_image(image):
    """One attached clinical file, as METADATA ONLY.

    NO URL IS SERIALIZED, AND THAT IS THE POINT. The client composes its own
    BFF path from `id`; an absolute URL in this payload is precisely where an
    Odoo origin, a /web/content path or an access token would leak into a
    browser. The id is the hospital.radiology.image id -- never the
    ir.attachment id, which is a database-wide handle this API has no business
    naming.

    `file` itself is absent. Bytes travel through the scoped byte endpoint,
    which re-checks the release state independently of this serializer.
    """
    return {
        "id": image.id,
        "name": image.name,
        "caption": _text(image.caption),
        "kind": image_kind(image.mimetype),
        "mimetype": image.mimetype or None,
        "filename": image.filename,
        "file_size": image.file_size or 0,
        "sequence": image.sequence,
    }


def released_images(result):
    """The active images of a result, in a deterministic order.

    Sorted explicitly by (sequence, id) rather than trusting the order the
    One2many happened to return, so the same database always produces the same
    list -- which is what lets a lightbox's "2 of 3" mean the same thing twice.
    """
    images = result.image_ids.filtered(lambda image: image.active)
    return images.sorted(key=lambda image: (image.sequence, image.id))


def serialize_radiology_result(result):
    """One released radiology report.

    `has_report` EXISTS BECAUSE THE EMPTY ONE IS REACHABLE. hospital.radiology
    has no completeness constraint: a report can be entered, validated and
    released with no findings, no impression and no lines, and the database
    already holds one. A card rendering that as blank space reads as a broken
    screen; the flag lets the desk say what is actually true -- released, with
    no report text recorded.

    Line summaries and notes count towards it: a report whose narrative is
    empty but whose exam line carries a one-line summary is not an empty report.
    """
    ordered_lines = result.request_id.line_ids
    exams = [serialize_reported_exam(line, ordered_lines) for line in result.line_ids]
    images = released_images(result)
    findings = _text(result.findings)
    impression = _text(result.impression)
    recommendations = _text(result.recommendations)
    # DELIBERATELY UNCHANGED BY IMAGING. `has_report` asks whether anyone wrote
    # anything, and a released report carrying only a picture is still a report
    # with no text -- which is what the empty-report sentence exists to say. An
    # image is shown beside that sentence, not instead of it.
    has_report = bool(
        findings
        or impression
        or recommendations
        or any(exam["summary"] or exam["notes"] for exam in exams)
    )
    return {
        "id": result.id,
        "result_code": result.name,
        "result_date": date_value(result.result_date),
        # DISPLAY NAME ONLY. No id, no login, no email.
        "radiologist": result.radiologist_id.name or None,
        "findings": findings,
        "impression": impression,
        "recommendations": recommendations,
        "has_report": has_report,
        "exams": exams,
        # IMAGING. Reached only from here, so it inherits the released-only
        # rule the whole `result` object already carries: an unreleased report
        # has no `result`, and therefore no images, without a second check.
        # The byte endpoint re-checks release anyway -- see the module docstring
        # on why one gate is never allowed to imply the other.
        "images": [serialize_radiology_image(image) for image in images],
        "image_count": len(images),
    }


def serialize_radiology_review(request):
    """One radiology order, as the Results tab reviews it."""
    released = released_results(request)
    current = released[:1]
    status = result_status(request, current)
    return {
        "request_id": request.id,
        "request_code": request.name,
        "priority": selection_value(request.priority),
        "status": status,
        "status_label": RESULT_STATUS_LABELS[status],
        "workflow_status": selection_value(request.state),
        "clinical_indication": _text(request.clinical_indication),
        "diagnosis": serialize_result_diagnosis(request.diagnosis_id),
        "ordered_at": date_value(request.request_date),
        "created_at": datetime_value(request.create_date),
        "billing_blocked": (
            bool(request.billing_blocked) if status == "pending" else False
        ),
        "result": serialize_radiology_result(current) if current else None,
        "superseded_count": max(len(released) - 1, 0),
        "pending_exams": (
            []
            if current
            else [serialize_pending_exam(line) for line in _active_exam_lines(request)]
        ),
    }


def serialize_results(laboratory_requests, radiology_requests):
    """THE results response shape. One payload, both services.

    The Results tab always renders the two together, so they travel together:
    two endpoints would mean two round trips, two loading states and two
    failure modes for one screen. There is no `can_*` flag anywhere in this
    payload, deliberately -- reviewing a result is not an act the desk may
    perform, and nothing here is writable.
    """
    return {
        "laboratory": [
            serialize_laboratory_review(request) for request in laboratory_requests
        ],
        "radiology": [
            serialize_radiology_review(request) for request in radiology_requests
        ],
    }

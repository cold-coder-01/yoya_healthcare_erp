"""Longitudinal clinical history payloads: prior episodes, and nothing priced.

WHAT THIS MODULE IS
-------------------
A PROJECTION over records that already exist. There is no history model, no
history table and no backfill; every value below is read from
hospital.encounter, hospital.consultation and the clinical children that hang
off them. A history row is therefore incapable of disagreeing with the record
it summarises, because it IS that record.

THE CONFIDENTIALITY CLAIM, STATED IN ONE SENTENCE
-------------------------------------------------
NOTHING BELOW READS A FINANCIAL MODEL OR A FINANCIAL FIELD.

There is no traversal to hospital.billing.account, hospital.charge.line,
hospital.payer, hospital.payer.agreement, hospital.patient.payer or
hospital.pharmacy stock, and no code path that could produce an amount, a
balance, a receipt, an agreement, a membership number or a payer name.

Three specific exclusions are worth naming because the obvious reuse would
introduce them:

  * consultation_serializers.serialize_consultation_envelope is NOT used.
    Its completion warnings deliberately carry
    encounter.reception_outstanding_amount, which is correct for the live
    completion screen and would be a confidentiality breach here.

  * doctor_serializers is NOT used. serialize_visit_detail exposes
    encounter.payer_type.

  * `billing_blocked` is STRIPPED from every laboratory and radiology row.
    It is a bare boolean about a pending order and carries no figure, but it
    is billing vocabulary on a screen that has no billing dimension, and a
    historical order's payment state is not a clinical fact.

RELEASED-ONLY IS NOT REDEFINED HERE
-----------------------------------
Laboratory and radiology rows are produced by result_serializers, unchanged,
so `released`, `available` and `abnormal` mean exactly what the Results tab
means by them. This module strips keys from those payloads; it never computes
a clinical value of its own and never inspects a numeric result.
"""
from .api_response import date_value, datetime_value, selection_value
from .consultation_serializers import serialize_consultation
from .diagnosis_serializers import serialize_diagnosis
from .medication_serializers import serialize_prescription
from .result_serializers import (
    released_images,
    released_results,
    serialize_laboratory_review,
    serialize_radiology_review,
)

# ----------------------------------------------------------------------
# Vocabulary
# ----------------------------------------------------------------------
ENCOUNTER_TYPE_LABELS = {
    "outpatient": "Outpatient",
    "inpatient": "Inpatient",
    "emergency": "Emergency",
    "daycare": "Day Care",
    "telemedicine": "Telemedicine",
    "other": "Other",
}

ENCOUNTER_STATE_LABELS = {
    "planned": "Planned",
    "checked_in": "Checked In",
    "active": "Active",
    "completed": "Completed",
    "closed": "Closed",
    "cancelled": "Cancelled",
}

TRIAGE_PRIORITY_LABELS = {
    "emergency": "Emergency",
    "urgent": "Urgent",
    "routine": "Routine",
}

# Keys removed from the reused Results payloads. See the module docstring.
BILLING_KEYS = ("billing_blocked",)

# Live-workflow affordances removed from the reused consultation payload. A
# historical note is not editable by anyone, and `version` exists only so the
# save path can detect a concurrent write; offering either on a read-only
# surface would invite a client to attempt a write that the model would refuse.
LIVE_NOTE_KEYS = ("editable", "version")

# Vitals stored as plain Float with no null sentinel. hospital.patient.evaluation
# declares these required=False with no default, so an UNRECORDED reading is
# indistinguishable in the column from a measured zero. Every one of these
# values is physiologically impossible at zero in a living patient, so 0.0 is
# reported as "not recorded" rather than rendered as a measurement.
#
# THIS IS A DISPLAY DECISION, NOT A CLINICAL ONE. It never converts a recorded
# value into a different recorded value; it only refuses to invent one.
ZERO_MEANS_UNRECORDED = (
    "systolic_bp",
    "diastolic_bp",
    "heart_rate",
    "temperature",
    "respiratory_rate",
    "spo2",
    "bmi",
    "weight",
    "height",
    "rbs",
    "head_circumference",
)


def _text(value):
    """Trimmed text, or None. Empty string and whitespace both read as absent."""
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _vital(value):
    """A measured vital, or None when the column holds the unrecorded zero."""
    if value is None:
        return None
    # Compared as a float rather than `not value` so a legitimately falsy but
    # recorded value could never be dropped by a future field added here.
    if float(value) == 0.0:
        return None
    return value


def _strip(payload, keys):
    """Return `payload` without `keys`. Never mutates the input."""
    if payload is None:
        return None
    return {key: value for key, value in payload.items() if key not in keys}


def _doctor_name(encounter, consultation):
    """The clinician who owned the episode. DISPLAY NAME ONLY.

    The consultation's stamped doctor is preferred because it is the physician
    who actually conducted the visit; encounter.primary_doctor_id is the
    assignment, which is the only answer available for an episode that never
    opened a consultation.

    NO id, NO login, NO email, in either branch.
    """
    if consultation and consultation.doctor_id:
        return consultation.doctor_id.name or None
    if encounter.primary_doctor_id:
        return encounter.primary_doctor_id.name or None
    return None


# ----------------------------------------------------------------------
# Patient identity
# ----------------------------------------------------------------------
def serialize_history_patient(patient):
    """The minimal identity block for the history header.

    NO RAW ORM ID. The client never needs it: every history route is addressed
    by appointment, derives the patient server-side and refuses a
    client-supplied patient id by construction. Omitting it here means the
    payload cannot become the source of an id that a future route might accept.
    """
    if not patient:
        return None
    return {
        "identification_code": patient.identification_code or None,
        "name": patient.name or None,
        "age": patient.age if patient.age else None,
        "gender": selection_value(patient.gender) if patient.gender else None,
    }


# ----------------------------------------------------------------------
# Summary
# ----------------------------------------------------------------------
def serialize_history_visit(encounter, consultation, chief_complaint,
                            primary_diagnosis, counts):
    """One prior episode, as the History worklist scans it.

    COMPACT ON PURPOSE. No narrative, no result value, no report text and no
    medication instruction reaches this row; those are what opening the visit
    is for. A card that carried them would make the list expensive to build and
    impossible to scan.

    `appointment_id` is the encounter's own COLUMN VALUE, used by the client to
    address the detail endpoint. Reading it does not read the appointment
    record, which History has no rule to reach; see the security XML.
    """
    disease = primary_diagnosis.disease_id if primary_diagnosis else None
    return {
        "appointment_id": encounter.appointment_id.id or None,
        "encounter_id": encounter.id,
        "encounter_code": encounter.name or None,
        "encounter_type": encounter.encounter_type or None,
        "encounter_type_label": ENCOUNTER_TYPE_LABELS.get(
            encounter.encounter_type
        ),
        "date": date_value(encounter.opened_at),
        "doctor": _doctor_name(encounter, consultation),
        "department": encounter.department_id.name or None,
        "chief_complaint": _text(chief_complaint),
        "primary_diagnosis": (
            {
                "name": disease.name if disease else None,
                "code": (disease.code or None) if disease else None,
                "certainty": selection_value(primary_diagnosis.certainty),
                "severity": selection_value(primary_diagnosis.severity),
            }
            if primary_diagnosis
            else None
        ),
        "status": encounter.state or None,
        "status_label": ENCOUNTER_STATE_LABELS.get(encounter.state),
        "counts": counts,
    }


def serialize_history_summary(patient, visits, total, limit, offset):
    """THE history summary response shape."""
    return {
        "patient": serialize_history_patient(patient),
        "visits": visits,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": (offset + len(visits)) < total,
    }


# ----------------------------------------------------------------------
# Detail
# ----------------------------------------------------------------------
def serialize_history_triage(evaluation):
    """The triage snapshot for one prior episode.

    An unrecorded vital serializes as null rather than 0.0; see
    ZERO_MEANS_UNRECORDED above for why the column cannot answer this itself.
    """
    if not evaluation:
        return None
    return {
        "chief_complaint": _text(evaluation.chief_complaint),
        "triage_priority": evaluation.triage_priority or None,
        "triage_priority_label": TRIAGE_PRIORITY_LABELS.get(
            evaluation.triage_priority
        ),
        "recorded_at": datetime_value(evaluation.evaluation_date),
        "systolic_bp": _vital(evaluation.systolic_bp),
        "diastolic_bp": _vital(evaluation.diastolic_bp),
        "heart_rate": _vital(evaluation.heart_rate),
        "temperature": _vital(evaluation.temperature),
        "respiratory_rate": _vital(evaluation.respiratory_rate),
        "spo2": _vital(evaluation.spo2),
        "bmi": _vital(evaluation.bmi),
        "bmi_state": evaluation.bmi_state or None,
        "weight": _vital(evaluation.weight),
        "height": _vital(evaluation.height),
        "pain_level": evaluation.pain_level or None,
    }


def serialize_history_visit_header(encounter, consultation):
    """Identity and provenance for the opened episode."""
    return {
        "appointment_id": encounter.appointment_id.id or None,
        "encounter_id": encounter.id,
        "encounter_code": encounter.name or None,
        "consultation_code": consultation.name if consultation else None,
        "encounter_type": encounter.encounter_type or None,
        "encounter_type_label": ENCOUNTER_TYPE_LABELS.get(
            encounter.encounter_type
        ),
        "date": date_value(encounter.opened_at),
        "opened_at": datetime_value(encounter.opened_at),
        "completed_at": datetime_value(encounter.completed_at),
        "doctor": _doctor_name(encounter, consultation),
        "department": encounter.department_id.name or None,
        "status": encounter.state or None,
        "status_label": ENCOUNTER_STATE_LABELS.get(encounter.state),
    }


def serialize_history_note(consultation):
    """The physician's narrative, minus the live-workflow affordances.

    serialize_consultation is reused rather than restated so a narrative field
    added to the model cannot be silently missing from History; CONSULTATION_
    NARRATIVE_FIELDS is imported from the model on that side for the same
    reason. serialize_consultation_envelope is NOT used; see the module
    docstring.
    """
    if not consultation:
        return None
    return _strip(serialize_consultation(consultation), LIVE_NOTE_KEYS)


def serialize_history_diagnoses(diagnoses):
    """Prior diagnoses, primary first, and read-only without exception."""
    rows = [serialize_diagnosis(row, editable=False) for row in diagnoses]
    order = {"primary": 0, "secondary": 1, "differential": 2, "history": 3}
    # Matches the frontend's sortDiagnoses so the two cannot disagree about
    # clinical reading order: headline first, then supporting, then what was
    # being ruled out, then the legacy `history` type.
    return sorted(
        rows,
        key=lambda row: (order.get(row["diagnosis_type"], 9), row["id"]),
    )


def serialize_history_medications(prescriptions):
    """Prior prescriptions.

    `editable` is already False from the serializer; `cancellable` is FORCED
    False here. A historical prescription is not a live order and History
    exposes no clinical action, so an affordance that says otherwise would be a
    control the viewer must never render. No price, batch, stock or fiscal
    field exists anywhere in the reused payload.

    A historical prescription is `Prescribed on <date>`. Nothing here asserts
    that the patient is still taking it: no model records a medication
    statement, a stop date or adherence, so History states what was prescribed
    and when, and stops there.
    """
    rows = []
    for prescription in prescriptions:
        row = serialize_prescription(prescription)
        row["editable"] = False
        row["cancellable"] = False
        rows.append(row)
    return rows


def serialize_history_laboratory(requests):
    """Prior laboratory orders, released-only, billing vocabulary stripped."""
    return [
        _strip(serialize_laboratory_review(request), BILLING_KEYS)
        for request in requests
    ]


def serialize_history_radiology(requests):
    """Prior imaging orders, released-only, billing vocabulary stripped.

    Image METADATA is left intact; the bytes stay behind the scoped visit
    image endpoint and nothing here emits base64.
    """
    return [
        _strip(serialize_radiology_review(request), BILLING_KEYS)
        for request in requests
    ]


def serialize_history_detail(encounter, consultation, evaluation, diagnoses,
                             prescriptions, laboratory_requests,
                             radiology_requests):
    """THE history detail response shape: one prior episode, in full."""
    return {
        "visit": serialize_history_visit_header(encounter, consultation),
        "triage": serialize_history_triage(evaluation),
        "note": serialize_history_note(consultation),
        "diagnoses": serialize_history_diagnoses(diagnoses),
        "medications": serialize_history_medications(prescriptions),
        "laboratory": serialize_history_laboratory(laboratory_requests),
        "radiology": serialize_history_radiology(radiology_requests),
    }


# ----------------------------------------------------------------------
# Counts
# ----------------------------------------------------------------------
def abnormal_counts(laboratory_requests):
    """(abnormal, critical) across the RELEASED results of these requests.

    THE SERVER FLAG IS THE ONLY INPUT. Nothing here reads a numeric value, a
    unit or a reference range; `abnormal_flag` is set by the laboratory and
    passed through unchanged, exactly as result_serializers.serialize_result_line
    states. A count derived from numbers would be a second, competing clinical
    interpretation.
    """
    abnormal = 0
    critical = 0
    for request in laboratory_requests:
        for result in released_results(request):
            for line in result.line_ids:
                flag = line.abnormal_flag or None
                if not flag or flag == "normal":
                    continue
                abnormal += 1
                if flag == "critical":
                    critical += 1
    return abnormal, critical


def released_image_count(radiology_requests):
    """How many released images this episode has, for the compact card.

    REUSES released_results and released_images rather than re-deriving
    "released" and "active". A second definition here could drift from the one
    the Results tab and the byte endpoint enforce, and the card would then
    promise a picture the viewer refuses to show.
    """
    return sum(
        len(released_images(result))
        for request in radiology_requests
        for result in released_results(request)
    )

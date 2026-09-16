"""Laboratory Desk payloads: the bench's own view of the work it must do.

WHY THIS IS NOT laboratory_serializers.py
-----------------------------------------
That module is the DOCTOR's contract. It answers "what happened to the tests I
ordered", and it deliberately collapses the second half of the workflow: its
`_STATE_STATUS` maps `in_progress` to `result_pending` and `completed` to
`result_available`, because a clinician wants to know whether a value has come
back, not which stage of the bench it is sitting at.

The bench needs the opposite. `sample_collected`, `in_progress` and `completed`
are three different piles of work to a laboratory technician, and folding them
into two would hide the distinction the queue exists to show. Reusing the
doctor's vocabulary here would either lie to the bench or force the doctor's
labels to change, so the two contracts are stated separately and pinned
separately by tests.

WHAT IS SHARED, AND IT IS THE PART THAT MATTERS: both vocabularies are DERIVED
from `hospital.laboratory.request.state` plus `billing_blocked`, and neither
invents a value. The six real states are the only input.

CONFIDENTIALITY, STATED ONCE AND CHECKABLE.
Nothing below reads hospital.charge.line, hospital.charge.receipt,
hospital.charge.receipt.allocation, hospital.billing.account, hospital.payer,
hospital.patient.payer, hospital.charge.responsibility, any invoice, journal,
fiscal or stock model, or hospital.audit.log. The ONE billing-derived value
that crosses this boundary is `billing_blocked` -- a BOOLEAN verdict computed
by hospital_billing, carrying no amount, no payer and no allocation.

`billing_clearance_message` IS DELIBERATELY NEVER READ. It is written for a
cashier and its own implementation (hospital_billing.laboratory_billing
._clearance_error and the engine's `reason` strings) can name sums:
"Prepayment of 900.00 is required before service." A bench operator is told
THAT a request is not cleared, never how much is owed by whom. This is the same
rule laboratory_serializers and result_serializers already apply for the
doctor, and a test asserts the string never appears in any payload.

WHAT A LABORATORY TECHNICIAN CAN ACTUALLY READ, WHICH SHAPES THIS FILE.
hospital_management grants group_hospital_lab_technician a read ACL on
hospital.patient, hospital.doctor, hospital.department and hospital.encounter
(the last one unrestricted by yoya_reception_bridge's
rule_encounter_service_desks). It grants NO ACL AT ALL on hospital.appointment
or hospital.consultation.

So the visit context below is resolved through `encounter_id` and never through
`appointment_id` or `consultation_id`. That is not a stylistic preference: the
laboratory models' own constraints already take narrow sudo() precisely because
"a lab technician holds no ACL on hospital.appointment", and a serializer that
reached for `request.appointment_id.display_name` would raise AccessError for
the one role this desk exists to serve -- turning the whole workstation into a
403 for the bench while working perfectly for the manager who tested it.

The appointment's raw id is not serialized either. It would be a database
identifier the desk cannot resolve, cannot display and must not use.
"""
from .api_response import date_value, datetime_value, m2o_value, selection_value

# THE BENCH STATUS VOCABULARY, derived from real backend state and nothing else.
#
# Seven keys for six states, because `requested` means two operationally
# different things depending on hospital_billing's clearance verdict:
#
#   requested + billing_blocked True   -> the money is not settled; do not draw
#   requested + billing_blocked False  -> draw the sample
#
# That single distinction is the reason a bench queue exists as a screen rather
# than as a list, and it is read from hospital_billing's own compute rather
# than guessed from anything financial.
#
# NONE OF THESE ARE DATABASE VALUES. They are display statuses and are never
# written back to Odoo; `state` travels alongside on every payload so a client
# always has the authoritative value too.
LAB_DESK_STATUS_LABELS = {
    "draft": "Draft",
    "awaiting_clearance": "Awaiting clearance",
    "ready_for_collection": "Ready for collection",
    "sample_collected": "Sample collected",
    "in_progress": "In progress",
    "completed": "Completed",
    "cancelled": "Cancelled",
}

# The statuses a bench actually works, in the order the work flows. Used as the
# default worklist filter and as the client's tab order, so "the default queue"
# has one definition rather than one per caller.
#
# `draft` is absent because a draft request has not been ordered yet -- the
# Doctor Desk confirms on submission, so a draft row is an Odoo-side artefact,
# not bench work. `completed` and `cancelled` are absent because they are
# finished. All three remain reachable through an explicit `status` filter.
LAB_DESK_ACTIVE_STATUSES = (
    "awaiting_clearance",
    "ready_for_collection",
    "sample_collected",
    "in_progress",
)

# Every status a caller may ask for. Anything else is a 400 rather than a
# silently empty queue.
LAB_DESK_STATUSES = tuple(LAB_DESK_STATUS_LABELS)

# Display status -> the request.state that backs it. THE ONLY BRIDGE between
# the vocabulary above and SQL, so a status filter narrows in the database
# instead of scanning the table and discarding rows in Python.
#
# Two statuses share `requested`; that is the split billing_blocked resolves,
# and it is resolved AFTER the query, per row. See _worklist_domain.
LAB_DESK_STATUS_STATE = {
    "draft": "draft",
    "awaiting_clearance": "requested",
    "ready_for_collection": "requested",
    "sample_collected": "sample_collected",
    "in_progress": "in_progress",
    "completed": "completed",
    "cancelled": "cancelled",
}

PRIORITY_LABELS = {
    "routine": "Routine",
    "urgent": "Urgent",
    "stat": "STAT",
}


def lab_desk_status(request):
    """One bench status key for one request. No money, no payer, no amount.

    `billing_blocked` is consulted ONLY in `requested`, which is the only state
    where hospital_billing computes it as a real verdict: its own
    `_compute_billing_blocked` short-circuits to False for every other state
    and for a request with no encounter. Reading False in `sample_collected`
    and calling that "cleared" would be reporting a decision nobody made.
    """
    state = request.state
    if state == "requested":
        # A bare boolean, computed with compute_sudo by hospital_billing.
        return "awaiting_clearance" if request.billing_blocked else "ready_for_collection"
    return state if state in LAB_DESK_STATUS_LABELS else "draft"


def status_label(status):
    return LAB_DESK_STATUS_LABELS.get(status, LAB_DESK_STATUS_LABELS["draft"])


def priority_label(priority):
    return PRIORITY_LABELS.get(priority, PRIORITY_LABELS["routine"])


def serialize_patient_identity(patient):
    """Identity only, and only what the bench genuinely uses.

    'mrn' is the vocabulary the Doctor Desk, the Front Desk and the Cashier
    worklist all use for hospital.patient.identification_code; the bench labels
    a specimen with the same number, so it keeps the same name here.

    Age and sex are included because a reference range is read against them and
    a mislabelled specimen is caught by them. Nothing else is: no phone, no
    address, no next of kin, no blood group, no allergy, no diagnosis. A
    laboratory needs to identify a sample, not to review a chart.
    """
    if not patient:
        return None
    return {
        "id": patient.id,
        "name": patient.name,
        "mrn": patient.identification_code or None,
        "age": patient.age,
        "gender": selection_value(patient.gender),
    }


def serialize_ordered_test(line):
    """One ordered test, as the bench has to perform it."""
    test = line.test_id
    return {
        # The request LINE id -- the identity every future slice needs, because
        # hospital.laboratory.result.line.request_line_id is what makes a result
        # traceable to the exact ordered occurrence of a test.
        "id": line.id,
        "test_id": test.id,
        "name": test.name,
        "code": test.code or None,
        "category": selection_value(test.category),
        # Derived on the line by the model, falling back to the catalogue
        # default -- exactly the resolution hospital.laboratory.result.line
        # applies when it copies the specimen forward.
        "sample_type": selection_value(line.sample_type or test.sample_type),
        "special_instruction": line.special_instruction or None,
        "sequence": line.sequence,
    }


def _encounter_context(request):
    """The visit, reached through the ENCOUNTER and never the appointment.

    hospital.encounter is the episode anchor, it carries name/department, and a
    laboratory technician can read it. hospital.appointment they cannot read at
    all -- see this module's docstring.
    """
    encounter = request.encounter_id
    if not encounter:
        return {"encounter_code": None, "department": None}
    return {
        "encounter_code": encounter.name or None,
        "department": m2o_value(encounter.department_id),
    }


def _result_summary(request):
    """How much reporting already exists against this request.

    Read from the `result_ids` One2many rather than from the model's
    `result_count`, which issues a `search_count` PER RECORD -- one extra query
    per queue row. The One2many is prefetched across the whole recordset in a
    single read, which is what keeps a hundred-row queue at a fixed query cost.

    STATE COUNTS ONLY, no values and no interpretation. A queue needs to know
    that reporting has started; the values belong to the detail payload's
    `result`, which only the selected request carries.
    """
    results = request.result_ids
    return {
        "result_count": len(results),
        "released_count": len(results.filtered(lambda r: r.state == "released")),
    }


def serialize_queue_row(request):
    """One bench queue row. Read-only, clinical and operational only."""
    status = lab_desk_status(request)
    lines = request.line_ids
    payload = {
        "id": request.id,
        "request_code": request.name,
        # The AUTHORITATIVE value travels next to the derived one, always. A
        # client that needs to reason about the workflow reads `state`; the
        # desk renders `status`. Neither has to be inferred from the other.
        "state": request.state,
        "status": status,
        "status_label": status_label(status),
        "priority": selection_value(request.priority),
        "priority_label": priority_label(request.priority),
        "request_date": date_value(request.request_date),
        "created_at": datetime_value(request.create_date),
        # A BOOLEAN VERDICT. Never an amount, never a payer, never a message.
        "billing_blocked": bool(request.billing_blocked),
        "active": bool(request.active),
        "patient": serialize_patient_identity(request.patient_id),
        "ordering_physician": m2o_value(request.physician_id),
        "test_count": len(lines),
        # The one-line "what is this" a queue row needs, built from the same
        # catalogue names the detail panel lists. Kept server-side so the queue
        # and the panel can never disagree about what was ordered.
        "tests_summary": " · ".join(line.test_id.name for line in lines) or None,
    }
    payload.update(_encounter_context(request))
    payload.update(_result_summary(request))
    return payload


def lab_desk_results(request):
    """Every result of one request the CALLER may read, oldest first.

    A search rather than `request.result_ids`, so the caller's own record rules
    are applied in the query and the ORM's active_test drops archived results --
    the same resolution _load_request uses for the request itself. Cancelled
    results are deliberately INCLUDED: a cancelled result is still an active
    record that covers the ordered lines, and hiding it would let the desk start
    a second one beside it.
    """
    return request.env["hospital.laboratory.result"].search(
        [("request_id", "=", request.id)], order="id asc"
    )


def _selection_label(record, field_name):
    """The model's own wording for a selection value, or None."""
    value = record[field_name]
    if not value:
        return None
    labels = dict(record._fields[field_name]._description_selection(record.env))
    return labels.get(value, value)


def serialize_result_line(line):
    """One result line: the ordered test it reports on, and the values typed.

    STRUCTURE IS READ-ONLY ON THE WIRE. `request_line_id`, the test and the
    specimen are what the model derived from the request; they are here so the
    bench can see WHAT it is reporting on, and the save endpoint refuses every
    one of them as input.
    """
    test = line.test_id
    return {
        "id": line.id,
        "request_line_id": line.request_line_id.id or None,
        "test": {
            "id": test.id,
            "name": test.name,
            "code": test.code or None,
        },
        # Derived by the model from the ordered line, falling back to the
        # catalogue for a legacy line that never had it copied forward.
        "sample_type": selection_value(line.sample_type or test.sample_type),
        "result_value": line.result_value or None,
        "unit": line.unit or None,
        "reference_range": line.reference_range or None,
        "abnormal_flag": selection_value(line.abnormal_flag),
        "notes": line.notes or None,
        "sequence": line.sequence,
    }


def serialize_operational_result(result):
    """THE Laboratory Desk result payload. Entry fields only.

    WHAT IS ABSENT, AND WHY. No validation or release metadata, because the
    model records none (there is no validated_by, released_at or entered_at).
    No technician or physician, because neither is editable here and neither is
    needed to type a value. No charge, delivery or billing field of any kind:
    result entry touches no money -- hospital_billing overrides action_validate,
    not action_mark_entered.

    `abnormal_flag_options` comes from the MODEL'S OWN selection, so the browser
    offers exactly the values Odoo can store and never a second list that could
    drift from it -- in particular never an invented "not assessed" value.
    """
    Line = result.env["hospital.laboratory.result.line"]
    return {
        "id": result.id,
        "name": result.name,
        "state": result.state,
        "state_label": _selection_label(result, "state"),
        "result_date": date_value(result.result_date),
        "interpretation": result.interpretation or None,
        "remarks": result.remarks or None,
        "lines": [serialize_result_line(line) for line in result.line_ids],
        "abnormal_flag_options": [
            {"value": value, "label": label}
            for value, label in Line._fields["abnormal_flag"]._description_selection(
                result.env
            )
        ],
    }


def serialize_request_detail(request):
    """One request, in full, for the detail panel.

    The queue row plus the ordered tests and the ordering clinician's own
    words. Deliberately a SUPERSET of the row, so the panel never has to merge
    two shapes and a selected row can render immediately from what the queue
    already returned.

    THE OPERATIONAL RESULT (Slice 3). The desk works ONE result per request.
    Exactly one existing result is that result, whatever its state; none is
    `null`. MORE THAN ONE is reported as `result_conflict` with `result: null`
    rather than by picking one: the model allows several results per request,
    and choosing "the newest" would let the bench type into a record that may
    not be the one the request's completion rule will count. Historical and
    repeat results are never exposed here.
    """
    payload = serialize_queue_row(request)
    results = lab_desk_results(request)
    payload.update(
        {
            "tests": [serialize_ordered_test(line) for line in request.line_ids],
            # The ordering clinician's instructions to the bench. `instructions`
            # is the model's operational field and `clinical_notes` the
            # indication the Doctor Desk writes; both are the doctor's own text
            # about THIS order, and the bench acts on them.
            "clinical_notes": request.clinical_notes or None,
            "instructions": request.instructions or None,
            "result": (
                serialize_operational_result(results) if len(results) == 1 else None
            ),
            "result_conflict": len(results) > 1,
        }
    )
    return payload


def serialize_worklist(requests, filters, meta, capabilities):
    """THE worklist response shape."""
    return {
        "rows": [serialize_queue_row(request) for request in requests],
        "filters": filters,
        "meta": meta,
        "capabilities": capabilities,
    }


def serialize_session(env, capabilities):
    """Who is signed in, and whether the bench opens for them.

    Identity and capability only -- both facts the caller already knows about
    themselves. No group list is returned: `lab_desk` is the only question this
    workstation asks, and enumerating a user's roles would tell a client more
    about the security model than it needs to render a header.
    """
    user = env.user
    return {
        "user": {"id": user.id, "name": user.name},
        "company": m2o_value(user.company_id),
        "capabilities": capabilities,
    }

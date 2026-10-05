"""Admissions Desk payloads: the inpatient census, the lanes and the bed board.

READ ONLY (Admissions Slice 1). Nothing here writes, repairs or audits.

WHAT THIS MODULE IS
-------------------
The desk's own read model over the Slice 0 authority: hospital.admission,
hospital.ward / .room / .bed, hospital.admission.transfer and the encounter the
admission belongs to. It consumes those facts; it never re-decides them. No
lane is persisted, no inconsistency is corrected, no clearance policy is
re-implemented.

ONE CLASSIFIER, ONE PASS
------------------------
classify() is THE lane function. The worklist runs it once over every
admission in its filter scope and derives the rows, the lane filter and the
lane counts from that single pass (the Pharmacy Desk pattern), so a badge can
never disagree with the list under it. Every admission lands in exactly one
lane, checked in LANE_ORDER.

The same is true of the bed board: build_bed_board() computes every bed row
and its flags once, and the /wards rollups are summed from those rows rather
than counted separately.

WHO SEES WHAT -- AND THE ONE PLACE sudo() IS USED
-------------------------------------------------
Admissions are always SEARCHED through the caller's own record rules. The desk
never widens the census.

The ENCOUNTER is different. hospital.encounter carries its own record rules and
they do not line up with the admission's: a ward nurse may read an admission on
their ward while being refused the visit it belongs to. The lane classifier
still needs four INTEGRITY facts about that visit -- whose patient, which
company, which state, which type -- or a nurse's census would silently show a
broken admission as healthy. Those four facts, the appointment id (for pending
order counts run under the CALLER's rights) and the amount-free clearance
verdict are read through sudo(), for admissions the caller already holds, and
never serialized as data about the visit. The visit's reference, state and type
are shown only when the caller can read the encounter themselves
(`encounter.restricted` says which). This is the same narrow shape Slice 0's own
constraints use (`_check_active_admission_has_encounter`).

BED BOARD PRIVACY
-----------------
A bed row names a patient ONLY when the caller can read the admission that
holds it. The admission is resolved by searching hospital.admission under the
caller's rules; a bed whose admission is hidden shows `occupied` and nothing
else -- no admission id, no reference, no name, no MRN, no length of stay. The
free-text filter `q` is applied in Python AFTER redaction, so a scoped user
cannot probe for a hidden patient's name by searching for it and watching
which beds come back.

CONFIDENTIALITY
---------------
No amount, rate, price, charge, bill, account, payer or insurance figure is
read into a payload. Billing crosses as a boolean verdict and a Selection KEY
from hospital.encounter.reception_clearance_ok / reception_clearance_state --
the encounter-wide verdict of hospital.billing.engine.check_financial_clearance,
computed there, not here -- plus a sentence from a CLOSED SET OF LITERALS
defined below. No nursing note body, MAR entry, care plan text or diagnosis
history is serialized; nursing appears as counts and latest timestamps.

The inpatient financial comparison (Slice 3: actual delivered care vs the money
available against it) crosses the same way -- `financial`: a state key, three
booleans and fixed review sentences from the model's own amount-free
projection. The amounts stay in hospital_admission, for the money roles'
own workflows.
"""
import logging

from odoo import fields
from odoo.exceptions import AccessError
from odoo.service.model import PG_CONCURRENCY_EXCEPTIONS_TO_RETRY

from odoo.addons.hospital_admission.models.admission_authority import (
    ADMISSION_ACTIVE_STATES,
    episode_closed_states,
)

from .api_response import datetime_value

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lanes. NONE OF THESE ARE DATABASE VALUES.
# ---------------------------------------------------------------------------
# Checked in this order; the first that matches wins, so every admission lands
# in exactly one lane. needs_review is first on purpose: a broken active
# admission must never be shown as a healthy one.
LANE_ORDER = (
    "needs_review",
    "awaiting_bed",
    "draft",
    # Slice 4: medically ready, still in the bed, awaiting the administrative
    # discharge -- and the cashier, where money is owed. Ahead of admitted and
    # transferred so a patient waiting to go home is never lost among those
    # staying.
    "discharge_pending",
    "admitted",
    "transferred",
    "discharged",
    "cancelled",
)

LANE_LABELS = {
    "needs_review": "Needs review",
    "awaiting_bed": "Awaiting bed",
    "draft": "Draft",
    "discharge_pending": "Ready for discharge",
    "admitted": "Admitted",
    "transferred": "Transferred",
    "discharged": "Discharged",
    "cancelled": "Cancelled",
}

# The default queue: work still open on the ward. Discharged and cancelled are
# history, reachable by asking for them; their counts are always reported.
ACTIVE_LANES = (
    "needs_review", "awaiting_bed", "draft", "discharge_pending", "admitted", "transferred",
)

KNOWN_ADMISSION_STATES = ("draft", "admitted", "transferred", "discharged", "cancelled")

# Why an active admission is in needs_review. FIXED SENTENCES; the client never
# invents wording and nothing here names a patient or an amount.
REVIEW_MESSAGES = {
    "unknown_state": "The admission is in a state this desk does not recognise.",
    "ward_missing": "The admission names no ward.",
    "room_missing": "The admission names no room.",
    "bed_missing": "The admission names no bed.",
    "bed_not_occupied": "The admission's bed is not marked occupied.",
    "bed_pointer_mismatch": "The admission's bed records a different current admission.",
    "bed_room_mismatch": "The bed is not in the room the admission names.",
    "room_ward_mismatch": "The room is not in the ward the admission names.",
    "bed_ward_mismatch": "The bed is not in the ward the admission names.",
    "encounter_missing": "The admission is active but is linked to no visit.",
    "encounter_patient_mismatch": "The linked visit belongs to a different patient.",
    "encounter_company_mismatch": "The linked visit belongs to a different company.",
    "encounter_closed": "The linked visit is completed, closed or cancelled.",
    "encounter_not_inpatient": "The linked visit is not typed as inpatient.",
}

# Bed-board inconsistencies. Surfaced, never repaired.
BED_FLAG_MESSAGES = {
    "occupied_without_admission": "The bed is marked occupied but records no admission.",
    "pointer_without_occupancy": "The bed records an admission but is not marked occupied.",
    "admission_without_occupied_bed": "An active admission names this bed, but the bed is not marked occupied.",
    "pointer_mismatch": "The bed's recorded admission and the admission naming this bed disagree.",
    "room_mismatch": "An admission naming this bed names a different room.",
    "ward_mismatch": "An admission naming this bed names a different ward.",
    "active_admission_on_unavailable_bed": "An active admission names a bed that is inactive or out of service.",
    "multiple_active_claims": "More than one active admission names this bed.",
}

BED_STATES = ("available", "occupied", "cleaning", "maintenance", "blocked")

# Pending orders: states in which the work is ordered and not yet finished.
PENDING_LAB_STATES = ("requested", "sample_collected", "in_progress")
PENDING_RAD_STATES = ("requested", "scheduled", "in_progress")

# ---------------------------------------------------------------------------
# Clearance vocabulary: a CLOSED SET OF LITERAL STRINGS.
# ---------------------------------------------------------------------------
# Keyed by hospital.encounter.reception_clearance_state. No interpolation, no
# format(), nothing read from a record -- so no change to the billing engine's
# own wording can ever reach this desk. None means "nothing to report".
CLEARANCE_MESSAGES = {
    "not_required": None,
    "cleared": None,
    "credit_authorized": None,
    "sponsor_cleared": None,
    "emergency_bypass": None,
    "inpatient_credit": None,
    "pending": "Financial clearance for this visit is pending at the front desk or cashier.",
}
CLEARANCE_FALLBACK_MESSAGE = "Financial clearance has not been confirmed for this visit."
CLEARANCE_NOT_APPLICABLE_MESSAGE = (
    "This admission is not linked to a visit, so its financial clearance cannot be "
    "determined."
)


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------
def _safe(read, default=None):
    """An unreadable relation renders as absent. Concurrency errors propagate
    so Odoo's HTTP layer can replay the request."""
    try:
        return read()
    except PG_CONCURRENCY_EXCEPTIONS_TO_RETRY:
        raise
    except Exception:  # noqa: BLE001
        return default


def _selection_label(record, field_name, value=None):
    value = record[field_name] if value is None else value
    if not value:
        return None
    selection = record._fields[field_name].selection
    if callable(selection):
        selection = selection(record)
    return dict(selection).get(value, value)


def lane_label(lane):
    return LANE_LABELS.get(lane, lane)


def _named(record, code_field="code"):
    if not record:
        return None
    return {
        "id": record.id,
        "code": (record[code_field] or None) if code_field in record._fields else None,
        "name": record.name or None,
    }


def serialize_patient_identity(patient):
    """Identity only: name, chart number, age and sex."""
    if not patient:
        return None
    return {
        "id": patient.id,
        "name": patient.name,
        "mrn": patient.identification_code or None,
        "age": patient.age or None,
        "gender": patient.gender or None,
    }


def length_of_stay(admission, now=None):
    """Elapsed stay as days + hours. None where a stay has not started or never
    happened (draft, cancelled). Ongoing for an active admission."""
    start = admission.admission_date
    if not start or admission.state in ("draft", "cancelled"):
        return None
    ongoing = admission.state in ADMISSION_ACTIVE_STATES
    end = (now or fields.Datetime.now()) if ongoing else admission.discharge_date
    if not end or end < start:
        return None
    total_hours = int((end - start).total_seconds() // 3600)
    return {"days": total_hours // 24, "hours": total_hours % 24, "ongoing": ongoing}


# ---------------------------------------------------------------------------
# Encounter facts: integrity only (see the module docstring)
# ---------------------------------------------------------------------------
def encounter_facts(admissions):
    """{admission_id: facts|None} for a batch, in ONE sudo read of the visits.

    Facts are ids, Selection keys and one boolean. Nothing here is serialized
    as-is; serialize_encounter() decides what the caller may see.
    """
    env = admissions.env
    visits = admissions.sudo().mapped("encounter_id")
    readable = set()
    if visits:
        # What the CALLER may read, in one search under their own rules.
        readable = set(
            env["hospital.encounter"]
            .with_context(active_test=False)
            .search([("id", "in", visits.ids)])
            .ids
        )
    facts = {}
    for admission in admissions:
        visit = admission.sudo().encounter_id
        if not visit:
            facts[admission.id] = None
            continue
        facts[admission.id] = {
            "id": visit.id,
            "patient_id": visit.patient_id.id,
            "company_id": visit.company_id.id,
            "state": visit.state,
            "type": visit.encounter_type,
            "appointment_id": visit.appointment_id.id or None,
            "readable": visit.id in readable,
            "_record": visit,
        }
    return facts


# ---------------------------------------------------------------------------
# THE classifier
# ---------------------------------------------------------------------------
def review_reasons(admission, encounter, closed_states):
    """Every integrity fault of an ACTIVE admission, in a stable order.

    Reads the admission's own columns, the bed/room/ward catalogue (no record
    rules) and the encounter FACTS. Never raises on a missing relation.
    """
    reasons = []
    ward, room, bed = admission.ward_id, admission.room_id, admission.bed_id
    if not ward:
        reasons.append("ward_missing")
    if not room:
        reasons.append("room_missing")
    if not bed:
        reasons.append("bed_missing")
    if bed:
        if bed.state != "occupied":
            reasons.append("bed_not_occupied")
        if bed.current_admission_id.id != admission.id:
            reasons.append("bed_pointer_mismatch")
        if room and bed.room_id != room:
            reasons.append("bed_room_mismatch")
        if ward and bed.ward_id != ward:
            reasons.append("bed_ward_mismatch")
    if room and ward and room.ward_id != ward:
        reasons.append("room_ward_mismatch")
    if not encounter:
        reasons.append("encounter_missing")
    else:
        if encounter["patient_id"] != admission.patient_id.id:
            reasons.append("encounter_patient_mismatch")
        if encounter["company_id"] != admission.company_id.id:
            reasons.append("encounter_company_mismatch")
        if encounter["state"] in closed_states:
            reasons.append("encounter_closed")
        if encounter["type"] != "inpatient":
            reasons.append("encounter_not_inpatient")
    return reasons


def classify(admission, encounter, closed_states):
    """-> (lane, [reason codes]). Exactly one lane per admission.

    DISCHARGED AND CANCELLED ARE HISTORY. They are never needs_review, even
    with no visit -- that is exactly the shape of the legacy ADM00001, which
    predates encounter linkage and must read as an ordinary discharge.
    """
    state = admission.state
    if state in ADMISSION_ACTIVE_STATES:
        reasons = review_reasons(admission, encounter, closed_states)
        if reasons:
            return "needs_review", reasons
        if admission.medical_discharge_ready:
            return "discharge_pending", []
        return state, []
    if state == "draft":
        return ("draft" if admission.bed_id else "awaiting_bed"), []
    if state == "discharged":
        return "discharged", []
    if state == "cancelled":
        return "cancelled", []
    return "needs_review", ["unknown_state"]


def classify_batch(admissions):
    """[(admission, lane, reasons, encounter_facts)] in ONE pass."""
    closed = episode_closed_states(admissions.env["hospital.encounter"])
    facts = encounter_facts(admissions)
    out = []
    for admission in admissions:
        encounter = facts.get(admission.id)
        lane, reasons = classify(admission, encounter, closed)
        out.append((admission, lane, reasons, encounter))
    return out


# ---------------------------------------------------------------------------
# Clearance
# ---------------------------------------------------------------------------
def serialize_clearance(encounter):
    """The amount-free verdict for the admission's visit.

    `billing_blocked` is NULL -- not False -- when there is no visit: the
    engine has nothing to answer for, and a False would be a fabricated
    "nothing owed". `admission_clearance_required` and
    `discharge_clearance_required` report the CURRENT WORKFLOW POLICY: Slice 0
    enforces no financial gate on admitting or discharging, so both are False
    for every record. They exist so Slice 4 can switch them on without a
    contract change, and they must not be read as statements about any
    particular balance.
    """
    policy = {"admission_clearance_required": False, "discharge_clearance_required": False}
    if not encounter:
        return dict(
            policy,
            billing_blocked=None,
            clearance_state="not_applicable",
            clearance_message=CLEARANCE_NOT_APPLICABLE_MESSAGE,
        )
    visit = encounter["_record"]
    state = _safe(lambda: visit.reception_clearance_state or None)
    cleared = _safe(lambda: bool(visit.reception_clearance_ok))
    if cleared is None:
        return dict(
            policy,
            billing_blocked=None,
            clearance_state="unavailable",
            clearance_message=CLEARANCE_FALLBACK_MESSAGE,
        )
    blocked = not cleared
    message = None
    if blocked:
        message = CLEARANCE_MESSAGES.get(state, CLEARANCE_FALLBACK_MESSAGE) or CLEARANCE_FALLBACK_MESSAGE
    return dict(
        policy,
        billing_blocked=blocked,
        clearance_state=state or "unavailable",
        clearance_message=message,
    )


# ---------------------------------------------------------------------------
# Rows and detail
# ---------------------------------------------------------------------------
def serialize_location(admission):
    bed = admission.bed_id
    return {
        "ward": _named(admission.ward_id),
        "room": _named(admission.room_id),
        "bed": dict(_named(bed), state=bed.state) if bed else None,
    }


def serialize_encounter(encounter):
    """The visit as the CALLER may see it."""
    if not encounter:
        return {"available": False, "restricted": False, "legacy": True,
                "reference": None, "state": None, "state_label": None,
                "type": None, "type_label": None}
    if not encounter["readable"]:
        return {"available": True, "restricted": True, "legacy": False,
                "reference": None, "state": None, "state_label": None,
                "type": None, "type_label": None}
    visit = encounter["_record"]
    return {
        "available": True,
        "restricted": False,
        "legacy": False,
        "reference": visit.name,
        "state": visit.state,
        "state_label": _selection_label(visit, "state"),
        "type": visit.encounter_type,
        "type_label": _selection_label(visit, "encounter_type"),
    }


def transfer_counts(admissions):
    """{admission_id: count} in one search under the caller's rules."""
    counts = {admission.id: 0 for admission in admissions}
    if not admissions:
        return counts
    transfers = admissions.env["hospital.admission.transfer"].search(
        [("admission_id", "in", admissions.ids)]
    )
    for transfer in transfers:
        counts[transfer.admission_id.id] = counts.get(transfer.admission_id.id, 0) + 1
    return counts


# Lanes an admission may be admitted from (Slice 2). A draft with or without a
# bed named; never needs_review -- a broken record is reviewed, not admitted.
ADMITTABLE_LANES = ("awaiting_bed", "draft")

# Lanes a patient may be transferred from (Slice 3): in a bed and healthy. A
# needs_review admission is reviewed first -- moving a patient whose bed
# pointer already disagrees would move the disagreement.
TRANSFERABLE_LANES = ("admitted", "transferred", "discharge_pending")

# Lanes a request may be cancelled from (Slice 3): the draft lanes only. After
# admission a stay ends by discharge, never by un-requesting it.
CANCELLABLE_LANES = ("awaiting_bed", "draft")

# Slice 4: the lane a discharge can be finalized from.
FINALIZABLE_LANES = ("discharge_pending",)

# The discharge checks, as the MODEL names them. Fixed sentences only.
def _checks(codes, messages):
    return [{"code": code, "message": messages.get(code, code)} for code in codes]


def serialize_discharge(admission, lane, may_finalize=False, financial=None):
    """The discharge block (Slice 4): medical readiness, the model's blocking
    checks and warnings, and whether the clerk may TRY to finalize.

    can_finalize_discharge is an AFFORDANCE: the role, the lane, no blocking
    check, and a financial state that is not needs_review. A `due` account
    still offers the attempt -- the server refuses it with
    admission_settlement_required and posts the stay to date, so the cashier
    has every started bed-day to collect against. The model re-checks all of
    it under its locks.
    """
    Admission = type(admission)
    blocking, warnings = _safe(admission._discharge_checks, default=(["unavailable"], []))
    financial = financial or {}
    return {
        "medical_ready": bool(admission.medical_discharge_ready),
        "medical_ready_at": datetime_value(admission.medical_discharge_at),
        "medical_ready_by": admission.medical_discharge_by_id.name if admission.medical_discharge_by_id else None,
        "blocking": _checks(
            [code for code in blocking if code != "not_medically_ready"],
            Admission.DISCHARGE_BLOCKING_MESSAGES,
        ),
        "warnings": _checks(warnings, Admission.DISCHARGE_WARNING_MESSAGES),
        "can_finalize_discharge": bool(
            may_finalize
            and lane in FINALIZABLE_LANES
            and not blocking
            and financial.get("financial_state") != "needs_review"
        ),
    }


def _may_cancel_this(admission, lane, may_cancel):
    """Role half AND ownership half. The ownership test is the model's own."""
    if not (may_cancel and lane in CANCELLABLE_LANES):
        return False
    return bool(_safe(admission._desk_may_cancel_request, default=False))


def serialize_admission_clearance(admission, lane):
    """Advance slice: may this REQUEST be given a bed, financially? The
    model's verdict (hospital.admission._admission_financial_clearance) as a
    state, a boolean and a fixed sentence -- NO amount, so every desk role,
    the ward nurse included, may see WHY Admit is not offered. The figures
    (estimate, advance received, remaining) are served only on the clerk's
    settlement route. None once the patient is in a bed."""
    if lane not in ADMITTABLE_LANES:
        return None
    verdict = _safe(admission._admission_financial_clearance, default=None)
    if verdict is None:
        return {
            "state": "unavailable",
            "cleared": False,
            "message": "The admission's financial readiness could not be determined.",
        }
    return {"state": verdict["state"], "cleared": bool(verdict["cleared"]), "message": verdict["message"]}


def serialize_row(
    admission, lane, reasons, encounter, transfers=0, now=None,
    may_admit=False, may_transfer=False, may_cancel=False, may_finalize=False,
):
    clearance = serialize_admission_clearance(admission, lane)
    return {
        "id": admission.id,
        "reference": admission.name,
        "state": admission.state,
        # Optimistic concurrency: echoed back by every desk mutation.
        "workflow_revision": admission.workflow_revision,
        # AFFORDANCES ONLY. The role may act AND this record is in a lane that
        # allows it. The model re-checks every condition under its locks.
        # Advance slice: and only once financially cleared for admission.
        "can_admit": bool(
            may_admit and lane in ADMITTABLE_LANES and clearance is not None and clearance["cleared"]
        ),
        "admission_clearance": clearance,
        "can_transfer": bool(may_transfer and lane in TRANSFERABLE_LANES),
        "can_cancel_request": _may_cancel_this(admission, lane, may_cancel),
        # Slice 4. Medical readiness is a fact the census shows; whether the
        # discharge may be FINALIZED is decided on the detail, with its checks.
        "medical_discharge_ready": bool(admission.medical_discharge_ready),
        "state_label": _selection_label(admission, "state"),
        "lane": lane,
        "lane_label": lane_label(lane),
        "review_reasons": [
            {"code": code, "message": REVIEW_MESSAGES.get(code, code)} for code in reasons
        ],
        "patient": serialize_patient_identity(admission.patient_id),
        "physician": (
            {"id": admission.physician_id.id, "name": admission.physician_id.name}
            if admission.physician_id else None
        ),
        "location": serialize_location(admission),
        "admitted_at": datetime_value(admission.admission_date),
        "expected_discharge_at": datetime_value(admission.expected_discharge_date),
        "discharged_at": datetime_value(admission.discharge_date),
        "length_of_stay": length_of_stay(admission, now),
        "encounter": serialize_encounter(encounter),
        "transfer_count": transfers,
        "billing_blocked": serialize_clearance(encounter)["billing_blocked"],
    }


def _count_and_latest(env, model, domain, stamp_field):
    """Count + latest timestamp under the CALLER's rights; None if refused."""
    if model not in env:
        return None
    Model = env[model]
    try:
        records = Model.search(domain, order="%s desc, id desc" % stamp_field)
    except AccessError:
        return None
    latest = records[:1][stamp_field] if records else None
    return {"count": len(records), "latest_at": datetime_value(latest)}


def _pending_count(env, model, appointment_id, states):
    if not appointment_id or model not in env:
        return None
    try:
        return env[model].search_count(
            [("appointment_id", "=", appointment_id), ("state", "in", list(states))]
        )
    except AccessError:
        return None


def nursing_summary(admission):
    env = admission.env
    domain = [("admission_id", "=", admission.id)]
    return {
        "rounds": _count_and_latest(env, "hospital.nursing.round", domain, "round_datetime"),
        "notes": _count_and_latest(env, "hospital.nursing.note", domain, "create_date"),
        "care_plans": _count_and_latest(env, "hospital.nursing.care.plan", domain, "create_date"),
        "medication_administrations": _count_and_latest(
            env, "hospital.medication.administration", domain, "create_date"
        ),
    }


def serialize_transfer(transfer):
    def place(ward, room, bed):
        return {"ward": _named(ward), "room": _named(room), "bed": _named(bed)}

    return {
        "id": transfer.id,
        "transferred_at": datetime_value(transfer.transfer_date),
        "from": place(transfer.from_ward_id, transfer.from_room_id, transfer.from_bed_id),
        "to": place(transfer.to_ward_id, transfer.to_room_id, transfer.to_bed_id),
        "transferred_by": transfer.transferred_by.name if transfer.transferred_by else None,
        "reason": transfer.reason or None,
    }


# The inpatient FINANCIAL STATE (Slice 3), as the desk may show it: a state,
# three booleans and fixed review sentences, from
# hospital.admission._inpatient_financial_status(). No amount crosses here --
# the model's projection has none, and this adds none. If the projection
# cannot be read at all, the desk says so rather than guessing "covered".
FINANCIAL_UNAVAILABLE = {
    "financial_state": "needs_review",
    "billing_blocked": True,
    "settlement_required": False,
    "refund_due": False,
    "patient_credit": False,
    "review_reasons": [
        {
            "code": "financial_unavailable",
            "message": "The inpatient financial state could not be determined.",
        }
    ],
}


def serialize_financial(admission):
    status = _safe(admission._inpatient_financial_status, default=None)
    if status is None:
        _logger.warning("Admissions desk: financial status unavailable for admission=%s", admission.id)
        return dict(FINANCIAL_UNAVAILABLE)
    return {
        "financial_state": status["financial_state"],
        "billing_blocked": bool(status["billing_blocked"]),
        "settlement_required": bool(status["settlement_required"]),
        "refund_due": bool(status["refund_due"]),
        "patient_credit": bool(status.get("patient_credit")),
        "review_reasons": [
            {"code": reason["code"], "message": reason["message"]}
            for reason in status["review_reasons"]
        ],
    }


def serialize_detail(
    admission, may_admit=False, may_transfer=False, may_cancel=False, may_finalize=False,
):
    """One admission, for a caller who can already read it."""
    env = admission.env
    [(record, lane, reasons, encounter)] = classify_batch(admission)
    bed = admission.bed_id
    appointment_id = encounter["appointment_id"] if encounter else None

    # The admission's OWN diagnosis only -- never the patient's history -- and
    # by disease name + code, not display_name: hospital.patient.diagnosis's
    # display_name appends the patient's MRN and name, which this payload
    # already carries once, deliberately, in `patient`. Read under the caller's
    # rules (the model has its own); refused reads say so rather than vanish.
    diagnosis = _safe(
        lambda: (
            {
                "id": admission.diagnosis_id.id,
                "name": admission.diagnosis_id.disease_id.name or None,
                "code": admission.diagnosis_id.disease_code or None,
                "restricted": False,
            }
            if admission.diagnosis_id else None
        ),
        default={"id": None, "name": None, "code": None, "restricted": True},
    )
    transfers = env["hospital.admission.transfer"].search(
        [("admission_id", "=", admission.id)], order="transfer_date asc, id asc"
    )

    ownership = None
    if bed:
        records_us = bed.current_admission_id.id == admission.id
        ownership = {
            "bed_state": bed.state,
            "bed_records_this_admission": records_us,
            # Only an ACTIVE admission is supposed to hold its bed; for a
            # draft or a finished stay the question does not apply.
            "consistent": (
                bed.state == "occupied" and records_us
                if admission.state in ADMISSION_ACTIVE_STATES else None
            ),
        }

    financial = serialize_financial(admission)
    return dict(
        serialize_row(
            admission, lane, reasons, encounter, transfers=len(transfers),
            may_admit=may_admit, may_transfer=may_transfer, may_cancel=may_cancel,
            may_finalize=may_finalize,
        ),
        admission_reason=admission.admission_reason or None,
        diagnosis=diagnosis,
        bed_ownership=ownership,
        transfers=[serialize_transfer(t) for t in transfers],
        nursing=nursing_summary(admission),
        linkages={
            "procedure_requests": _count_and_latest(
                env, "hospital.procedure.request", [("admission_id", "=", admission.id)], "create_date"
            ),
            "inventory_movements": _count_and_latest(
                env, "hospital.stock.movement", [("admission_id", "=", admission.id)], "create_date"
            ),
            "pending_laboratory_requests": _pending_count(
                env, "hospital.laboratory.request", appointment_id, PENDING_LAB_STATES
            ),
            "pending_radiology_requests": _pending_count(
                env, "hospital.radiology.request", appointment_id, PENDING_RAD_STATES
            ),
        },
        clearance=serialize_clearance(encounter),
        financial=financial,
        discharge=(
            serialize_discharge(admission, lane, may_finalize=may_finalize, financial=financial)
            if admission.state in ADMISSION_ACTIVE_STATES else None
        ),
    )


# ---------------------------------------------------------------------------
# Bed board
# ---------------------------------------------------------------------------
def build_bed_board(env, bed_domain=None, now=None):
    """Every bed in scope, with its flags and (where readable) its occupant.

    ONE bed search, ONE admission search for the admissions naming these beds,
    ONE for the admissions the beds point at -- both under the caller's rules.
    Flags that need an admission are computed only from admissions the caller
    can read; a scoped role therefore sees a complete picture of its own wards
    and bed-only flags everywhere, and never learns that a hidden admission
    exists.
    """
    beds = env["hospital.bed"].with_context(active_test=False).search(
        list(bed_domain or []) + [("company_id", "in", env.companies.ids)],
        order="ward_id, room_id, name, id",
    )
    Admission = env["hospital.admission"].with_context(active_test=False)
    claims = Admission.search(
        [("bed_id", "in", beds.ids), ("state", "in", list(ADMISSION_ACTIVE_STATES))]
    ) if beds else Admission
    pointer_ids = [bed.current_admission_id.id for bed in beds if bed.current_admission_id]
    pointed = Admission.search([("id", "in", pointer_ids)]) if pointer_ids else Admission

    visible = {record.id: record for record in (claims | pointed)}
    claims_by_bed = {}
    for claim in claims:
        claims_by_bed.setdefault(claim.bed_id.id, []).append(claim)

    rows = []
    for bed in beds:
        pointer_id = bed.current_admission_id.id or None
        bed_claims = claims_by_bed.get(bed.id, [])
        flags = []
        if bed.state == "occupied" and not pointer_id:
            flags.append("occupied_without_admission")
        if pointer_id and bed.state != "occupied":
            flags.append("pointer_without_occupancy")
        if bed_claims and bed.state != "occupied":
            flags.append("admission_without_occupied_bed")
        pointed_visible = visible.get(pointer_id) if pointer_id else None
        if bed_claims and pointer_id and pointer_id not in [c.id for c in bed_claims]:
            flags.append("pointer_mismatch")
        elif pointed_visible and (
            pointed_visible.state not in ADMISSION_ACTIVE_STATES
            or pointed_visible.bed_id.id != bed.id
        ):
            flags.append("pointer_mismatch")
        elif bed_claims and not pointer_id and "occupied_without_admission" not in flags:
            flags.append("pointer_mismatch")
        if any(c.room_id != bed.room_id for c in bed_claims):
            flags.append("room_mismatch")
        if any(c.ward_id != bed.ward_id for c in bed_claims):
            flags.append("ward_mismatch")
        if bed_claims and (not bed.active or bed.state in ("cleaning", "maintenance", "blocked")):
            flags.append("active_admission_on_unavailable_bed")
        if len(bed_claims) > 1:
            flags.append("multiple_active_claims")

        occupant = pointed_visible if (
            pointed_visible and pointed_visible.state in ADMISSION_ACTIVE_STATES
        ) else (bed_claims[0] if bed_claims else None)

        rows.append({
            "id": bed.id,
            "code": bed.code or None,
            "name": bed.name,
            "type": bed.bed_type or None,
            "state": bed.state,
            "state_label": _selection_label(bed, "state"),
            "active": bed.active,
            "occupied": bed.state == "occupied",
            "ward": _named(bed.ward_id),
            "room": _named(bed.room_id),
            "can_view_admission": bool(occupant),
            "admission": (
                {
                    "id": occupant.id,
                    "reference": occupant.name,
                    "state": occupant.state,
                    "state_label": _selection_label(occupant, "state"),
                    "patient": serialize_patient_identity(occupant.patient_id),
                    "length_of_stay": length_of_stay(occupant, now),
                }
                if occupant else None
            ),
            "flags": [{"code": f, "message": BED_FLAG_MESSAGES[f]} for f in flags],
            "needs_review": bool(flags),
        })
    return rows


def bed_row_text(row):
    """Everything a caller may search a bed row by: only what it already shows."""
    parts = [row["code"], row["name"]]
    for key in ("ward", "room"):
        if row[key]:
            parts += [row[key]["code"], row[key]["name"]]
    admission = row["admission"]
    if admission:
        parts.append(admission["reference"])
        if admission["patient"]:
            parts += [admission["patient"]["name"], admission["patient"]["mrn"]]
    return " ".join(p for p in parts if p).lower()


def _empty_rollup():
    return {
        "bed_count": 0, "inactive_bed_count": 0,
        "available_count": 0, "occupied_count": 0, "cleaning_count": 0,
        "maintenance_count": 0, "blocked_count": 0, "needs_review_count": 0,
    }


def _add(rollup, row):
    if not row["active"]:
        rollup["inactive_bed_count"] += 1
    else:
        rollup["bed_count"] += 1
        rollup["%s_count" % row["state"]] += 1
    if row["needs_review"]:
        rollup["needs_review_count"] += 1


def build_ward_rollups(env, bed_rows):
    """Ward -> room hierarchy, summed from the SAME bed rows as the board."""
    wards = env["hospital.ward"].with_context(active_test=False).search(
        [("company_id", "in", env.companies.ids)], order="name, id"
    )
    rooms = env["hospital.room"].with_context(active_test=False).search(
        [("ward_id", "in", wards.ids)], order="name, id"
    )
    room_rollups = {room.id: _empty_rollup() for room in rooms}
    ward_rollups = {ward.id: _empty_rollup() for ward in wards}
    for row in bed_rows:
        if row["room"] and row["room"]["id"] in room_rollups:
            _add(room_rollups[row["room"]["id"]], row)
        if row["ward"] and row["ward"]["id"] in ward_rollups:
            _add(ward_rollups[row["ward"]["id"]], row)

    rooms_by_ward = {}
    for room in rooms:
        rooms_by_ward.setdefault(room.ward_id.id, []).append(dict(
            _named(room),
            type=room.room_type or None,
            active=room.active,
            **room_rollups[room.id],
        ))
    return [
        dict(
            _named(ward),
            type=ward.ward_type or None,
            type_label=_selection_label(ward, "ward_type"),
            department=(
                {"id": ward.department_id.id, "name": ward.department_id.name}
                if ward.department_id else None
            ),
            active=ward.active,
            room_count=len(rooms_by_ward.get(ward.id, [])),
            rooms=rooms_by_ward.get(ward.id, []),
            **ward_rollups[ward.id],
        )
        for ward in wards
    ]


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
ROLE_LABELS = (
    ("system_admin", "System Administrator"),
    ("manager", "Hospital Manager"),
    ("receptionist", "Admissions / Reception"),
    ("doctor", "Doctor"),
    ("front_desk_nurse", "Front Desk Nurse"),
    ("nurse", "Nurse"),
)


def desk_scope(roles):
    """What the record rules will let this user see, in words the UI can use.
    Descriptive only -- the rules decide, not this."""
    if roles.get("manager") or roles.get("system_admin") or roles.get("receptionist"):
        return "all_wards"
    if roles.get("doctor") and roles.get("nurse"):
        return "own_patients_and_permitted_departments"
    if roles.get("doctor"):
        return "own_patients"
    if roles.get("nurse"):
        return "permitted_departments"
    return "none"


def serialize_session(env, roles, capabilities):
    user = env.user
    labels = [label for key, label in ROLE_LABELS if roles.get(key)]
    scope = desk_scope(roles)
    departments = wards = None
    if scope in ("permitted_departments", "own_patients_and_permitted_departments"):
        department_ids = (
            user.yoya_permitted_department_ids.ids
            if "yoya_permitted_department_ids" in user._fields else []
        )
        departments = sorted(department_ids)
        wards = sorted(
            env["hospital.ward"].search([("department_id", "in", department_ids)]).ids
        ) if department_ids else []
    return {
        "user": {"id": user.id, "name": user.name},
        "roles": roles,
        "role_labels": labels,
        "desk_role": labels[0] if labels else None,
        "scope": scope,
        "permitted_department_ids": departments,
        "permitted_ward_ids": wards,
        "capabilities": capabilities,
        "read_only": True,
    }


# ---------------------------------------------------------------------------
# Doctor Desk handoff (Admissions Slice 2)
# ---------------------------------------------------------------------------
# What the DOCTOR sees about admission on a visit: whether one was requested,
# whether the patient is now an inpatient and where, and whether they may
# request one. Deliberately NOT an Admissions Desk: no lanes, no bed board, no
# transfer or discharge.
DOCTOR_ADMISSION_STATUS = {
    "draft": ("requested", "Admission requested - awaiting bed"),
    "admitted": ("admitted", "Inpatient - admitted"),
    # Slice 3: a moved patient is shown as moved, at their CURRENT location.
    "transferred": ("transferred", "Inpatient - transferred"),
    # Slice 4 (resolved from the readiness fact, not the state -- see below).
    "discharge_pending": (
        "discharge_pending", "Medically ready - awaiting administrative discharge",
    ),
    "discharged": ("discharged", "Discharged"),
    "cancelled": ("cancelled", "Admission request cancelled"),
}

# Why a doctor may NOT request, as fixed keys and sentences. Nothing here names
# another patient, doctor or ward.
DOCTOR_REQUEST_BLOCKS = {
    "not_authorized": "Only the visit's own doctor may request an admission.",
    "visit_not_open": "This visit is not open, so an admission cannot be requested from it.",
    "open_admission_exists": "This patient already has an open admission request or an active admission.",
}


def _doctor_may_request(appointment, visible_open):
    """(allowed, block_key). AFFORDANCE ONLY -- _desk_request_admission() is the
    authority and re-checks every condition under its lock."""
    from odoo.addons.hospital_admission.models.admission_authority import (
        ENCOUNTER_ADMISSIBLE_STATES,
        AdmissionDeskError,
    )

    Admission = appointment.env["hospital.admission"]
    try:
        Admission._desk_assert_may_request(appointment)
    except AdmissionDeskError:
        return False, "not_authorized"
    visit = appointment.sudo().encounter_id
    if not visit or visit.state not in ENCOUNTER_ADMISSIBLE_STATES:
        return False, "visit_not_open"
    # The duplicate check is the model's own, read as a BOOLEAN: an open
    # admission the doctor cannot see still blocks, without being described.
    if visible_open or Admission._desk_open_admission_for(visit.patient_id, visit.company_id):
        return False, "open_admission_exists"
    return True, None


def serialize_doctor_admission(appointment, now=None):
    """The admission block on the Doctor Desk visit detail.

    The admission itself is read under the DOCTOR's own record rules (the
    admitting physician, the appointment's doctor, or the visit's primary
    doctor). The one elevated read is the boolean duplicate check above.
    """
    env = appointment.env
    Admission = env["hospital.admission"].with_context(active_test=False)
    visit_id = appointment.sudo().encounter_id.id
    domain = [("appointment_id", "=", appointment.id)]
    if visit_id:
        domain = ["|", ("appointment_id", "=", appointment.id), ("encounter_id", "=", visit_id)]
    try:
        admission = Admission.search(domain, order="id desc", limit=1)
    except AccessError:
        admission = Admission

    open_states = ("draft",) + tuple(ADMISSION_ACTIVE_STATES)
    can_request, block = _doctor_may_request(
        appointment, bool(admission and admission.state in open_states)
    )
    status_key = admission.state if admission else None
    if admission and admission.state in ADMISSION_ACTIVE_STATES and admission.medical_discharge_ready:
        status_key = "discharge_pending"
    status, label = DOCTOR_ADMISSION_STATUS.get(status_key, ("none", "Not admitted")) if admission else ("none", "Not admitted")

    # Slice 4: the doctor may declare their inpatient medically ready.
    # Affordance only; _desk_request_medical_discharge() decides again.
    can_request_discharge = bool(
        admission
        and admission.state in ADMISSION_ACTIVE_STATES
        and not admission.medical_discharge_ready
        and _safe(admission._desk_may_request_medical_discharge, default=False)
    )
    warnings = []
    if can_request_discharge:
        _blocking, codes = _safe(admission._discharge_checks, default=([], []))
        warnings = _checks(codes, type(admission).DISCHARGE_WARNING_MESSAGES)

    # Slice 3: the doctor may withdraw their OWN request while it is still a
    # draft. Affordance only; the model decides again under its lock.
    can_cancel = bool(
        admission
        and admission.state == "draft"
        and _safe(admission._desk_may_cancel_request, default=False)
    )

    return {
        "status": status,
        "status_label": label,
        "can_cancel_request": can_cancel,
        "can_request_discharge": can_request_discharge,
        "discharge_warnings": warnings,
        "admission": (
            {
                "id": admission.id,
                "reference": admission.name,
                "state": admission.state,
                "state_label": _selection_label(admission, "state"),
                # Echoed back as expected_revision by cancel-request.
                "workflow_revision": admission.workflow_revision,
                "requested_at": datetime_value(admission.create_date),
                "admitted_at": (
                    datetime_value(admission.admission_date)
                    if admission.state in ADMISSION_ACTIVE_STATES + ("discharged",) else None
                ),
                "location": serialize_location(admission),
                "length_of_stay": length_of_stay(admission, now),
                "medical_discharge_at": datetime_value(admission.medical_discharge_at),
                "discharged_at": datetime_value(admission.discharge_date),
            }
            if admission else None
        ),
        "can_request": can_request,
        "request_blocked_reason": block,
        "request_blocked_message": DOCTOR_REQUEST_BLOCKS.get(block) if block else None,
    }

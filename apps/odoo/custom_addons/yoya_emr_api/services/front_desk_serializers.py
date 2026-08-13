"""Front Desk worklist serializers.

Purpose-built for the dense front-desk workstation: one compact row per visit,
no histories, no per-row queries.

WHAT THIS IS ALLOWED TO READ, and why it is safe for a Front Desk Nurse
----------------------------------------------------------------------
Front Desk Nurse implies Hospital Nurse, which holds read on hospital.patient,
hospital.appointment, hospital.encounter, hospital.department, hospital.doctor,
hospital.patient.evaluation, hospital.billing.account and hospital.charge.line.

Money is read from the ENCOUNTER's reception_* fields, not from the billing
account's amount_* fields. That is deliberate and load-bearing:

    encounter.reception_required_amount / _paid_amount / _outstanding_amount /
    _clearance_ok / _clearance_state / _clearance_message

are compute_sudo=True and carry NO ``groups=``, so any role that can read the
encounter can read them, and they are computed by the one authoritative engine
call (check_financial_clearance, persist=False) in yoya_reception_bridge.

By contrast billing_account.amount_received and amount_due_for_clearance carry
groups=OPERATIONAL_MONEY_READ, which does NOT include Nurse. Reading those here
would raise AccessError for exactly the intended user -- the same class of bug as
the cashier serializer that took money and then 403'd on
hospital.patient.evaluation. Only the two UNGROUPED account state fields
(operational_funding_state, financial_clearance_state) are read from the account.

PERFORMANCE
-----------
Every value below comes from a record that was already fetched in bulk by
prefetch_worklist(). Nothing in here searches, and nothing calls sudo().
"""
from .api_response import (
    datetime_value,
    float_value,
    m2o_value,
    selection_value,
)

# Canonical stage vocabulary, imported from the model that derives it so the API
# cannot drift from the workflow. Order is workflow order.
from odoo.addons.yoya_reception_bridge.models.hospital_appointment import (
    FRONT_DESK_STAGES,
)

STAGE_KEYS = tuple(key for key, _label in FRONT_DESK_STAGES)

# Stages the desk is actively working. 'completed' and 'cancelled' are excluded
# from the default window: the front desk works a live queue.
ACTIVE_STAGE_KEYS = (
    "new",
    "intake",
    "triage",
    "awaiting_cashier",
    "ready_doctor",
    "in_consultation",
)

# Counter keys. 'intake' folds 'new' into it -- a draft visit and a checked-in
# untriaged visit are the same job at the desk.
COUNTER_STAGE_SOURCES = {
    "intake": ("new", "intake"),
    "triage": ("triage",),
    "awaiting_cashier": ("awaiting_cashier",),
    "ready_doctor": ("ready_doctor",),
    "in_consultation": ("in_consultation",),
    "completed": ("completed",),
}

URGENT_PRIORITIES = ("urgent", "emergency")


def prefetch_worklist(appointments):
    """Warm every relation the rows need, in bulk, before serializing.

    Reading a field on a RECORDSET makes Odoo batch the compute or the SELECT
    for the whole set. Reading the same field row by row inside the serializer
    would issue one query per row. This function is the difference between the
    two, and it is why serialize_worklist_row() below never triggers a query.
    """
    if not appointments:
        return appointments

    encounters = appointments.mapped("encounter_id")
    evaluations = appointments.mapped("evaluation_ids")
    accounts = encounters.mapped("billing_account_id")

    # One batched compute each.
    appointments.mapped("front_desk_stage")
    appointments.mapped("patient_id").mapped("name")
    appointments.mapped("department_id").mapped("name")
    appointments.mapped("doctor_id").mapped("name")
    if evaluations:
        evaluations.mapped("state")
        evaluations.mapped("triage_priority")
    if encounters:
        # Single batched _compute_reception_clearance for the whole set.
        encounters.mapped("reception_clearance_ok")
        encounters.mapped("reception_outstanding_amount")
        encounters.mapped("state")
    if accounts:
        accounts.mapped("operational_funding_state")
        accounts.mapped("financial_clearance_state")
    return appointments


def _clearance(encounter):
    """Live encounter-wide clearance. Ungrouped compute_sudo fields only."""
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


def _arrived_at(appointment):
    """Best authoritative arrival timestamp already on the record.

    registered_at is stamped by hospital.reception.workflow.create_visit() and
    is the true moment the patient presented at the desk. Legacy appointments
    predate it, so appointment_date is the documented fallback rather than a
    silent None.
    """
    return datetime_value(appointment.registered_at or appointment.appointment_date)


def _permitted_actions(appointment, stage, capabilities, clearance):
    """What THIS user may do to THIS visit right now.

    Each flag is capability AND workflow position. None of them is the control:
    every one is enforced again by the model method the action would call.
    """
    is_confirmed = appointment.state == "confirmed"
    doctor = appointment.doctor_id
    user = appointment.env.user
    is_assigned_doctor = bool(
        doctor and doctor.user_id and doctor.user_id.id == user.id
    )

    return {
        # Nursing triage: allowed from intake onward and explicitly NOT gated on
        # money -- that is the whole point of this phase.
        "record_triage": bool(
            capabilities["triage"] and is_confirmed and stage in ("intake", "triage")
        ),
        "complete_triage": bool(
            capabilities["triage"] and is_confirmed and stage == "triage"
        ),
        # The desk never takes money. Reported so the UI can route the patient.
        "record_payment": bool(
            capabilities["record_payment"] and stage == "awaiting_cashier"
        ),
        "send_to_cashier": stage == "awaiting_cashier",
        # Manager/admin may override the assignment; a front desk nurse gets
        # False here whatever the stage.
        "start_consultation": bool(
            (is_assigned_doctor or capabilities["start_consultation_role"])
            and is_confirmed
            and stage == "ready_doctor"
        ),
        "emergency_bypass": bool(
            capabilities["emergency_bypass"]
            and appointment.encounter_id
            and not appointment.encounter_id.emergency_bypass
        ),
        "authorize_payer": bool(
            capabilities["payer_authorization"]
            and appointment.encounter_id
            and appointment.encounter_id.payer_type != "self_pay"
        ),
        "blocked_reason": (
            clearance.get("message")
            if stage == "awaiting_cashier"
            else None
        ),
    }


def serialize_worklist_row(appointment, capabilities):
    """One compact row. Triggers no query when prefetch_worklist() has run."""
    encounter = appointment.encounter_id
    account = encounter.billing_account_id if encounter else None
    evaluation = appointment._latest_evaluation()
    patient = appointment.patient_id
    stage = appointment.front_desk_stage
    clearance = _clearance(encounter)
    priority = evaluation.triage_priority if evaluation else None
    emergency = bool(
        (encounter and encounter.emergency_bypass)
        or appointment.visit_type == "emergency"
    )

    return {
        # Stable row identity for the client.
        "appointment_id": appointment.id,
        "appointment_code": appointment.appointment_code,
        "encounter_id": encounter.id if encounter else None,
        "patient_id": patient.id,
        "mrn": patient.identification_code,
        "patient_name": patient.name,
        "age": patient.age,
        "gender": selection_value(patient.gender),
        "arrived_at": _arrived_at(appointment),
        "department": m2o_value(appointment.department_id),
        "doctor": m2o_value(appointment.doctor_id),
        "visit_type": selection_value(appointment.visit_type),
        "appointment_state": appointment.state,
        "encounter_state": encounter.state if encounter else None,
        "evaluation_id": evaluation.id if evaluation else None,
        "triage_state": evaluation.state if evaluation else None,
        "triage_priority": selection_value(priority),
        "clearance": clearance,
        "financial_clearance_state": (
            selection_value(account.financial_clearance_state) if account else None
        ),
        "operational_funding_state": (
            selection_value(account.operational_funding_state) if account else None
        ),
        "emergency": emergency,
        "queue_stage": stage,
        # A priority indicator, NOT a competing stage.
        "urgent": bool(priority in URGENT_PRIORITIES or emergency),
        "permitted_actions": _permitted_actions(
            appointment, stage, capabilities, clearance
        ),
    }


def _evaluation_detail(evaluation):
    """The working triage record: vitals plus the nursing note.

    Bounded on purpose. Diagnoses, prescriptions, lab and radiology history are
    NOT here; they are a later phase and would turn a desk lookup into a dozen
    joins across models a front desk nurse mostly cannot read anyway.
    """
    if not evaluation:
        return None
    return {
        "id": evaluation.id,
        "name": evaluation.name,
        "state": evaluation.state,
        "evaluation_date": datetime_value(evaluation.evaluation_date),
        "started_at": datetime_value(evaluation.started_at),
        "completed_at": datetime_value(evaluation.completed_at),
        "assigned_nurse": m2o_value(evaluation.assigned_nurse_id),
        "chief_complaint": evaluation.chief_complaint or None,
        "triage_priority": selection_value(evaluation.triage_priority),
        "triage_notes": evaluation.triage_notes or None,
        "vitals": {
            "weight": float_value(evaluation.weight),
            "height": float_value(evaluation.height),
            "temperature": float_value(evaluation.temperature),
            "heart_rate": float_value(evaluation.heart_rate),
            "respiratory_rate": float_value(evaluation.respiratory_rate),
            "systolic_bp": float_value(evaluation.systolic_bp),
            "diastolic_bp": float_value(evaluation.diastolic_bp),
            # Supported by the model, range-checked (0-100 %), writable through
            # the clinical save allowlist and frozen by LOCKED_CLINICAL_FIELDS
            # once triage is done. It was simply absent here, which made SpO2
            # write-only from the front desk's point of view.
            "spo2": float_value(evaluation.spo2),
            "rbs": float_value(evaluation.rbs),
            "head_circumference": float_value(evaluation.head_circumference),
            "bmi": float_value(evaluation.bmi),
            "bmi_state": selection_value(evaluation.bmi_state),
            "pain_level": selection_value(evaluation.pain_level),
            "pain_note": evaluation.pain_note or None,
        },
    }


def serialize_front_desk_visit(appointment, capabilities):
    """The selected-visit panel. Row data plus the working triage record.

    Reuses serialize_worklist_row() rather than restating identity, clearance and
    permitted actions, so the panel and the row it was selected from cannot
    disagree about what stage the patient is at or what may be done next.
    """
    row = serialize_worklist_row(appointment, capabilities)
    encounter = appointment.encounter_id
    evaluation = appointment._latest_evaluation()

    return {
        "row": row,
        "patient": {
            "id": appointment.patient_id.id,
            "mrn": appointment.patient_id.identification_code,
            "name": appointment.patient_id.name,
            "date_of_birth": datetime_value(appointment.patient_id.date_of_birth),
            "age": appointment.patient_id.age,
            "gender": selection_value(appointment.patient_id.gender),
            "phone": appointment.patient_id.phone or None,
            "mobile": appointment.patient_id.mobile or None,
            "blood_group": selection_value(appointment.patient_id.blood_group),
        },
        "visit": {
            "appointment_id": appointment.id,
            "appointment_code": appointment.appointment_code,
            "appointment_date": datetime_value(appointment.appointment_date),
            "arrived_at": _arrived_at(appointment),
            "state": appointment.state,
            "visit_type": selection_value(appointment.visit_type),
            "reason": appointment.reason or None,
            "department": m2o_value(appointment.department_id),
            "doctor": m2o_value(appointment.doctor_id),
            "triage_destination": m2o_value(appointment.triage_destination_id),
            "reception_workflow_managed": bool(
                appointment.reception_workflow_managed
            ),
        },
        "encounter": {
            "id": encounter.id,
            "name": encounter.name,
            "state": encounter.state,
            "encounter_type": selection_value(encounter.encounter_type),
            "payer_type": selection_value(encounter.payer_type),
            "emergency_bypass": bool(encounter.emergency_bypass),
            "emergency_bypass_reason": encounter.emergency_bypass_reason or None,
        }
        if encounter
        else None,
        "evaluation": _evaluation_detail(evaluation),
        "clearance": row["clearance"],
        "permitted_actions": row["permitted_actions"],
    }


def worklist_counts(rows):
    """Counters derived from the SAME rows the client is shown.

    Computed from the serialized rows rather than by re-querying, so a counter
    and its tab can never disagree -- which is the one thing a dense worklist
    must never do.
    """
    by_stage = {key: 0 for key in STAGE_KEYS}
    for row in rows:
        by_stage[row["queue_stage"]] += 1

    counts = {
        name: sum(by_stage[key] for key in sources)
        for name, sources in COUNTER_STAGE_SOURCES.items()
    }
    counts["today"] = len(rows)
    counts["urgent"] = sum(1 for row in rows if row["urgent"])
    counts["cancelled"] = by_stage["cancelled"]
    return counts

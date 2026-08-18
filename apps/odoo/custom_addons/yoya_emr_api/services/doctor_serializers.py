"""Doctor Desk serializers: the clinician's read of a visit.

WHAT A DOCTOR IS ALLOWED TO SEE, AND WHY IT IS SAFE
---------------------------------------------------
Hospital Doctor holds read on hospital.appointment, hospital.encounter,
hospital.patient, hospital.patient.evaluation and hospital.medical.alert, and
yoya_reception_bridge / yoya_clinical_bridge scope all four to the doctor's own
patients:

    rule_appointment_doctor          doctor_id.user_id = me
    rule_encounter_doctor            primary_doctor_id.user_id = me
                                     OR appointment_id.doctor_id.user_id = me
    rule_patient_evaluation_doctor   physician_id.user_id = me
                                     OR appointment_id.doctor_id.user_id = me

Nothing here calls sudo(), and nothing here reads a field the Doctor group does
not already hold. That last rule is what makes the module safe to extend: a
field a doctor may not read would raise AccessError rather than leak.

WHAT IS ABSENT IS AS DELIBERATE AS WHAT IS PRESENT
--------------------------------------------------
No amount, balance, receipt, sponsor allocation, agreement name, membership
number or payer name appears anywhere below, and there is no code path that
could produce one. The doctor receives an operational clearance VERDICT and a
fixed operational sentence.

In particular this module NEVER reads appointment.billing_clearance_message.
That field is built by hospital.billing.engine and embeds money and, in the
fully-sponsored arm, the sponsor's display name:

    "Prepayment of 300.00 is required before service."
    "Patient responsibility of 450.00 is required before service; the sponsor
     carries 850.00."
    "Fully sponsored: 1200.00 authorized to <SPONSOR NAME>. ..."

Forwarding it -- which the interim Doctor Desk wiring did, by way of the
clinical evaluation endpoints -- puts all three on a clinician's screen. The
verdict is taken from the model's own predicate instead, and the sentence from
the allowlist below, which is a closed set of strings written here.
"""
from .api_response import (
    date_value,
    datetime_value,
    float_value,
    m2o_value,
    selection_value,
)
from .clinical_serializers import VITAL_FIELDS, evaluation_status

# Canonical stage vocabulary, imported from the model that derives it so the
# Doctor API cannot drift from the workflow. Same import the Front Desk
# serializer makes, and for the same reason.
from odoo.addons.yoya_reception_bridge.models.hospital_appointment import (
    FRONT_DESK_STAGES,
)

STAGE_KEYS = tuple(key for key, _label in FRONT_DESK_STAGES)

# THE stage that means "this patient may be seen now". Stated once, here, and
# imported by the controller and the tests. Every readiness claim on this
# surface resolves to a comparison against this constant -- never to a
# recomputation from triage state and a billing flag, which is what the interim
# adapter did and what could over-promise Ready for a patient who still owes
# money at the desk.
READY_STAGE = "ready_doctor"

# Stages whose visits belong in the Doctor's Finished / history bucket.
FINISHED_STAGES = ("in_consultation", "completed")

# Mirrors yoya_emr_api.services.front_desk_serializers.URGENT_PRIORITIES.
URGENT_PRIORITIES = ("urgent", "emergency")

# THE operational clearance vocabulary a doctor may be told, keyed by
# hospital.encounter.reception_clearance_state.
#
# A CLOSED SET OF LITERAL STRINGS, written here and nowhere else. This is the
# control that makes a money leak structurally impossible rather than merely
# absent today: there is no interpolation, no %, no format() and no value read
# from a record in any of them, so no future change to the billing engine's
# wording can reach a clinician's screen through this path.
#
# None means "nothing to report" -- the visit is not blocked and the desk has
# no message for the doctor.
DOCTOR_CLEARANCE_REASONS = {
    "not_required": None,
    "cleared": None,
    "credit_authorized": None,
    "sponsor_cleared": None,
    "emergency_bypass": None,
    "pending": (
        "Financial clearance is still pending at the front desk. The patient "
        "must be cleared before the consultation can start."
    ),
}

# Used when the encounter reports a state this module has not been taught, and
# when there is no encounter at all. Deliberately vague about the cause: an
# unrecognised state is exactly the case where guessing a specific reason would
# be wrong.
DOCTOR_CLEARANCE_FALLBACK_REASON = (
    "Financial clearance has not been confirmed for this visit."
)


def serialize_clearance(appointment):
    """The clearance VERDICT and a fixed operational sentence. Never a figure.

    The verdict is hospital.appointment._is_payment_blocking(), the model's own
    predicate -- which is the SAME input _resolve_front_desk_stage consults to
    decide awaiting_cashier vs ready_doctor. Reusing it rather than re-deriving
    one is what makes it impossible for this payload to disagree with the
    queue_stage sitting next to it in the same row.

    It reads encounter.reception_clearance_ok and encounter.emergency_bypass,
    both ungrouped (the former compute_sudo=True), and falls back to
    appointment.billing_blocked (also compute_sudo=True) when a legacy visit
    has no encounter. A doctor therefore obtains the verdict without holding
    rights on any cash field, and without this module elevating anything.

    'state' is a Selection KEY, not a sentence: one of not_required / pending /
    cleared / credit_authorized / sponsor_cleared / emergency_bypass. It
    carries no amount and names no payer, and it is passed through so the desk
    can distinguish "nothing was owed" from "the sponsor covered it" without
    being told either figure.
    """
    encounter = appointment.encounter_id
    blocked = bool(appointment._is_payment_blocking())
    state = selection_value(encounter.reception_clearance_state) if encounter else None

    if not blocked:
        reason = None
    else:
        # .get with a distinct default, NOT DOCTOR_CLEARANCE_REASONS[state]: an
        # unknown state must degrade to the safe sentence, not raise mid-queue.
        reason = DOCTOR_CLEARANCE_REASONS.get(state, DOCTOR_CLEARANCE_FALLBACK_REASON)
        if reason is None:
            # The state says cleared but the predicate says blocked. Trust the
            # predicate -- it is the one the doctor's Start button will face --
            # and say so without inventing a cause.
            reason = DOCTOR_CLEARANCE_FALLBACK_REASON

    return {"blocked": blocked, "state": state, "reason": reason}


def serialize_patient_identity(patient):
    """Identity only. Touches no count or overview compute."""
    return {
        "id": patient.id,
        "name": patient.name,
        # 'mrn' is the vocabulary the Doctor Desk and the Front Desk worklist
        # both use for hospital.patient.identification_code.
        "mrn": patient.identification_code or None,
        "age": patient.age,
        "gender": selection_value(patient.gender),
    }


def serialize_vitals(record):
    """Vitals from an evaluation, plus the derived BMI and pain readings.

    Accepts an empty recordset and answers with a full dict of Nones, so the
    client never has to branch on presence to render the grid.
    """
    if not record:
        payload = {name: None for name in VITAL_FIELDS}
        payload.update({"bmi": None, "bmi_state": None, "pain_level": None})
        return payload

    payload = {name: float_value(record[name]) for name in VITAL_FIELDS}
    payload.update(
        {
            "bmi": float_value(record.bmi),
            "bmi_state": selection_value(record.bmi_state),
            # Selection keyed by the strings '0'..'10'; kept as a string.
            "pain_level": selection_value(record.pain_level),
        }
    )
    return payload


def serialize_previous_vitals(patient):
    """Vitals from the patient's most recent COMPLETED evaluation.

    Reads the latest_* computes on hospital.patient, which resolve the newest
    state='done' evaluation in a single search, rather than searching here.
    """
    if not patient.latest_evaluation_id:
        return None
    return {
        "evaluation_id": patient.latest_evaluation_id.id,
        "weight": float_value(patient.latest_weight),
        "height": float_value(patient.latest_height),
        "temperature": float_value(patient.latest_temperature),
        "heart_rate": float_value(patient.latest_heart_rate),
        "respiratory_rate": float_value(patient.latest_respiratory_rate),
        "systolic_bp": float_value(patient.latest_systolic_bp),
        "diastolic_bp": float_value(patient.latest_diastolic_bp),
        "spo2": float_value(patient.latest_spo2),
        "rbs": float_value(patient.latest_rbs),
        "head_circumference": float_value(patient.latest_head_circumference),
        "bmi": float_value(patient.latest_bmi),
        "bmi_state": selection_value(patient.latest_bmi_state),
        "pain_level": selection_value(patient.latest_pain_level),
    }


def serialize_encounter(encounter):
    if not encounter:
        return None
    return {
        "id": encounter.id,
        "name": encounter.name,
        "state": encounter.state,
        "encounter_type": selection_value(encounter.encounter_type),
    }


def prefetch_worklist(appointments):
    """Warm every relation the rows need, in bulk, before serializing.

    Reading a field on a RECORDSET makes Odoo batch the compute or the SELECT
    for the whole set; reading it row by row inside the serializer issues one
    query per row. front_desk_stage in particular is a non-stored compute whose
    inputs traverse the encounter, so a per-row read of it is the difference
    between one query and forty.
    """
    if not appointments:
        return appointments

    encounters = appointments.mapped("encounter_id")
    evaluations = appointments.mapped("evaluation_ids")

    appointments.mapped("front_desk_stage")
    appointments.mapped("patient_id").mapped("name")
    appointments.mapped("department_id").mapped("name")
    appointments.mapped("doctor_id").mapped("name")
    if evaluations:
        evaluations.mapped("state")
        evaluations.mapped("triage_priority")
        evaluations.mapped("chief_complaint")
    if encounters:
        # One batched _compute_reception_clearance for the whole set.
        encounters.mapped("reception_clearance_ok")
        encounters.mapped("reception_clearance_state")
        encounters.mapped("emergency_bypass")
        encounters.mapped("state")
    return appointments


def _is_assigned_doctor(appointment):
    doctor = appointment.doctor_id
    user = appointment.env.user
    return bool(doctor and doctor.user_id and doctor.user_id.id == user.id)


def _may_start_consultation(appointment, stage, capabilities):
    """May THIS user start THIS consultation right now, and is it the moment.

    AFFORDANCE, NOT AUTHORIZATION. Every clause is enforced again, and
    authoritatively, by hospital.appointment.action_start_consultation():
    _assert_may_start_consultation (assignment / override group),
    _assert_triage_completed (nursing triage) and hospital_billing's override
    (financial clearance). A client cannot act on a True it was not granted.

    The stage clause is what makes it honest in the other direction: it is the
    AUTHORITATIVE front_desk_stage, so this can never offer the action for a
    patient the desk still has at the cashier.
    """
    return bool(
        (_is_assigned_doctor(appointment) or capabilities["start_consultation_role"])
        and appointment.state == "confirmed"
        and stage == READY_STAGE
    )


def serialize_queue_row(appointment, capabilities):
    """One compact row. Triggers no query once prefetch_worklist() has run."""
    encounter = appointment.encounter_id
    evaluation = appointment._latest_evaluation()
    patient = appointment.patient_id
    # THE authoritative stage, read from the model. Never recomputed here.
    stage = appointment.front_desk_stage
    priority = evaluation.triage_priority if evaluation else None
    emergency = bool(
        (encounter and encounter.emergency_bypass)
        or appointment.visit_type == "emergency"
    )

    return {
        "appointment_id": appointment.id,
        "appointment_code": appointment.appointment_code,
        "appointment_date": datetime_value(appointment.appointment_date),
        "state": appointment.state,
        "patient": serialize_patient_identity(patient),
        "department": m2o_value(appointment.department_id),
        "doctor": m2o_value(appointment.doctor_id),
        "encounter": serialize_encounter(encounter),
        # Authoritative. hospital.appointment.front_desk_stage, serialized
        # unchanged -- this is the field the whole integration turns on.
        "queue_stage": stage,
        "visit_type": selection_value(appointment.visit_type),
        "triage_status": evaluation_status(evaluation),
        "triage_priority": selection_value(priority),
        "chief_complaint": (evaluation.chief_complaint or None) if evaluation else None,
        # A priority indicator, NOT a competing stage.
        "urgent": bool(priority in URGENT_PRIORITIES or emergency),
        "clearance": serialize_clearance(appointment),
        "can_start_consultation": _may_start_consultation(
            appointment, stage, capabilities
        ),
    }


def _serialize_triage(evaluation):
    """The nursing record a doctor reads before seeing the patient.

    Bounded on purpose: diagnoses, prescriptions, lab and radiology history are
    a later phase and are not smuggled into a queue lookup.
    """
    return {
        "evaluation_id": evaluation.id if evaluation else None,
        "status": evaluation_status(evaluation),
        "priority": selection_value(evaluation.triage_priority) if evaluation else None,
        "chief_complaint": (evaluation.chief_complaint or None) if evaluation else None,
        "notes": (evaluation.triage_notes or None) if evaluation else None,
        "started_at": datetime_value(evaluation.started_at) if evaluation else None,
        "completed_at": datetime_value(evaluation.completed_at) if evaluation else None,
        "vitals": serialize_vitals(evaluation),
    }


def serialize_visit_detail(appointment, capabilities):
    """The selected-patient panel, resolved from one read.

    Reuses serialize_queue_row() for the row-level facts rather than restating
    them, so the panel and the queue row it was opened from cannot disagree
    about the stage, the clearance verdict or what may be done next.
    """
    row = serialize_queue_row(appointment, capabilities)
    patient = appointment.patient_id
    encounter = appointment.encounter_id
    evaluation = appointment._latest_evaluation()

    payload = serialize_patient_identity(patient)
    payload.update(
        {
            "date_of_birth": date_value(patient.date_of_birth),
            "blood_group": selection_value(patient.blood_group),
            "phone": patient.phone or None,
            "mobile": patient.mobile or None,
            "disease_history": patient.disease_history or None,
            "past_medical_history": patient.past_medical_history or None,
        }
    )

    return {
        "visit": {
            "appointment_id": appointment.id,
            "appointment_code": appointment.appointment_code,
            "appointment_date": datetime_value(appointment.appointment_date),
            "state": appointment.state,
            "reason": appointment.reason or None,
            "department": m2o_value(appointment.department_id),
            "doctor": m2o_value(appointment.doctor_id),
            "queue_stage": row["queue_stage"],
            "visit_type": row["visit_type"],
        },
        "patient": payload,
        # hospital.patient has NO authoritative allergy field; medical alerts
        # are the nearest structured equivalent and are named as such. The UI
        # must never present them under an Allergies heading, which would
        # assert clinical safety information the record cannot support.
        "medical_alerts": [
            {
                "id": alert.id,
                "name": alert.name,
                "severity": alert.severity or None,
            }
            for alert in patient.medical_alert_ids
        ],
        "encounter": serialize_encounter(encounter),
        "triage": _serialize_triage(evaluation),
        "previous_vitals": serialize_previous_vitals(patient),
        # Sponsorship CATEGORY only: self_pay / insurance / corporate /
        # government / ngo / other.
        #
        # SAFE UNDER NORMAL DOCTOR RIGHTS, and supplied for that reason.
        # hospital.encounter.payer_type carries no groups=, Hospital Doctor
        # holds read on hospital.encounter, and rule_encounter_doctor scopes it
        # to this doctor's own patients. No ACL was added to expose it.
        #
        # It is a bare selection key. The payer's NAME, the agreement, the
        # membership number and every figure live on hospital.payer,
        # hospital.payer.agreement and hospital.patient.payer, none of which is
        # read here or reachable from this payload.
        "payer_type": selection_value(encounter.payer_type) if encounter else None,
        "clearance": row["clearance"],
        "can_start_consultation": row["can_start_consultation"],
    }


def worklist_counts(rows):
    """Bucket counters derived from the SAME rows the client is shown.

    Computed from the serialized rows rather than by re-querying, so a counter
    and the list under it can never disagree -- the one thing a dense worklist
    must never do.

    The buckets are the Doctor Desk's, and each is defined by the AUTHORITATIVE
    stage:

        review    ready_doctor and still confirmed -- the workable set
        finished  in_consultation / completed
        wait      everything else still in the doctor's day
    """
    counts = {"all": len(rows), "wait": 0, "review": 0, "finished": 0}
    for row in rows:
        counts[bucket_of(row)] += 1
    return counts


def bucket_of(row):
    """THE bucket rule, server side, mirrored by lib/doctor-format.ts."""
    if row["queue_stage"] in FINISHED_STAGES:
        return "finished"
    if row["queue_stage"] == READY_STAGE and row["state"] == "confirmed":
        return "review"
    return "wait"


def serialize_session(env, capabilities, role_flags):
    """Who is signed in, and what the desk may offer them.

    'doctor' on the hospital.doctor search is the caller's own staff record.
    A manager or admin working the desk legitimately has none, which is why
    is_doctor is read from the GROUP rather than from the presence of that
    record.
    """
    user = env.user
    doctor = env["hospital.doctor"].search([("user_id", "=", user.id)], limit=1)
    return {
        "user": {"id": user.id, "name": user.name, "login": user.login},
        "company": m2o_value(user.company_id),
        "doctor": m2o_value(doctor),
        "is_doctor": bool(role_flags["doctor"]),
        "capabilities": capabilities,
    }

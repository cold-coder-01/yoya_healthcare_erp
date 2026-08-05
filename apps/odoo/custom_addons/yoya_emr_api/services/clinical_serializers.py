"""Serializers for the clinical evaluation workflow.

Deliberately narrow: each function serialises one clinical concept for one
screen. Nothing here reflects a model generically, and nothing touches the
patient count/overview computes (_compute_related_record_counts fires ten
search_count queries per record, _compute_patient_overview four more).
"""
from .api_response import (
    date_value,
    datetime_value,
    float_value,
    m2o_value,
    selection_value,
)

# Every vital carried on hospital.patient.evaluation.
VITAL_FIELDS = (
    "weight",
    "height",
    "temperature",
    "heart_rate",
    "respiratory_rate",
    "systolic_bp",
    "diastolic_bp",
    "spo2",
    "rbs",
    "head_circumference",
)


def evaluation_status(evaluation):
    """Derived queue status.

    The base model has only draft/done/cancelled; started_at (added by
    yoya_clinical_bridge) is what separates a claimed evaluation from a
    queued one.
    """
    if not evaluation:
        return "not_started"
    if evaluation.state == "done":
        return "completed"
    if evaluation.state == "cancelled":
        return "cancelled"
    if evaluation.state == "draft":
        return "in_progress" if evaluation.started_at else "waiting"
    return evaluation.state


def serialize_vitals(evaluation):
    return {name: float_value(evaluation[name]) for name in VITAL_FIELDS}


def serialize_evaluation(evaluation):
    if not evaluation:
        return None
    payload = {
        "id": evaluation.id,
        "name": evaluation.name,
        "state": evaluation.state,
        "status": evaluation_status(evaluation),
        "patient_id": m2o_value(evaluation.patient_id),
        "appointment_id": m2o_value(evaluation.appointment_id),
        "physician_id": m2o_value(evaluation.physician_id),
        "encounter_id": m2o_value(evaluation.encounter_id),
        "assigned_nurse_id": m2o_value(evaluation.assigned_nurse_id),
        "triage_priority": selection_value(evaluation.triage_priority),
        "chief_complaint": evaluation.chief_complaint or None,
        "triage_notes": evaluation.triage_notes or None,
        # Selection keyed by strings '0'..'10'; kept as a string on the wire.
        "pain_level": selection_value(evaluation.pain_level),
        "pain_note": evaluation.pain_note or None,
        "bmi": float_value(evaluation.bmi),
        "bmi_state": selection_value(evaluation.bmi_state),
        "evaluation_date": datetime_value(evaluation.evaluation_date),
        "started_at": datetime_value(evaluation.started_at),
        "completed_at": datetime_value(evaluation.completed_at),
    }
    payload.update(serialize_vitals(evaluation))
    return payload


def serialize_patient_identity(patient):
    """Identity only. No count or overview computes are touched."""
    return {
        "id": patient.id,
        "name": patient.name,
        "identification_code": patient.identification_code,
        "age": patient.age,
        "gender": selection_value(patient.gender),
        "date_of_birth": date_value(patient.date_of_birth),
    }


def serialize_patient_detail(patient):
    payload = serialize_patient_identity(patient)
    payload.update(
        {
            "phone": patient.phone or None,
            "mobile": patient.mobile or None,
            "blood_group": selection_value(patient.blood_group),
            "disease_history": patient.disease_history or None,
            "past_medical_history": patient.past_medical_history or None,
            # hospital.patient has no allergy field; medical alerts are the
            # nearest structured equivalent in this schema.
            "medical_alerts": [
                {
                    "id": alert.id,
                    "name": alert.name,
                    "severity": alert.severity,
                }
                for alert in patient.medical_alert_ids
            ],
        }
    )
    return payload


def serialize_previous_vitals(patient):
    """Vitals from the patient's most recent COMPLETED evaluation.

    Reads the latest_* computes on hospital.patient, which resolve the newest
    state='done' evaluation in one search.
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


def serialize_billing_clearance(appointment):
    """Read the hospital_billing computes.

    Both are compute_sudo on hospital_billing's side: a clinician may trigger
    the check without holding read rights on the cash fields it inspects.
    Nothing here elevates privileges.
    """
    return {
        "billing_blocked": bool(appointment.billing_blocked),
        "billing_clearance_message": appointment.billing_clearance_message or None,
    }


def serialize_appointment_core(appointment):
    return {
        "id": appointment.id,
        "appointment_code": appointment.appointment_code,
        "appointment_date": datetime_value(appointment.appointment_date),
        "state": appointment.state,
        "reason": appointment.reason or None,
        "doctor_id": m2o_value(appointment.doctor_id),
        "department_id": m2o_value(appointment.department_id),
    }


def serialize_queue_row(appointment, evaluation):
    row = serialize_appointment_core(appointment)
    row.update(
        {
            "patient": serialize_patient_identity(appointment.patient_id),
            "encounter": serialize_encounter(appointment.encounter_id),
            "evaluation": {
                "id": evaluation.id if evaluation else None,
                "state": evaluation.state if evaluation else None,
                "status": evaluation_status(evaluation),
                "triage_priority": (
                    selection_value(evaluation.triage_priority) if evaluation else None
                ),
                "assigned_nurse_id": (
                    m2o_value(evaluation.assigned_nurse_id) if evaluation else None
                ),
                "started_at": datetime_value(evaluation.started_at) if evaluation else None,
                "completed_at": (
                    datetime_value(evaluation.completed_at) if evaluation else None
                ),
            },
        }
    )
    row.update(serialize_billing_clearance(appointment))
    return row


def serialize_session(env, groups):
    user = env.user
    doctor = env["hospital.doctor"].search([("user_id", "=", user.id)], limit=1)
    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "login": user.login,
        },
        "company": m2o_value(user.company_id),
        "groups": groups,
        "doctor": m2o_value(doctor),
        "permitted_departments": [
            m2o_value(department) for department in user.yoya_permitted_department_ids
        ],
    }
